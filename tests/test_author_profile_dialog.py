"""Smoke + structural tests for ``AuthorProfileDialog``.

We construct the dialog without a real relay pool. The fetcher is
mocked so the dialog never tries to open a socket. The tests cover
the moving parts that are easy to break and hard to notice:

  * the dialog renders without raising on a minimal viewer state
  * the zap button is present but disabled (the wallet milestone is
    explicitly deferred — see the inline note in the dialog source)
  * the reviews list switches from loading -> populated when the
    fetcher emits a ready signal
  * the action column reflects the viewer's capabilities (no follow
    button when ``can_follow`` is False, no mute button for anon)
"""

from __future__ import annotations

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton

from plugin_marketplace.social import (
    AuthorReview,
    AuthorReviewSet,
    PLUGIN_LISTING_KIND,
    PluginAnchor,
)
from plugin_marketplace.ui.author_profile_dialog import (
    AuthorProfileDialog,
    _AuthorReviewRow,
)


AUTHOR_PUBKEY = "a" * 64
PLUGIN = PluginAnchor(
    kind=PLUGIN_LISTING_KIND, author_pubkey="b" * 64, plugin_id="plugin_a",
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubFetcher(QObject):
    """Minimal fetcher whose ``request`` records the call.

    The real fetcher emits ``ready`` after the relay subscription
    closes; in the tests we call ``simulate_ready`` directly so the
    dialog's render path is exercised synchronously.
    """

    ready = Signal(object)
    failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[str] = []

    def request(self, pubkey: str) -> None:
        self.requests.append(pubkey)

    def simulate_ready(self, review_set: AuthorReviewSet) -> None:
        self.ready.emit(review_set)


def _build_dialog(qapp, **overrides):
    fetcher = overrides.pop("fetcher", None) or _StubFetcher()
    listing_resolver = overrides.pop("listing_resolver", lambda anchor: None)
    kwargs = dict(
        author_pubkey=AUTHOR_PUBKEY,
        display_name="Alice",
        nip05_identifier="alice@example.com",
        nip05_verified=True,
        avatar_pixmap=None,
        reviews_fetcher=fetcher,
        listing_resolver=listing_resolver,
        is_self=False,
        is_signed_in=True,
        is_muted=False,
        is_followed=False,
        can_follow=False,
    )
    kwargs.update(overrides)
    return AuthorProfileDialog(**kwargs), fetcher


# ──────────────────────────────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────────────────────────────

def test_construction_does_not_raise(qapp):
    dlg, fetcher = _build_dialog(qapp)
    assert dlg.windowTitle().startswith("Alice")
    # The dialog should have asked the fetcher for our pubkey exactly once.
    assert fetcher.requests == [AUTHOR_PUBKEY]


def test_zap_button_is_disabled(qapp):
    """The zap button is rendered but disabled until the wallet
    milestone wires it up — keeps it discoverable as a future feature
    without misleading the user about current capability."""
    dlg, _ = _build_dialog(qapp)
    buttons = dlg.findChildren(QPushButton)
    zap_buttons = [b for b in buttons if "Zap" in b.text()]
    assert zap_buttons, "Expected a Zap button in the dialog"
    assert all(not b.isEnabled() for b in zap_buttons)


def test_view_on_nostr_button_present(qapp):
    dlg, _ = _build_dialog(qapp)
    buttons = dlg.findChildren(QPushButton)
    assert any("View on Nostr" in b.text() for b in buttons)


# ──────────────────────────────────────────────────────────────────────
# Action column reflects capability
# ──────────────────────────────────────────────────────────────────────

def test_follow_button_hidden_when_publisher_missing(qapp):
    dlg, _ = _build_dialog(qapp, can_follow=False)
    buttons = dlg.findChildren(QPushButton)
    assert not any("Follow" in b.text() or "Unfollow" in b.text() for b in buttons)


def test_follow_button_visible_when_allowed(qapp):
    dlg, _ = _build_dialog(qapp, can_follow=True, is_signed_in=True, is_self=False)
    buttons = dlg.findChildren(QPushButton)
    assert any(b.text() == "Follow" for b in buttons)


def test_mute_button_hidden_for_self(qapp):
    dlg, _ = _build_dialog(qapp, is_self=True)
    buttons = dlg.findChildren(QPushButton)
    assert not any(b.text() in ("Mute", "Unmute") for b in buttons)


def test_mute_button_hidden_for_anonymous_viewer(qapp):
    dlg, _ = _build_dialog(qapp, is_signed_in=False)
    buttons = dlg.findChildren(QPushButton)
    assert not any(b.text() in ("Mute", "Unmute") for b in buttons)


def test_unmute_label_shown_when_already_muted(qapp):
    dlg, _ = _build_dialog(qapp, is_muted=True)
    buttons = dlg.findChildren(QPushButton)
    assert any(b.text() == "Unmute" for b in buttons)


# ──────────────────────────────────────────────────────────────────────
# Reviews list lifecycle
# ──────────────────────────────────────────────────────────────────────

def test_initially_shows_loading_state(qapp):
    dlg, _ = _build_dialog(qapp)
    assert dlg._stats_label.text() == "Loading reviews..."


def test_renders_zero_state_when_no_reviews(qapp):
    dlg, fetcher = _build_dialog(qapp)
    fetcher.simulate_ready(AuthorReviewSet(
        author_pubkey=AUTHOR_PUBKEY, reviews=(), fetched_at=0,
    ))
    assert "No reviews" in dlg._stats_label.text()


def test_populates_reviews_when_fetcher_returns(qapp):
    dlg, fetcher = _build_dialog(qapp)
    fetcher.simulate_ready(AuthorReviewSet(
        author_pubkey=AUTHOR_PUBKEY,
        reviews=(
            AuthorReview(anchor=PLUGIN, stars=5, comment="great", created_at=100),
            AuthorReview(anchor=PLUGIN, stars=4, comment="also great", created_at=50),
        ),
        fetched_at=0,
    ))
    rows = dlg.findChildren(_AuthorReviewRow)
    assert len(rows) == 2
    assert "2 reviews" in dlg._stats_label.text()
    assert "average 4.5" in dlg._stats_label.text()


def test_review_for_other_author_is_ignored(qapp):
    dlg, fetcher = _build_dialog(qapp)
    fetcher.simulate_ready(AuthorReviewSet(
        author_pubkey="z" * 64,   # not us
        reviews=(AuthorReview(anchor=PLUGIN, stars=5, comment="x", created_at=1),),
        fetched_at=0,
    ))
    assert dlg.findChildren(_AuthorReviewRow) == []


def test_failure_path_shows_error_panel(qapp):
    dlg, fetcher = _build_dialog(qapp)
    fetcher.failed.emit(AUTHOR_PUBKEY, "no relays available")
    assert "unavailable" in dlg._stats_label.text().lower()


# ──────────────────────────────────────────────────────────────────────
# Plugin-open routing
# ──────────────────────────────────────────────────────────────────────

def test_plugin_open_signal_carries_anchor_for_unknown_plugin(qapp):
    dlg, fetcher = _build_dialog(qapp, listing_resolver=lambda a: None)
    fetcher.simulate_ready(AuthorReviewSet(
        author_pubkey=AUTHOR_PUBKEY,
        reviews=(AuthorReview(anchor=PLUGIN, stars=5, comment="x", created_at=1),),
        fetched_at=0,
    ))
    rows = dlg.findChildren(_AuthorReviewRow)
    assert len(rows) == 1
    received: list[object] = []
    dlg.plugin_open_requested.connect(received.append)
    rows[0]._on_open(PLUGIN)
    assert received == [PLUGIN]
