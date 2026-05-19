"""Active-profile NIP-51 mute-list cache + publisher.

The user can mute a contributor right from the social panel. To make
that meaningful across devices it has to land on relays as an updated
``kind:10000`` event. This module owns:

  - the in-memory mute set per pubkey-owner (so the social panel can
    render a "MUTED" badge without re-reading the cache per row),
  - the read path that fetches the latest kind:10000 on profile-
    connect and watches for further updates,
  - the write path that constructs the updated mute list, signs it
    through the NIP-46 bunker, and publishes it via the marketplace's
    outbox router.

Private items (NIP-51's encrypted-content form) are intentionally NOT
supported yet: it requires nip44_encrypt round-trips through the
bunker that some signers prompt for separately, which we will add in
a follow-up when the broader app gets nip44 sign-on-connect support.
For now we publish the standard public-tags form, which every signer
implements.

References:
  - NIP-51: https://github.com/nostr-protocol/nips/blob/master/51.md
  - NIP-09: tombstoning by republishing replaceable events
"""

from __future__ import annotations

import time
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Set

from PySide6.QtCore import QObject, QTimer, Signal

from nostr import CLIENT_NAME
from nostr.bunker import BunkerSessionPool
from nostr.events import build_event, verify_event
from nostr.relay import RelayPool, Subscription

from ._seeds import seed_relays
from .outbox_router import OutboxRouter
from .parser import NIP51_MUTE_LIST_KIND, parse_mute_list


# How long after EOSE we keep the per-pubkey subscription alive to
# pick up a refresh published moments later. The mute list is
# replaceable, so subscribing once and listening is cheap.
_REFRESH_DEBOUNCE_MS: int = 1200


class MuteListCache(QObject):
    """Tracks each profile's NIP-51 mute list.

    Designed for the editor's lifetime — load on profile connect,
    update on every kind:10000 received, replicate on every mute /
    unmute write. The marketplace dialog reads ``muted_pubkeys()`` to
    populate the trust policy and the panel re-renders on change.
    """

    # Fires when the locally cached set for ``owner_pubkey`` changes.
    # Marketplace dialog listens and re-pushes the snapshot so the
    # social panel updates immediately, no extra network round-trip.
    mute_list_updated = Signal(str)        # owner_pubkey

    def __init__(
        self,
        *,
        relay_pool: RelayPool,
        bunker_pool: Optional[BunkerSessionPool] = None,
        outbox_router: Optional[OutboxRouter] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._bunker_pool = bunker_pool
        self._router = outbox_router
        # owner_pubkey -> frozenset[muted_pubkey]
        self._sets: Dict[str, FrozenSet[str]] = {}
        # Most recent kind:10000 event observed per owner so a republish
        # carries forward unrelated tags (NIP-51 lists can include t /
        # word / e items too; we only edit ``p`` entries).
        self._events: Dict[str, dict] = {}
        # Live subs per owner, refcounted so multiple parts of the UI
        # can ask to follow a list without stomping on each other.
        self._watches: Dict[str, _MuteWatch] = {}

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def muted_pubkeys(self, owner_pubkey: str) -> FrozenSet[str]:
        """Returns the current mute set, or an empty frozenset.

        Safe to call before the first kind:10000 arrives; the trust
        policy treats "no list" as "nobody muted." Once the fetch
        completes the marketplace dialog re-pushes the snapshot via
        ``mute_list_updated``.
        """
        return self._sets.get(owner_pubkey, frozenset())

    def is_muted(self, owner_pubkey: str, target_pubkey: str) -> bool:
        return target_pubkey in self._sets.get(owner_pubkey, frozenset())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def watch(self, owner_pubkey: str, *, extra_relays: Iterable[str] = ()) -> None:
        """Open (or refcount up) a subscription to ``owner_pubkey``'s mute list.

        Each ``watch`` must be paired with one ``unwatch``. The cache
        survives long enough for the marketplace's typical flow
        (open dialog -> stay for minutes -> close).
        """
        existing = self._watches.get(owner_pubkey)
        if existing is not None:
            existing.refs += 1
            return
        watch = _MuteWatch(owner=owner_pubkey, refs=1)
        watch.subscription = self._open_subscription(owner_pubkey, extra_relays)
        self._watches[owner_pubkey] = watch

    def unwatch(self, owner_pubkey: str) -> None:
        watch = self._watches.get(owner_pubkey)
        if watch is None:
            return
        watch.refs -= 1
        if watch.refs <= 0:
            self._close_watch(watch)
            self._watches.pop(owner_pubkey, None)

    def close_all(self) -> None:
        for watch in list(self._watches.values()):
            self._close_watch(watch)
        self._watches.clear()

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def mute(
        self,
        profile,
        target_pubkey: str,
        *,
        on_status: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        """Add ``target_pubkey`` to the active profile's mute list.

        Optimistically updates the local view + signal so the UI flips
        the row to muted state immediately, then publishes the updated
        list in the background. A failed publish surfaces via
        ``on_status``; the local view stays optimistic to avoid a
        confusing flicker if the user retries.
        """
        if not target_pubkey:
            return
        owner = profile.user_pubkey
        muted = set(self._sets.get(owner, frozenset()))
        if target_pubkey in muted:
            return
        muted.add(target_pubkey)
        self._apply_local(owner, frozenset(muted))
        self._publish_update(profile, sorted(muted), on_status=on_status)

    def unmute(
        self,
        profile,
        target_pubkey: str,
        *,
        on_status: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        if not target_pubkey:
            return
        owner = profile.user_pubkey
        muted = set(self._sets.get(owner, frozenset()))
        if target_pubkey not in muted:
            return
        muted.discard(target_pubkey)
        self._apply_local(owner, frozenset(muted))
        self._publish_update(profile, sorted(muted), on_status=on_status)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _open_subscription(
        self,
        owner_pubkey: str,
        extra_relays: Iterable[str],
    ) -> Subscription:
        urls = self._read_relays(owner_pubkey, extra_relays)
        filters = [{"kinds": [NIP51_MUTE_LIST_KIND], "authors": [owner_pubkey], "limit": 1}]
        sub = self._relay_pool.subscribe(urls, filters)
        sub.event.connect(lambda e: self._handle_event(owner_pubkey, e))
        return sub

    def _close_watch(self, watch: "_MuteWatch") -> None:
        sub = watch.subscription
        if sub is not None:
            try:
                sub.close()
            except Exception:  # noqa: BLE001
                pass
            watch.subscription = None

    def _handle_event(self, owner_pubkey: str, event: dict) -> None:
        if not isinstance(event, dict):
            return
        if event.get("kind") != NIP51_MUTE_LIST_KIND:
            return
        # Dedup by event id before the (expensive) signature verify so
        # a relay flood serving the same row N times only verifies
        # once. The verifier is BIP-340 schnorr in pure-Python, so
        # repeated verifies of an identical payload waste main-thread
        # cycles.
        event_id = event.get("id")
        existing = self._events.get(owner_pubkey)
        if (existing is not None
                and event_id is not None
                and existing.get("id") == event_id):
            return
        if not verify_event(event):
            return
        if (event.get("pubkey") or "").lower() != owner_pubkey:
            return
        parsed = parse_mute_list(event)
        if parsed is None:
            return
        # NIP-01: keep the newer replaceable event.
        if existing is not None and event.get("created_at", 0) <= existing.get("created_at", 0):
            return
        self._events[owner_pubkey] = event
        self._apply_local(owner_pubkey, parsed.muted_pubkeys)

    def _apply_local(self, owner_pubkey: str, muted: FrozenSet[str]) -> None:
        if self._sets.get(owner_pubkey) == muted:
            return
        self._sets[owner_pubkey] = muted
        self.mute_list_updated.emit(owner_pubkey)

    def _publish_update(
        self,
        profile,
        muted_pubkeys: List[str],
        *,
        on_status: Optional[Callable[[str, int], None]],
    ) -> None:
        if self._bunker_pool is None:
            self._notify(on_status, "Sign in to publish your mute list.", 4000)
            return

        owner = profile.user_pubkey
        previous = self._events.get(owner)
        tags = self._merge_mute_tags(previous, muted_pubkeys)
        # NIP-51 stores private mute items in an encrypted ``content``
        # blob (NIP-04 / NIP-44 to self). We don't currently support
        # writing that blob — but we MUST preserve whatever the
        # previous event carried so a user who set private mutes from
        # another client doesn't lose them on the first edit here.
        prior_content = ""
        if previous is not None:
            raw_content = previous.get("content", "")
            if isinstance(raw_content, str):
                prior_content = raw_content

        unsigned = build_event(
            pubkey_hex=owner,
            kind=NIP51_MUTE_LIST_KIND,
            content=prior_content,
            tags=tags,
            created_at=int(time.time()),
        )

        def _on_signer(client) -> None:
            def _on_signed(signed: dict) -> None:
                self._events[owner] = signed
                urls = self._write_relays(owner)
                if not urls:
                    self._notify(on_status, "Mute list saved locally — no relay reached.", 4000)
                    return
                self._relay_pool.publish(urls, signed)
                self._notify(on_status, "Updated mute list published.", 3500)

            def _on_sign_failed(reason: str) -> None:
                self._notify(on_status, f"Signer rejected mute publish: {reason}", 5000)

            client.sign_event(unsigned, on_success=_on_signed, on_failure=_on_sign_failed)

        def _on_signer_failed(reason: str) -> None:
            self._notify(on_status, f"Signer unreachable: {reason}", 5000)

        self._bunker_pool.get(profile, _on_signer, _on_signer_failed)

    @staticmethod
    def _merge_mute_tags(previous: Optional[dict], muted_pubkeys: List[str]) -> List[List[str]]:
        """Build the new tag list for a NIP-51 mute event.

        Strategy: preserve every non-``p`` tag from the previous event
        (so unrelated mute kinds — t / word / e — survive a pubkey
        edit), then append the canonical ``p`` set in sorted order so
        diffs across publishes are stable.
        """
        tags: List[List[str]] = []
        if previous is not None:
            for tag in previous.get("tags", []) or []:
                if not isinstance(tag, list) or not tag:
                    continue
                if tag[0] == "p":
                    continue
                tags.append(list(tag))
        for pubkey in muted_pubkeys:
            tags.append(["p", pubkey])
        tags.append(["client", CLIENT_NAME])
        return tags

    def _read_relays(self, owner_pubkey: str, extra: Iterable[str]) -> List[str]:
        seeds = self._seed_relays(extra)
        if self._router is None:
            return seeds
        plan = self._router.relays_for_read([owner_pubkey], seeds=seeds)
        return plan.relays

    def _write_relays(self, owner_pubkey: str) -> List[str]:
        seeds = self._seed_relays(())
        if self._router is None:
            return seeds
        plan = self._router.relays_for_write(owner_pubkey, [], seeds=seeds)
        return plan.relays

    @staticmethod
    def _seed_relays(extra: Iterable[str]) -> List[str]:
        return seed_relays(extra)

    @staticmethod
    def _notify(on_status: Optional[Callable], message: str, ms: int) -> None:
        if on_status is not None:
            try:
                on_status(message, ms)
            except Exception:  # noqa: BLE001
                pass


class _MuteWatch:
    """One open subscription + refcount per owner pubkey."""

    __slots__ = ("owner", "refs", "subscription")

    def __init__(self, *, owner: str, refs: int) -> None:
        self.owner = owner
        self.refs = refs
        self.subscription: Optional[Subscription] = None
