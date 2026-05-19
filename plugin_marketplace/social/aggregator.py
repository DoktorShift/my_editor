"""Roll raw engagement events into a single ``EngagementSnapshot``.

Pipeline (each step is in-memory only; relays + cache are upstream):

  1. Apply the **trust policy**: drop authors on the mute list,
     optionally drop authors not in the follow graph or not
     NIP-05 verified.
  2. Apply **NIP-09 deletions**: a kind:5 from the original author
     erases their referenced events.
  3. **Dedup ratings**: one rating per ``(plugin, author)`` pair,
     newest wins.
  4. **Build the comment tree**: top-level threads in newest-first
     order, children sorted oldest-first inside each thread.
  5. **Sum zaps**: skip duplicates by ``event_id``.

The aggregator does no I/O. It takes lists in, returns a snapshot
out. Filter toggles in the UI are simply re-runs over the same input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .models import (
    CommentEvent,
    CommentNode,
    ContactList,
    DeletionRequest,
    EngagementSnapshot,
    MuteList,
    Nip05Verification,
    PluginAnchor,
    RatingDistribution,
    RatingEvent,
    ZapReceipt,
)


# --------------------------------------------------------------------------- #
# Trust policy                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TrustPolicy:
    """The filter knobs the UI exposes to the user.

    Defaults are intentionally lenient: the empty policy lets
    everything through. Filters are layered, not replaced: enabling
    ``verified_only`` *adds* that restriction on top of any
    existing mute list, it doesn't remove the mute filter.
    """

    # Hard exclusions
    muted_pubkeys: FrozenSet[str] = frozenset()

    # Optional restrictions
    require_nip05: bool = False
    require_follow_graph: bool = False

    # Inputs used when the optional restrictions are on.
    verified_pubkeys: FrozenSet[str] = frozenset()
    followed_pubkeys: FrozenSet[str] = frozenset()

    # When ``True``, the viewer's own contributions are always shown
    # regardless of the filters. Without this a freshly-published
    # rating would disappear from the user's own view while they're
    # waiting for NIP-05 propagation.
    viewer_pubkey: Optional[str] = None

    def allows(self, author_pubkey: str) -> bool:
        """Decide whether ``author_pubkey`` may contribute to the snapshot.

        Order matters:
          - Mute beats everything (even the viewer's own pubkey).
          - The viewer always sees their own contributions otherwise.
          - Verified / follow-graph filters apply last.
        """
        if author_pubkey in self.muted_pubkeys:
            return False
        if self.viewer_pubkey and author_pubkey == self.viewer_pubkey:
            return True
        if self.require_nip05 and author_pubkey not in self.verified_pubkeys:
            return False
        if self.require_follow_graph and author_pubkey not in self.followed_pubkeys:
            return False
        return True

    @classmethod
    def default(cls) -> "TrustPolicy":
        """The shipped default for first-launch users.

        We deliberately ship neither ``require_nip05`` nor
        ``require_follow_graph`` because the network is small at
        launch; surfacing nothing would make the marketplace feel
        broken. The mute filter is always applied, and the UI nudges
        toward verified-only once a profile is connected.
        """
        return cls()

    def with_mute_list(self, mute: Optional[MuteList]) -> "TrustPolicy":
        return self if mute is None else _replace(self, muted_pubkeys=mute.muted_pubkeys)

    def with_contact_list(self, contacts: Optional[ContactList]) -> "TrustPolicy":
        return self if contacts is None else _replace(self, followed_pubkeys=contacts.follows)

    def with_nip05_verifications(self, verifications: Iterable[Nip05Verification]) -> "TrustPolicy":
        verified = frozenset(v.pubkey for v in verifications if v.verified)
        return _replace(self, verified_pubkeys=verified)


def _replace(policy: TrustPolicy, **kwargs) -> TrustPolicy:
    """frozen-dataclass-safe ``copy.replace`` shim.

    Avoids importing ``dataclasses.replace`` just for this one call
    site; keeps the policy's frozen contract obvious at the source.
    """
    base = dict(
        muted_pubkeys=policy.muted_pubkeys,
        require_nip05=policy.require_nip05,
        require_follow_graph=policy.require_follow_graph,
        verified_pubkeys=policy.verified_pubkeys,
        followed_pubkeys=policy.followed_pubkeys,
        viewer_pubkey=policy.viewer_pubkey,
    )
    base.update(kwargs)
    return TrustPolicy(**base)


# --------------------------------------------------------------------------- #
# Aggregator                                                                  #
# --------------------------------------------------------------------------- #

class EngagementAggregator:
    """Roll parsed events for one plugin into an ``EngagementSnapshot``.

    Construction is cheap; one instance per snapshot computation. The
    aggregator never holds onto its inputs after returning, so callers
    can mutate them freely afterwards.
    """

    def __init__(self, *, anchor: PluginAnchor) -> None:
        self._anchor = anchor

    # ----------------------------------------------------------------------
    # Addressable deletion check (NIP-09 + addressable / kind:30700 listing)
    # ----------------------------------------------------------------------

    @staticmethod
    def is_listing_deleted(
        deletions: Iterable[DeletionRequest],
        *,
        coord: str,
        author_pubkey: str,
        event_created_at: int,
    ) -> bool:
        """Has the author tombstoned the addressable event at ``coord``?

        NIP-09 ``a``-tag deletions wipe every version of the coord up
        to the deletion's ``created_at``. Callers that load a kind:30700
        listing event should consult this before showing the listing so
        a creator who deleted their plugin doesn't keep being surfaced
        from cached relay copies.

        ``author_pubkey`` is the listing event's author. The aggregator
        enforces "deletion's author == target's author" — a curator or
        relay operator cannot tombstone someone else's listing.
        """
        if not coord or not author_pubkey:
            return False
        index = _index_deletions(deletions)
        return index.is_coord_deleted_at_or_before(
            coord, author_pubkey, event_created_at,
        )

    # ----------------------------------------------------------------------
    def snapshot(
        self,
        *,
        ratings: Iterable[RatingEvent] = (),
        comments: Iterable[CommentEvent] = (),
        zaps: Iterable[ZapReceipt] = (),
        deletions: Iterable[DeletionRequest] = (),
        policy: TrustPolicy = TrustPolicy(),
    ) -> EngagementSnapshot:
        """Compute the snapshot. Inputs may be in any order.

        Deletions are applied first because they can eliminate
        events that would otherwise survive the trust filter and
        skew the result.
        """
        deletion_index = _index_deletions(deletions)

        ratings_filtered = self._filter_ratings(ratings, deletion_index, policy)
        comments_filtered = self._filter_comments(comments, deletion_index, policy)
        zaps_filtered = self._filter_zaps(zaps, deletion_index, policy)

        own = policy.viewer_pubkey
        own_rating = None
        own_comment_ids: List[str] = []

        # Dedup ratings: last-write-wins per author.
        latest_by_author: Dict[str, RatingEvent] = {}
        for rating in ratings_filtered:
            existing = latest_by_author.get(rating.author_pubkey)
            if existing is None or rating.created_at > existing.created_at:
                latest_by_author[rating.author_pubkey] = rating

        if own is not None:
            own_rating = latest_by_author.get(own)

        distribution = _distribution(latest_by_author.values())

        # Build the comment tree.
        thread = _build_thread(comments_filtered)

        if own is not None:
            for c in comments_filtered:
                if c.author_pubkey == own:
                    own_comment_ids.append(c.event_id)

        # Sum zap amounts. The parser already validated the
        # description/amount pairing so we can just total them up.
        seen_zap_ids: Set[str] = set()
        total_sats = 0
        zap_count = 0
        for zap in zaps_filtered:
            if zap.event_id in seen_zap_ids:
                continue
            seen_zap_ids.add(zap.event_id)
            total_sats += zap.amount_sats
            zap_count += 1

        return EngagementSnapshot(
            anchor=self._anchor,
            distribution=distribution,
            comments=tuple(thread),
            zaps_sats=total_sats,
            zap_count=zap_count,
            own_rating=own_rating,
            own_comment_ids=tuple(own_comment_ids),
        )

    # ----------------------------------------------------------------------
    def _filter_ratings(
        self,
        ratings: Iterable[RatingEvent],
        deletion_index: "_DeletionIndex",
        policy: TrustPolicy,
    ) -> List[RatingEvent]:
        out: List[RatingEvent] = []
        for r in ratings:
            if r.anchor != self._anchor:
                continue
            if deletion_index.is_deleted(r.event_id, r.author_pubkey):
                continue
            if not policy.allows(r.author_pubkey):
                continue
            out.append(r)
        return out

    def _filter_comments(
        self,
        comments: Iterable[CommentEvent],
        deletion_index: "_DeletionIndex",
        policy: TrustPolicy,
    ) -> List[CommentEvent]:
        out: List[CommentEvent] = []
        for c in comments:
            if c.anchor != self._anchor:
                continue
            if deletion_index.is_deleted(c.event_id, c.author_pubkey):
                continue
            if not policy.allows(c.author_pubkey):
                continue
            out.append(c)
        return out

    def _filter_zaps(
        self,
        zaps: Iterable[ZapReceipt],
        deletion_index: "_DeletionIndex",
        policy: TrustPolicy,
    ) -> List[ZapReceipt]:
        out: List[ZapReceipt] = []
        for z in zaps:
            if z.anchor != self._anchor:
                continue
            # Zap receipts are signed by the LNURL service, not by the
            # zapper, and we deliberately don't honor deletions on
            # zaps: once paid, the transaction is real. Mute the
            # *zapper* if you don't want their zap in your view.
            if not policy.allows(z.zapper_pubkey):
                continue
            out.append(z)
        return out


# --------------------------------------------------------------------------- #
# Deletion index                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class _DeletionIndex:
    """Cached lookup: did a particular event author publish a deletion
    naming this event (or addressable coord)?

    NIP-09 requires the deletion to be authored by the same key that
    originally published the target. We track two views:

      - ``by_author_and_event`` for ``e``-tag deletions of regular
        events (kind:1985 ratings, kind:1111 comments, etc.).
      - ``by_author_and_coord`` for ``a``-tag deletions of addressable
        events. NIP-09 wipes every version of that coord authored by
        the same pubkey up to the deletion's ``created_at``.

    Cross-author deletions are refused (the original author check is
    enforced by the aggregator before calling ``is_deleted*``), so a
    hostile relay can't suppress someone else's events.
    """

    by_author_and_event: Dict[Tuple[str, str], int] = field(default_factory=dict)
    by_author_and_coord: Dict[Tuple[str, str], int] = field(default_factory=dict)

    def is_deleted(self, event_id: str, author_pubkey: str) -> bool:
        return (author_pubkey, event_id) in self.by_author_and_event

    def is_coord_deleted_at_or_before(
        self,
        coord: str,
        author_pubkey: str,
        event_created_at: int,
    ) -> bool:
        """``True`` when an addressable event has been tombstoned.

        NIP-09 says an ``a``-tag deletion wipes "every version up to the
        deletion's ``created_at``". A version published *later* than the
        deletion survives until a fresh deletion is published. We honor
        that timestamp window so an author can re-publish a corrected
        version after a deletion without it disappearing.
        """
        deleted_at = self.by_author_and_coord.get((author_pubkey, coord))
        if deleted_at is None:
            return False
        return event_created_at <= deleted_at


def _index_deletions(deletions: Iterable[DeletionRequest]) -> _DeletionIndex:
    """Build the deletion index from parsed kind:5 requests.

    Multiple deletions for the same target keep the newest timestamp;
    that's what the spec's "up to created_at" window needs.
    """
    index = _DeletionIndex()
    for d in deletions:
        for event_id in d.target_event_ids:
            key = (d.author_pubkey, event_id)
            existing = index.by_author_and_event.get(key, -1)
            if d.created_at > existing:
                index.by_author_and_event[key] = d.created_at
        for coord in d.target_coords:
            key = (d.author_pubkey, coord)
            existing = index.by_author_and_coord.get(key, -1)
            if d.created_at > existing:
                index.by_author_and_coord[key] = d.created_at
    return index


# --------------------------------------------------------------------------- #
# Rating distribution                                                         #
# --------------------------------------------------------------------------- #

def _distribution(ratings: Iterable[RatingEvent]) -> RatingDistribution:
    buckets = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in ratings:
        buckets[r.stars.value] += 1
    return RatingDistribution(
        one=buckets[1], two=buckets[2], three=buckets[3],
        four=buckets[4], five=buckets[5],
    )


# --------------------------------------------------------------------------- #
# Comment tree                                                                #
# --------------------------------------------------------------------------- #

def _build_thread(comments: Iterable[CommentEvent]) -> List[CommentNode]:
    """Build the comment tree.

    Strategy: index all comments by id, then attach each child to its
    parent if the parent is present. Orphans (replies whose parent we
    haven't received yet) are promoted to top-level threads so the
    user still sees them; they reattach automatically when the parent
    streams in and the snapshot is recomputed.

    Top-level comments are sorted newest-first; children inside a
    thread are sorted oldest-first (chronological reading order).
    """
    by_id: Dict[str, CommentNode] = {}
    for c in comments:
        by_id[c.event_id] = CommentNode(comment=c)

    roots: List[CommentNode] = []
    for c in comments:
        node = by_id[c.event_id]
        parent_id = c.parent_event_id
        if parent_id and parent_id in by_id:
            by_id[parent_id].children.append(node)
        else:
            roots.append(node)

    # Sort
    roots.sort(key=lambda n: n.comment.created_at, reverse=True)
    for node in by_id.values():
        node.children.sort(key=lambda n: n.comment.created_at)

    return roots
