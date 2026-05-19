"""Typed records the social layer hands between its modules.

Every record here is the parsed, validated form of a raw Nostr event.
Construction is the parser's job (see ``parser.py``); consumers can
assume an instance reflects a correctly-formed event for the kind
it represents. Anything we couldn't trust would never have been
constructed.

Why dataclasses rather than dicts:

  - Static guarantees about which fields exist on a record.
  - One place to attach helper methods (``Stars.value``, ``CommentNode.children``).
  - Trivial to cache via the SQLite layer because every field is
    JSON-serializable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Plugin anchor (kind:pubkey:d-tag triple)                                    #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PluginAnchor:
    """The addressable Nostr coordinate a plugin's engagement targets.

    A plugin listing event is ``kind:30700`` published by the plugin
    author with the plugin id as its ``d`` tag. Comments, ratings and
    zaps all carry an ``a`` tag (or ``A``) pointing at this triple.
    """

    kind: int            # always 30700 for plugin listings
    author_pubkey: str   # 64-char hex
    plugin_id: str       # matches the manifest id

    @property
    def coord(self) -> str:
        """The ``kind:pubkey:d-tag`` string used in tags and filters."""
        return f"{self.kind}:{self.author_pubkey}:{self.plugin_id}"

    @classmethod
    def parse(cls, coord: str) -> Optional["PluginAnchor"]:
        """Parse a ``kind:pubkey:d-tag`` string. Returns ``None`` for
        anything malformed so the parser layer can drop the event
        silently rather than raising."""
        parts = coord.split(":", 2)
        if len(parts) != 3:
            return None
        try:
            kind = int(parts[0])
        except ValueError:
            return None
        pubkey = parts[1].lower()
        if len(pubkey) != 64 or any(c not in "0123456789abcdef" for c in pubkey):
            return None
        plugin_id = parts[2]
        if not plugin_id:
            return None
        return cls(kind=kind, author_pubkey=pubkey, plugin_id=plugin_id)


# --------------------------------------------------------------------------- #
# Stars (1..5)                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Stars:
    """A discrete star value, 1..5.

    Wrapped in a tiny type rather than passed as a bare int so it's
    impossible for the aggregator to confuse a rating with a zap
    amount or a comment count further down the pipeline.
    """

    value: int

    def __post_init__(self) -> None:
        if self.value < 1 or self.value > 5:
            raise ValueError(f"star value must be 1..5, got {self.value}")


# --------------------------------------------------------------------------- #
# Parsed engagement events                                                    #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RatingEvent:
    """A parsed ``kind:1985`` NIP-32 label event scoring a plugin.

    The aggregator keeps the latest rating per ``(plugin_anchor, author_pubkey)``
    pair; earlier ratings from the same author are ignored. This
    matches how NIP-32 expects labels to be revised: by republishing.
    """

    event_id: str
    author_pubkey: str        # the rater
    anchor: PluginAnchor      # plugin being rated
    stars: Stars
    created_at: int           # unix seconds; needed for dedup ordering
    content: str = ""         # optional short reason (NIP-32 allows free text)


@dataclass(frozen=True)
class CommentEvent:
    """A parsed ``kind:1111`` NIP-22 comment.

    Top-level comments are recognised by ``parent_event_id is None``.
    Replies carry ``parent_event_id`` pointing at another comment.
    The ``anchor`` is always the plugin listing event, no matter how
    deep in the reply chain the comment sits, so the whole tree can
    be retrieved from one filter.
    """

    event_id: str
    author_pubkey: str
    anchor: PluginAnchor
    content: str
    created_at: int
    parent_event_id: Optional[str] = None
    parent_author_pubkey: Optional[str] = None


@dataclass(frozen=True)
class ZapReceipt:
    """A parsed + validated ``kind:9735`` zap receipt for a plugin.

    Per NIP-57, a receipt is the lightning service's signed proof
    that a zap was paid. We surface only validated receipts:

      - the receipt's ``description`` tag holds the original zap
        request as a JSON string, and that request targets this
        plugin's anchor via its ``a`` tag.
      - the bolt11 amount matches the zap request's ``amount`` tag.

    ``zapper_pubkey`` is the original zap request's signer (the
    person who pressed the button), not the LNURL service that
    minted the receipt.
    """

    event_id: str               # receipt event id (signed by LNURL service)
    zapper_pubkey: str          # who sent the zap (from the zap request)
    anchor: PluginAnchor
    amount_sats: int            # parsed from the zap request's amount tag, in sats
    created_at: int             # receipt created_at
    content: str = ""           # optional zapper comment


@dataclass(frozen=True)
class DeletionRequest:
    """A parsed ``kind:5`` NIP-09 deletion request.

    The aggregator honors a deletion only when the deletion's
    author matches the target event's author: a user can delete
    their own contribution and nobody else's.
    """

    event_id: str               # the kind:5 event's own id
    author_pubkey: str          # who is asking for the deletion
    target_event_ids: Tuple[str, ...]    # events referenced via ``e``
    target_coords: Tuple[str, ...]       # addressable refs via ``a``
    created_at: int


# --------------------------------------------------------------------------- #
# Trust list events                                                           #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MuteList:
    """A parsed NIP-51 mute list (kind ``10000``).

    Contains the ``p`` tags from the user's published list. The
    aggregator excludes events whose author is on the active mute
    list before any other filter runs.
    """

    owner_pubkey: str
    muted_pubkeys: frozenset      # frozenset[str]
    created_at: int


@dataclass(frozen=True)
class ContactList:
    """A parsed NIP-02 contact list (kind ``3``).

    Used for the "from people I follow" filter. Stored as a
    frozenset so membership checks are O(1) and the record is
    safely hashable for memoization.
    """

    owner_pubkey: str
    follows: frozenset
    created_at: int


# --------------------------------------------------------------------------- #
# Trust signals                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Nip05Verification:
    """The result of a NIP-05 verification check for one pubkey.

    Verification is asynchronous (it requires an HTTPS fetch to the
    user's domain) so the parser stamps a record with ``verified =
    None`` initially. The verifier worker fills in the actual result
    later and updates the cache.
    """

    pubkey: str
    identifier: str                  # e.g. "alice@my-editor.dev"
    verified: Optional[bool]         # None = not yet checked
    checked_at: Optional[int] = None


# --------------------------------------------------------------------------- #
# Aggregated snapshot (what the UI consumes)                                  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RatingDistribution:
    """Histogram of star counts plus the derived average + total.

    The aggregator returns this as a single record so the UI can
    render the histogram bars and the headline "4.8 (47 ratings)"
    from one source of truth.
    """

    one: int = 0
    two: int = 0
    three: int = 0
    four: int = 0
    five: int = 0

    @property
    def total(self) -> int:
        return self.one + self.two + self.three + self.four + self.five

    @property
    def average(self) -> float:
        t = self.total
        if t == 0:
            return 0.0
        weighted = (
            1 * self.one + 2 * self.two + 3 * self.three
            + 4 * self.four + 5 * self.five
        )
        return weighted / t

    def percent(self, star: int) -> float:
        """Percentage of total ratings that gave ``star`` stars, 0..100."""
        if self.total == 0:
            return 0.0
        return (self.bucket(star) / self.total) * 100.0

    def bucket(self, star: int) -> int:
        return {1: self.one, 2: self.two, 3: self.three, 4: self.four, 5: self.five}[star]


@dataclass
class CommentNode:
    """One node in the rendered comment tree.

    ``children`` is mutable on purpose: the aggregator walks the flat
    event list once and appends children to their parents in place.
    Once handed to the UI the tree is treated as read-only.
    """

    comment: CommentEvent
    children: List["CommentNode"] = field(default_factory=list)


@dataclass(frozen=True)
class EngagementSnapshot:
    """Everything the social panel needs to render one plugin.

    Built by ``EngagementAggregator.snapshot()`` from the raw events
    cached for a plugin anchor. Replays are cheap; toggling a trust
    filter is a recompute over the same input, not a re-fetch.
    """

    anchor: PluginAnchor
    distribution: RatingDistribution
    comments: Tuple[CommentNode, ...]    # top-level threads in newest-first order
    zaps_sats: int
    zap_count: int
    # The viewer's own contributions, surfaced separately so the UI
    # can pre-fill the composer's star value and offer "edit / delete"
    # affordances next to the user's own past comments.
    own_rating: Optional[RatingEvent] = None
    own_comment_ids: Tuple[str, ...] = ()

    @property
    def comment_count(self) -> int:
        """Total comments in the tree, including replies."""
        def walk(node: CommentNode) -> int:
            return 1 + sum(walk(c) for c in node.children)
        return sum(walk(n) for n in self.comments)
