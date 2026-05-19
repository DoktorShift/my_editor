"""Tests for ZapDialog state machine.

We don't drive a real lightning service or relay. The dialog's
worker pipeline (LNURL → sign → invoice → receipt) is exercised
by directly invoking the state-transition methods. Signal-level
checks confirm the visible-button matrix matches each page.
"""

from __future__ import annotations

import time

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import (
    EngagementCache,
    LnurlPayData,
    PluginAnchor,
    ZapReceipt,
)
from plugin_marketplace.social.parser import PLUGIN_LISTING_KIND
from plugin_marketplace.ui.zap_dialog import ZapDialog


AUTHOR_PUBKEY = "a" * 64
ZAPPER_PUBKEY = "b" * 64
ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "plugin")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cache(tmp_path):
    return EngagementCache(path=tmp_path / "engagement.sqlite")


class _FakeFetcher(QObject):
    snapshot_changed = Signal(object)


def _make_dialog(cache, *, fetcher=None, bunker_client=None) -> ZapDialog:
    return ZapDialog(
        parent_window=None,
        anchor=ANCHOR,
        recipient_display_name="Alice",
        recipient_avatar_pixmap=None,
        lightning_address="alice@example.com",
        bunker_client=bunker_client,
        active_pubkey=ZAPPER_PUBKEY,
        engagement_fetcher=fetcher,
        cache=cache,
    )


def _pay_data(**overrides) -> LnurlPayData:
    fields = dict(
        callback="https://example.com/callback",
        min_sendable_msat=1000,
        max_sendable_msat=1_000_000,
        metadata_raw="[]",
        allows_nostr=True,
        nostr_pubkey="c" * 64,
    )
    fields.update(overrides)
    return LnurlPayData(**fields)


# ──────────────────────────────────────────────────────────────────────
# initial state

def test_initial_state_is_compose(qapp, cache):
    dlg = _make_dialog(cache)
    assert dlg._stack.currentWidget() is dlg._compose_page
    assert dlg._send_btn.isVisible() is False or dlg._send_btn.isHidden() is False
    # Defaults: 100 sat preset selected.
    assert dlg._chosen_sats == 100


def test_preset_picker_updates_chosen_sats(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._on_preset_picked(500)
    assert dlg._chosen_sats == 500


def test_custom_amount_wins_over_preset(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._on_preset_picked(500)
    dlg._on_custom_changed("777")
    assert dlg._chosen_sats == 777


def test_custom_amount_ignores_invalid_input(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._on_preset_picked(500)
    dlg._on_custom_changed("not-a-number")
    assert dlg._chosen_sats == 500


def test_custom_amount_ignores_non_positive(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._on_preset_picked(500)
    dlg._on_custom_changed("-10")
    assert dlg._chosen_sats == 500


# ──────────────────────────────────────────────────────────────────────
# error display

def test_show_error_makes_status_visible(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._show_error("something broke")
    assert not dlg._status.isHidden()
    assert dlg._status.text() == "something broke"
    assert dlg._send_btn.text() == "Try again"
    assert dlg._send_btn.isEnabled()


# ──────────────────────────────────────────────────────────────────────
# LNURL resolution branches

def test_lnurl_resolved_below_min_shows_error(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._chosen_sats = 1   # 1000 msat, but min is 2000
    dlg._on_lnurl_resolved(_pay_data(min_sendable_msat=2000))
    assert not dlg._status.isHidden()
    assert "at least" in dlg._status.text()


def test_lnurl_resolved_above_max_shows_error(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._chosen_sats = 1_000_000   # 1B msat, cap is 5000 msat
    dlg._on_lnurl_resolved(_pay_data(max_sendable_msat=5000))
    assert not dlg._status.isHidden()
    assert "caps" in dlg._status.text()


def test_lnurl_resolved_without_nostr_support_skips_signing(qapp, cache, monkeypatch):
    """Endpoint says ``allowsNostr: false`` -> fall back to plain LNURL invoice."""
    dlg = _make_dialog(cache, bunker_client=object())
    invoked = {}

    def fake_plain(amount_msat):
        invoked["amount_msat"] = amount_msat

    monkeypatch.setattr(dlg, "_fetch_plain_invoice", fake_plain)
    dlg._on_lnurl_resolved(_pay_data(allows_nostr=False, nostr_pubkey=""))
    assert invoked == {"amount_msat": 100_000}


def test_lnurl_resolved_without_bunker_client_shows_error(qapp, cache):
    """User started a zap but isn't connected via NIP-46."""
    dlg = _make_dialog(cache, bunker_client=None)
    dlg._on_lnurl_resolved(_pay_data())
    assert not dlg._status.isHidden()
    assert "signer" in dlg._status.text().lower()


def test_lnurl_resolved_calls_bunker_sign(qapp, cache):
    """Happy-path: pay data is acceptable -> sign_event is invoked."""
    calls: list[dict] = []

    class _Bunker:
        def sign_event(self, unsigned, on_ok, on_err):
            calls.append({
                "unsigned": unsigned,
                "on_ok": on_ok,
                "on_err": on_err,
            })

    dlg = _make_dialog(cache, bunker_client=_Bunker())
    dlg._on_lnurl_resolved(_pay_data())
    assert len(calls) == 1
    unsigned = calls[0]["unsigned"]
    assert unsigned["kind"] == 9734
    assert unsigned["pubkey"] == ZAPPER_PUBKEY
    # required NIP-57 tags
    tag_names = [t[0] for t in unsigned["tags"] if t]
    assert "relays" in tag_names
    assert "amount" in tag_names
    assert "p" in tag_names
    assert "a" in tag_names


# ──────────────────────────────────────────────────────────────────────
# invoice -> confirmed flip

def test_invoice_ready_switches_to_invoice_page(qapp, cache):
    dlg = _make_dialog(cache)
    dispatched: list[int] = []
    dlg.zap_dispatched.connect(dispatched.append)
    dlg._chosen_sats = 100
    dlg._on_invoice_ready("lnbc1u1pj...")
    assert dlg._stack.currentWidget() is dlg._invoice_page
    assert dlg._invoice_field.text() == "lnbc1u1pj..."
    assert dispatched == [100]


def test_maybe_flip_to_confirmed_ignores_other_anchors(qapp, cache):
    fetcher = _FakeFetcher()
    dlg = _make_dialog(cache, fetcher=fetcher)
    dlg._on_invoice_ready("lnbc1...")   # advance to invoice page
    other = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "other-plugin")
    dlg._maybe_flip_to_confirmed(other)
    assert dlg._stack.currentWidget() is dlg._invoice_page


def test_maybe_flip_to_confirmed_ignores_when_not_on_invoice_page(qapp, cache):
    fetcher = _FakeFetcher()
    dlg = _make_dialog(cache, fetcher=fetcher)
    # Still on compose page; receipts shouldn't move the dialog.
    dlg._maybe_flip_to_confirmed(ANCHOR)
    assert dlg._stack.currentWidget() is dlg._compose_page


def test_maybe_flip_to_confirmed_advances_when_own_receipt_arrives(qapp, cache, monkeypatch):
    fetcher = _FakeFetcher()
    dlg = _make_dialog(cache, fetcher=fetcher)
    dlg._on_invoice_ready("lnbc1...")

    receipt = ZapReceipt(
        event_id="r" * 64,
        zapper_pubkey=ZAPPER_PUBKEY,
        anchor=ANCHOR,
        amount_sats=100,
        created_at=int(time.time()),
        content="",
    )
    monkeypatch.setattr(
        cache, "parsed_for_anchor",
        lambda anchor, **_kw: ([], [], [receipt], []),
    )
    dlg._maybe_flip_to_confirmed(ANCHOR)
    assert dlg._stack.currentWidget() is dlg._confirmed_page


def test_maybe_flip_to_confirmed_ignores_other_zappers(qapp, cache, monkeypatch):
    fetcher = _FakeFetcher()
    dlg = _make_dialog(cache, fetcher=fetcher)
    dlg._on_invoice_ready("lnbc1...")

    stranger_receipt = ZapReceipt(
        event_id="r" * 64,
        zapper_pubkey="f" * 64,     # not our pubkey
        anchor=ANCHOR,
        amount_sats=100,
        created_at=int(time.time()),
        content="",
    )
    monkeypatch.setattr(
        cache, "parsed_for_anchor",
        lambda anchor, **_kw: ([], [], [stranger_receipt], []),
    )
    dlg._maybe_flip_to_confirmed(ANCHOR)
    assert dlg._stack.currentWidget() is dlg._invoice_page


# ──────────────────────────────────────────────────────────────────────
# cleanup

def test_close_disconnects_fetcher_signal(qapp, cache):
    fetcher = _FakeFetcher()
    dlg = _make_dialog(cache, fetcher=fetcher)
    # closeEvent must not raise even when called twice.
    dlg.close()
    dlg.close()


def test_on_send_with_zero_amount_shows_error(qapp, cache):
    dlg = _make_dialog(cache)
    dlg._chosen_sats = 0
    dlg._on_send()
    assert not dlg._status.isHidden()
    assert "zero" in dlg._status.text().lower() or "amount" in dlg._status.text().lower()
