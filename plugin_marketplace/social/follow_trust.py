"""Owner-keyed NIP-02 follow set cache for the marketplace's trust policy.

Replaces the prior code path that derived the "from people I follow"
set from the editor-wide ``KnownPeople`` table. That global table is
populated by every contact-list fetch across the editor's lifetime,
so it leaks across profile switches: a user who connected profile A
yesterday would see A's follows applied to profile B today.

This cache is keyed on the **owner pubkey** of the contact list:

  - ``follows(owner_pubkey)`` returns the cached follow set for that
    owner, or an empty frozenset.
  - ``watch(owner_pubkey, extra_relays)`` opens a relay subscription
    that keeps the cache fresh (replaceable kind:3 events).
  - ``close_all()`` is idempotent and is called at editor shutdown +
    on profile switch so dropped profiles never linger.

Signature verification on incoming events guarantees relays can't
inject follows for someone else's profile.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional

from PySide6.QtCore import QObject, Signal

from nostr.events import verify_event
from nostr.relay import RelayPool, Subscription

from ._seeds import seed_relays
from .outbox_router import OutboxRouter
from .parser import NIP02_CONTACT_LIST_KIND, parse_contact_list


class FollowTrustCache(QObject):
    """Per-owner kind:3 follow set, refreshed live."""

    follow_set_updated = Signal(str)   # owner_pubkey

    def __init__(
        self,
        *,
        relay_pool: RelayPool,
        outbox_router: Optional[OutboxRouter] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._router = outbox_router
        # owner_pubkey -> frozenset[follow_pubkey]
        self._sets: Dict[str, FrozenSet[str]] = {}
        # owner_pubkey -> last observed kind:3 event for "newest wins"
        # updates (per NIP-01 replaceable-event semantics).
        self._events: Dict[str, dict] = {}
        # Refcounted watches so multiple parts of the UI can request
        # a follow-set subscription without stomping on each other.
        self._watches: Dict[str, "_FollowWatch"] = {}

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def follows(self, owner_pubkey: str) -> FrozenSet[str]:
        """The cached follow set, or empty if unknown.

        Empty is a safe default: the trust policy never *requires* a
        follow filter unless the user opts in via the "From people I
        follow" chip, and that chip is only meaningful once the
        owner's kind:3 has been resolved.
        """
        return self._sets.get(owner_pubkey, frozenset())

    def has_follows(self, owner_pubkey: str) -> bool:
        return bool(self._sets.get(owner_pubkey))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def watch(self, owner_pubkey: str, *, extra_relays: Iterable[str] = ()) -> None:
        existing = self._watches.get(owner_pubkey)
        if existing is not None:
            existing.refs += 1
            return
        watch = _FollowWatch(owner=owner_pubkey, refs=1)
        watch.subscription = self._open_subscription(owner_pubkey, extra_relays)
        self._watches[owner_pubkey] = watch

    def unwatch(self, owner_pubkey: str) -> None:
        watch = self._watches.get(owner_pubkey)
        if watch is None:
            return
        watch.refs -= 1
        if watch.refs <= 0:
            self._close(watch)
            self._watches.pop(owner_pubkey, None)

    def close_all(self) -> None:
        for watch in list(self._watches.values()):
            self._close(watch)
        self._watches.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _open_subscription(
        self,
        owner_pubkey: str,
        extra_relays: Iterable[str],
    ) -> Subscription:
        urls = self._read_relays(owner_pubkey, extra_relays)
        filters = [{
            "kinds": [NIP02_CONTACT_LIST_KIND],
            "authors": [owner_pubkey],
            "limit": 1,
        }]
        sub = self._relay_pool.subscribe(urls, filters)
        sub.event.connect(lambda e: self._handle_event(owner_pubkey, e))
        return sub

    def _close(self, watch: "_FollowWatch") -> None:
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
        if event.get("kind") != NIP02_CONTACT_LIST_KIND:
            return
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
        parsed = parse_contact_list(event)
        if parsed is None:
            return
        if existing is not None and event.get("created_at", 0) <= existing.get("created_at", 0):
            return
        self._events[owner_pubkey] = event
        if self._sets.get(owner_pubkey) == parsed.follows:
            return
        self._sets[owner_pubkey] = frozenset(parsed.follows)
        self.follow_set_updated.emit(owner_pubkey)

    def _read_relays(self, owner_pubkey: str, extra: Iterable[str]) -> List[str]:
        seeds = self._seed_relays(extra)
        if self._router is None:
            return seeds
        plan = self._router.relays_for_read([owner_pubkey], seeds=seeds)
        return plan.relays

    @staticmethod
    def _seed_relays(extra: Iterable[str]) -> List[str]:
        return seed_relays(extra)


class _FollowWatch:
    __slots__ = ("owner", "refs", "subscription")

    def __init__(self, *, owner: str, refs: int) -> None:
        self.owner = owner
        self.refs = refs
        self.subscription: Optional[Subscription] = None
