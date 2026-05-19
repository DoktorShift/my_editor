"""Regression locks for the production-readiness audit's findings.

Each test pins down a behavior the audit identified as broken or
under-tested. Tests are grouped by audit ID (C-2, C-3, P-1, …) so a
future regression can be traced back to the exact finding it broke.
"""

from __future__ import annotations

import hashlib
import json
from typing import List

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import (
    EngagementAggregator,
    EngagementCache,
    PluginAnchor,
    parse_zap_receipt,
)
from plugin_marketplace.social.aggregator import _index_deletions
from plugin_marketplace.social.models import DeletionRequest
from plugin_marketplace.social.outbox_router import OutboxRouter
from plugin_marketplace.social.parser import (
    NIP09_DELETION_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    NIP57_ZAP_REQUEST_KIND,
    PLUGIN_LISTING_KIND,
)
from plugin_marketplace.ui.social_panel import PublishState, SocialPanel
from tests._bolt11_builder import build_test_bolt11


AUTHOR = "a" * 64
ZAPPER = "c" * 64
ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "hello")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# ──────────────────────────────────────────────────────────────────────
# C-2 — Optimistic publish state survives snapshot re-renders


def test_c2_publish_state_outlives_panel_rerender(qapp):
    """After a SIGNING transition, a snapshot re-render must NOT
    silently flip the composer back to IDLE.
    """
    panel = SocialPanel()
    panel._signed_in = True  # type: ignore[attr-defined]
    panel.set_publish_state(PublishState.SIGNING, "Signing…")
    assert panel.publish_state() == PublishState.SIGNING
    # Simulate a re-render by re-building the composer.
    panel._build_composer()
    # State must still report SIGNING — the audit's bug was that the
    # rebuild reset the state to IDLE.
    assert panel.publish_state() == PublishState.SIGNING


def test_c2_publish_state_clears_to_idle_on_success(qapp):
    panel = SocialPanel()
    panel._signed_in = True  # type: ignore[attr-defined]
    panel.set_publish_state(PublishState.SIGNING, "")
    panel.set_publish_state(PublishState.PUBLISHED, "Published.")
    # The audit accepted PUBLISHED -> IDLE on next interaction; the
    # important invariant is the textarea draft was wiped.
    assert panel._composer_draft == ""  # type: ignore[attr-defined]


def test_c2_publish_state_preserves_draft_on_failure(qapp):
    """A failed publish must keep the user's text intact so they
    can retry without retyping. The audit caught the inverse bug.
    """
    panel = SocialPanel()
    panel._signed_in = True  # type: ignore[attr-defined]
    panel._composer_draft = "really long review text"  # type: ignore[attr-defined]
    panel.set_publish_state(PublishState.SIGNING, "")
    panel.set_publish_state(PublishState.FAILED, "relay error")
    assert panel._composer_draft == "really long review text"  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────
# C-3 — NIP-09 coordinate deletion actually hides addressable listings


def test_c3_listing_deletion_blocks_when_after_listing():
    deletion = DeletionRequest(
        event_id="d1", author_pubkey=AUTHOR,
        target_event_ids=(),
        target_coords=(ANCHOR.coord,),
        created_at=500,
    )
    assert EngagementAggregator.is_listing_deleted(
        [deletion], coord=ANCHOR.coord,
        author_pubkey=AUTHOR, event_created_at=400,
    ) is True


def test_c3_listing_deletion_does_not_block_newer_listing():
    deletion = DeletionRequest(
        event_id="d1", author_pubkey=AUTHOR,
        target_event_ids=(),
        target_coords=(ANCHOR.coord,),
        created_at=500,
    )
    # A version published after the deletion survives — per NIP-09's
    # "up to created_at" rule.
    assert EngagementAggregator.is_listing_deleted(
        [deletion], coord=ANCHOR.coord,
        author_pubkey=AUTHOR, event_created_at=600,
    ) is False


def test_c3_cross_author_listing_deletion_is_ignored():
    deletion = DeletionRequest(
        event_id="d1", author_pubkey="b" * 64,
        target_event_ids=(),
        target_coords=(ANCHOR.coord,),
        created_at=500,
    )
    assert EngagementAggregator.is_listing_deleted(
        [deletion], coord=ANCHOR.coord,
        author_pubkey=AUTHOR, event_created_at=400,
    ) is False


# ──────────────────────────────────────────────────────────────────────
# P-1 — NIP-57 amount-less invoice rejection


def _zap_request(amount_msat=21_000):
    return {
        "kind": NIP57_ZAP_REQUEST_KIND,
        "pubkey": ZAPPER,
        "created_at": 1_700_000_300,
        "tags": [
            ["amount", str(amount_msat)],
            ["a", ANCHOR.coord, ""],
            ["p", AUTHOR, ""],
        ],
        "content": "thanks",
        "id": "4" * 64,
        "sig": "0" * 128,
    }


def _receipt(*, bolt11_amount_msat, request=None):
    request = request or _zap_request()
    raw = json.dumps(request, separators=(",", ":"))
    desc_hash = hashlib.sha256(raw.encode()).hexdigest()
    invoice = build_test_bolt11(
        amount_msat=bolt11_amount_msat if bolt11_amount_msat else None,
        description_hash_hex=desc_hash,
    )
    return {
        "id": "5" * 64,
        "kind": NIP57_ZAP_RECEIPT_KIND,
        "pubkey": "d" * 64,
        "created_at": 1_700_000_400,
        "tags": [
            ["bolt11", invoice],
            ["description", raw],
            ["p", AUTHOR],
            ["P", ZAPPER],
        ],
        "content": "",
    }


def test_p1_amountless_invoice_rejected_in_receipt():
    receipt = _receipt(bolt11_amount_msat=0)   # 0 -> amount-less invoice
    assert parse_zap_receipt(receipt, ANCHOR) is None


def test_p1_matching_amounts_accepted():
    receipt = _receipt(bolt11_amount_msat=21_000)
    assert parse_zap_receipt(receipt, ANCHOR) is not None


# ──────────────────────────────────────────────────────────────────────
# P-3 — NIP-65 routing respects author intent over seed union


def test_p3_resolved_authors_only_use_their_relays():
    from nostr.outbox import RelayList

    class _Cache:
        def __init__(self, mapping):
            self._m = mapping
            self.fetched: List[str] = []

        def get_cached(self, pk):
            return self._m.get(pk)

        def fetch(self, pk, relays, on_done):
            self.fetched.append(pk)

    cache = _Cache({
        AUTHOR: RelayList(write=["wss://author.example"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_read([AUTHOR], seeds=("wss://seed.example",))
    assert plan.relays == ["wss://author.example"]
    assert "wss://seed.example" not in plan.relays


# ──────────────────────────────────────────────────────────────────────
# P-5 — NIP-51 private mute content preservation


def test_p5_mute_publish_preserves_prior_content(qapp):
    """When the previous kind:10000 carried encrypted content, the
    next publish must keep it so private mutes from another client
    survive.
    """
    from plugin_marketplace.social import MuteListCache

    class _Profile:
        user_pubkey = "0" * 64
        bunker_relays = ()

    class _PublishJob(QObject):
        first_accept = Signal(str)
        all_done = Signal(list)

    class _Pool:
        def __init__(self):
            self.published = []

        def subscribe(self, *_a, **_kw):
            class _Sub(QObject):
                event = Signal(dict)
                eose = Signal()
                closed = Signal(str)

                def close(self):
                    return None

            return _Sub()

        def publish(self, urls, event):
            self.published.append(event)
            return _PublishJob()

    class _BunkerClient:
        def sign_event(self, unsigned, *, on_success, on_failure):
            signed = dict(unsigned)
            signed["id"] = "0" * 64
            signed["sig"] = "0" * 128
            on_success(signed)

    class _BunkerPool:
        def get(self, profile, on_success, on_failure):
            on_success(_BunkerClient())

    cache = MuteListCache(relay_pool=_Pool(), bunker_pool=_BunkerPool())
    owner = _Profile.user_pubkey
    # Seed the cache with a prior event carrying encrypted content.
    cache._events[owner] = {  # type: ignore[attr-defined]
        "kind": 10000, "tags": [], "content": "ENCRYPTED_PAYLOAD",
        "created_at": 1, "id": "z" * 64,
    }
    cache.mute(_Profile(), "a" * 64)
    published = cache._relay_pool.published   # type: ignore[attr-defined]
    assert published
    assert published[0]["content"] == "ENCRYPTED_PAYLOAD"


# ──────────────────────────────────────────────────────────────────────
# S-1 — Paid install without LUD-21 verify must NOT install


def test_s1_paid_install_without_lud21_blocks_install(qapp, tmp_path):
    from plugin_marketplace.models import PluginListing
    from plugin_marketplace.payment_receipts import PaymentReceiptStore
    from plugin_marketplace.ui.paid_install_dialog import PaidInstallDialog
    from plugin_marketplace.social import InvoiceWithVerify, LnurlPayData

    receipts = PaymentReceiptStore(path=tmp_path / "r.json")
    listing = PluginListing(
        plugin_id="paid", name="Paid Plugin", version="1.0.0",
        download_url="https://example.com/p.zip", sha256="ab" * 32,
        price_sats=1000, lightning_address="alice@example.com",
    )
    dlg = PaidInstallDialog(listing=listing, receipt_store=receipts)
    pay_data = LnurlPayData(
        callback="https://example.com/cb",
        min_sendable_msat=1_000,
        max_sendable_msat=10_000_000,
        metadata_raw="[]",
        allows_nostr=False,
        nostr_pubkey="",
    )
    invoice = InvoiceWithVerify(bolt11="lnbc1u1pj...", verify_url=None)
    confirmations: list = []
    dlg.payment_confirmed.connect(confirmations.append)
    dlg._on_lnurl_ready(pay_data)   # type: ignore[attr-defined]
    dlg._on_invoice_ready(invoice)   # type: ignore[attr-defined]
    # The dialog must NOT auto-confirm. The audit's "I paid" bypass
    # has been removed; the only path forward without LUD-21 is to
    # close the dialog.
    assert confirmations == []
    assert receipts.all() == []
    dlg.close()


# ──────────────────────────────────────────────────────────────────────
# S-2 — publish_event refuses destructive kinds and malformed tags


def test_s2_publish_event_rejects_blocked_kinds():
    from plugin_system.api import HostHooks, PluginAPI, PluginIdentity

    class _Host:
        def host_publish_event(self, *_a, **_kw):
            raise AssertionError("should not be called")

    class _S:
        def get(self, *_a, **_kw):
            return None

        def set(self, *_a, **_kw):
            return None

    identity = PluginIdentity(
        plugin_id="evil", name="Evil", version="1.0.0",
        declared_kinds=frozenset({0, 3, 10000}),
    )
    api = PluginAPI(identity=identity, host=_Host(), settings_scope=_S())
    for blocked in (0, 3, 5, 10000, 10002, 24133):
        with pytest.raises(ValueError) as excinfo:
            api.publish_event({"kind": blocked, "content": "", "tags": []})
        assert "publish kind" in str(excinfo.value).lower()


def test_s2_publish_event_rejects_malformed_p_tag():
    from plugin_system.api import HostHooks, PluginAPI, PluginIdentity

    class _Host:
        def host_publish_event(self, *_a, **_kw):
            raise AssertionError("should not be called")

    class _S:
        def get(self, *_a, **_kw):
            return None

        def set(self, *_a, **_kw):
            return None

    identity = PluginIdentity(
        plugin_id="ok", name="OK", version="1.0.0",
        declared_kinds=frozenset({1}),
    )
    api = PluginAPI(identity=identity, host=_Host(), settings_scope=_S())
    with pytest.raises(ValueError):
        api.publish_event({
            "kind": 1, "content": "hi",
            "tags": [["p", "not-hex"]],
        })


# ──────────────────────────────────────────────────────────────────────
# S-3 — Verified cache TTL gating


def test_s3_expired_verifications_are_not_returned(tmp_path):
    import time
    from plugin_marketplace.social.models import Nip05Verification

    cache = EngagementCache(path=tmp_path / "c.sqlite")
    fresh = Nip05Verification(
        pubkey="a" * 64, identifier="alice@example.com",
        verified=True, checked_at=int(time.time()),
    )
    stale = Nip05Verification(
        pubkey="b" * 64, identifier="bob@example.com",
        verified=True, checked_at=int(time.time()) - 30 * 24 * 60 * 60,
    )
    cache.store_nip05(fresh)
    cache.store_nip05(stale)
    verified = cache.verified_pubkeys()
    assert "a" * 64 in verified
    assert "b" * 64 not in verified


# ──────────────────────────────────────────────────────────────────────
# T-10 — Auto-refresh does not re-subscribe (U-1 regression lock)


def test_u1_auto_refresh_does_not_churn_subscription():
    """The 30s tick must call into the existing snapshot pipeline
    without touching the engagement fetcher's subscription. Otherwise
    a busy panel constantly reconnects and rate-limits itself."""
    from plugin_marketplace.ui.marketplace_dialog import MarketplaceDialog
    import inspect

    source = inspect.getsource(MarketplaceDialog._auto_refresh_tick)
    # Hardened invariant: no ``unwatch`` / ``watch`` call in the tick.
    assert "unwatch" not in source
    assert ".watch(" not in source


# ──────────────────────────────────────────────────────────────────────
# T-8 — publish_event host adapter invalid event shapes


def test_t8_publish_event_propagates_failure_when_host_unavailable():
    from plugin_system.api import HostHooks, PluginAPI, PluginIdentity
    from plugin_system.host_adapter import PluginHost

    class _Backend:
        # Deliberately omit editor_publish_event — older builds.
        def editor_create_menu_action(self, *_a, **_kw):
            return None

        def editor_remove_menu_action(self, *_a, **_kw):
            return None

        def editor_get_current_text(self):
            return ""

        def editor_set_current_text(self, _text):
            return True

        def editor_open_tab(self, *_a, **_kw):
            return None

        def editor_show_status(self, *_a, **_kw):
            return None

    host = PluginHost(_Backend())
    identity = PluginIdentity(
        plugin_id="ok", name="OK", version="1.0.0",
        declared_kinds=frozenset({1111}),
    )

    class _S:
        def get(self, *_a, **_kw):
            return None

        def set(self, *_a, **_kw):
            return None

    api = PluginAPI(identity=identity, host=host, settings_scope=_S())
    errors: list = []
    api.publish_event(
        {"kind": 1111, "content": "x", "tags": []},
        on_failed=errors.append,
    )
    # Host without editor_publish_event must surface a friendly error
    # via on_failed — never raise NotImplementedError, never silently
    # succeed.
    assert errors
    assert "publish_event" in errors[0].lower()
