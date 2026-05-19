"""Parse raw Nostr events into typed engagement records.

Strict on purpose: every parser returns ``None`` for anything that
doesn't match the spec we care about, rather than raising. The caller
treats malformed events as if they were never received. This keeps
relays from being able to poison the aggregator by serving us
truncated or mis-tagged events.

Specs (verbatim links so future-me can re-check):

  - NIP-01 events shape        https://github.com/nostr-protocol/nips/blob/master/01.md
  - NIP-02 contact list        https://github.com/nostr-protocol/nips/blob/master/02.md
  - NIP-09 event deletion      https://github.com/nostr-protocol/nips/blob/master/09.md
  - NIP-22 comments            https://github.com/nostr-protocol/nips/blob/master/22.md
  - NIP-32 labels              https://github.com/nostr-protocol/nips/blob/master/32.md
  - NIP-51 lists               https://github.com/nostr-protocol/nips/blob/master/51.md
  - NIP-57 zaps                https://github.com/nostr-protocol/nips/blob/master/57.md
  - NIP-05 verification        https://github.com/nostr-protocol/nips/blob/master/05.md

Every parser receives a NIP-01 event dict (``{"id", "pubkey", "kind",
"tags", "content", "created_at", "sig"}``). The signature is not
re-verified here; the caller is expected to have done that once at
ingest time via ``nostr.events.verify_event``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .bolt11 import Bolt11Error, decode_bolt11
from .models import (
    CommentEvent,
    ContactList,
    DeletionRequest,
    MuteList,
    PluginAnchor,
    RatingEvent,
    Stars,
    ZapReceipt,
)


# --------------------------------------------------------------------------- #
# Kind constants                                                              #
# --------------------------------------------------------------------------- #

# Plugin listing event kind. Project convention until a NIP pins this.
PLUGIN_LISTING_KIND: int = 30700

# Standard NIPs we read + write.
NIP22_COMMENT_KIND: int = 1111
NIP32_LABEL_KIND: int = 1985
NIP57_ZAP_RECEIPT_KIND: int = 9735
NIP57_ZAP_REQUEST_KIND: int = 9734
NIP09_DELETION_KIND: int = 5
NIP01_METADATA_KIND: int = 0
NIP02_CONTACT_LIST_KIND: int = 3
NIP51_MUTE_LIST_KIND: int = 10000

# NIP-32 namespace for our ratings. The label is the star value as a
# decimal string. We pick a reverse-DNS-style namespace because that's
# the convention NIP-32 endorses for project-scoped labels.
RATING_LABEL_NAMESPACE: str = "com.my-editor.review/rating"


# --------------------------------------------------------------------------- #
# Generic helpers                                                             #
# --------------------------------------------------------------------------- #

def _tags(event: dict) -> List[List[str]]:
    """Return the event's tags as a list-of-lists. Tolerates bogus shapes.

    Per NIP-01 each tag is an array of strings. We coerce non-conforming
    entries to empty strings so downstream code can index without try/except
    for every access.
    """
    raw = event.get("tags")
    if not isinstance(raw, list):
        return []
    out: List[List[str]] = []
    for tag in raw:
        if not isinstance(tag, list):
            continue
        out.append([str(x) if not isinstance(x, str) else x for x in tag])
    return out


def _find_first(tags: List[List[str]], name: str) -> Optional[List[str]]:
    """First tag whose first element equals ``name`` (case-sensitive)."""
    for tag in tags:
        if tag and tag[0] == name:
            return tag
    return None


def _find_all(tags: List[List[str]], name: str) -> List[List[str]]:
    return [t for t in tags if t and t[0] == name]


def _hex_pubkey(value: Any) -> Optional[str]:
    """Validate that ``value`` is a 32-byte hex string (lowercase)."""
    if not isinstance(value, str):
        return None
    pk = value.lower()
    if len(pk) != 64:
        return None
    if any(c not in "0123456789abcdef" for c in pk):
        return None
    return pk


def _hex_event_id(value: Any) -> Optional[str]:
    """Same shape as a pubkey: 32 bytes / 64 hex chars."""
    return _hex_pubkey(value)


def _plugin_anchor_from_a_tag(tag: List[str]) -> Optional[PluginAnchor]:
    """Parse the second element of an ``a`` / ``A`` tag into an anchor.

    ``a`` tags are ``["a", "<kind>:<pubkey>:<d>", "<relay-hint>?"]``;
    we only care about the coord. Plugin engagement always targets the
    listing kind, so we filter on that here.
    """
    if len(tag) < 2:
        return None
    anchor = PluginAnchor.parse(tag[1])
    if anchor is None or anchor.kind != PLUGIN_LISTING_KIND:
        return None
    return anchor


def _shared_event_fields(event: dict) -> Optional[Tuple[str, str, int]]:
    """Pull ``(event_id, pubkey, created_at)`` out of any event.

    Returns ``None`` if any of those required fields are missing or
    malformed. Keeps the per-kind parsers focused on their own work.
    """
    event_id = _hex_event_id(event.get("id"))
    pubkey = _hex_pubkey(event.get("pubkey"))
    created_at = event.get("created_at")
    if event_id is None or pubkey is None or not isinstance(created_at, int):
        return None
    return event_id, pubkey, created_at


# --------------------------------------------------------------------------- #
# NIP-32 ratings                                                              #
# --------------------------------------------------------------------------- #

def parse_rating(event: dict, expected_anchor: PluginAnchor) -> Optional[RatingEvent]:
    """Parse a ``kind:1985`` label event as a plugin rating.

    Accepts an event only if:
      - the kind is 1985
      - it carries an ``L`` tag declaring the rating namespace
      - it carries an ``l`` tag whose label is a decimal string ``"1".."5"``
        and whose third element repeats the rating namespace
      - it carries an ``a`` tag pointing at the expected plugin anchor

    The ``expected_anchor`` argument is required because we never want
    to accidentally aggregate a rating from a different plugin into
    this one's score: the subscription filter narrows the wire, but
    we double-check on parse so a malicious relay can't pollute the
    cache.
    """
    if event.get("kind") != NIP32_LABEL_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    event_id, author_pubkey, created_at = shared
    tags = _tags(event)

    # Namespace declaration (NIP-32 ``L`` tag).
    namespace_tag = _find_first(tags, "L")
    if namespace_tag is None or len(namespace_tag) < 2:
        return None
    if namespace_tag[1] != RATING_LABEL_NAMESPACE:
        return None

    # Label itself (NIP-32 ``l`` tag, with namespace repeated in slot 2).
    label_tag = _find_first(tags, "l")
    if label_tag is None or len(label_tag) < 3:
        return None
    if label_tag[2] != RATING_LABEL_NAMESPACE:
        return None
    try:
        stars = Stars(int(label_tag[1]))
    except (TypeError, ValueError):
        return None

    # Target plugin anchor.
    anchor = _find_anchor_in_a_tags(tags, expected_anchor)
    if anchor is None:
        return None

    content = event.get("content", "")
    if not isinstance(content, str):
        content = ""

    return RatingEvent(
        event_id=event_id,
        author_pubkey=author_pubkey,
        anchor=anchor,
        stars=stars,
        created_at=created_at,
        content=content,
    )


def _find_anchor_in_a_tags(
    tags: List[List[str]],
    expected: PluginAnchor,
) -> Optional[PluginAnchor]:
    """Return the matching anchor if any ``a`` tag points at ``expected``."""
    for tag in _find_all(tags, "a"):
        anchor = _plugin_anchor_from_a_tag(tag)
        if anchor is not None and anchor == expected:
            return anchor
    return None


# --------------------------------------------------------------------------- #
# NIP-22 comments                                                             #
# --------------------------------------------------------------------------- #

def parse_comment(event: dict, expected_anchor: PluginAnchor) -> Optional[CommentEvent]:
    """Parse a ``kind:1111`` NIP-22 comment anchored to ``expected_anchor``.

    NIP-22 distinguishes the **root** scope (uppercase tags) from the
    **parent** of this specific reply (lowercase tags). For our use:

      - The root MUST be the plugin's listing coord, encoded in an
        uppercase ``A`` tag pointing at ``expected_anchor`` and an
        uppercase ``K`` tag equal to ``"30700"``.
      - Top-level comments have a lowercase ``a`` tag that mirrors
        the root. Replies have a lowercase ``e`` tag pointing at the
        parent comment and a lowercase ``k`` tag equal to ``"1111"``.

    Returns ``None`` if either constraint is broken.
    """
    if event.get("kind") != NIP22_COMMENT_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    event_id, author_pubkey, created_at = shared
    tags = _tags(event)

    # Root scope. The uppercase ``A`` must point at the expected anchor;
    # uppercase ``K`` must be the listing kind.
    upper_a = _find_first(tags, "A")
    if upper_a is None:
        return None
    root_anchor = _plugin_anchor_from_a_tag(upper_a)
    if root_anchor != expected_anchor:
        return None
    upper_k = _find_first(tags, "K")
    if upper_k is None or len(upper_k) < 2 or upper_k[1] != str(PLUGIN_LISTING_KIND):
        return None

    # Parent. If a lowercase ``e`` tag is present we treat the event as
    # a reply; the lowercase ``k`` must declare kind 1111 in that case.
    # If only a lowercase ``a`` tag is present (mirroring the root),
    # the comment is top-level.
    parent_event_id: Optional[str] = None
    parent_author_pubkey: Optional[str] = None

    lower_e = _find_first(tags, "e")
    if lower_e is not None:
        parent_event_id = _hex_event_id(lower_e[1] if len(lower_e) >= 2 else None)
        if parent_event_id is None:
            return None
        lower_k = _find_first(tags, "k")
        if lower_k is None or len(lower_k) < 2 or lower_k[1] != str(NIP22_COMMENT_KIND):
            return None
        # NIP-22 puts the parent author in the 4th slot of the e tag
        # (after id + relay hint).
        if len(lower_e) >= 4:
            parent_author_pubkey = _hex_pubkey(lower_e[3])
        if parent_author_pubkey is None:
            # Fall back to a sibling lowercase ``p`` tag if present.
            lower_p = _find_first(tags, "p")
            if lower_p is not None and len(lower_p) >= 2:
                parent_author_pubkey = _hex_pubkey(lower_p[1])
    else:
        # Top-level: there must be a lowercase ``a`` tag mirroring the root.
        lower_a = _find_first(tags, "a")
        if lower_a is None:
            return None
        if _plugin_anchor_from_a_tag(lower_a) != expected_anchor:
            return None

    content = event.get("content", "")
    if not isinstance(content, str):
        return None
    # Strip blank-only comments. Some clients ship a kind:1111 to ack a
    # reaction; without text there's nothing for us to render.
    if not content.strip():
        return None

    return CommentEvent(
        event_id=event_id,
        author_pubkey=author_pubkey,
        anchor=expected_anchor,
        content=content,
        created_at=created_at,
        parent_event_id=parent_event_id,
        parent_author_pubkey=parent_author_pubkey,
    )


# --------------------------------------------------------------------------- #
# NIP-57 zaps                                                                 #
# --------------------------------------------------------------------------- #

def parse_zap_receipt(
    event: dict,
    expected_anchor: PluginAnchor,
    *,
    expected_lnurl_pubkey: Optional[str] = None,
) -> Optional[ZapReceipt]:
    """Parse + validate a ``kind:9735`` zap receipt for the given anchor.

    Implements the strict per-receipt checklist from NIP-57:

      1. The receipt is kind 9735.
      2. Exactly one ``bolt11`` tag and exactly one ``description`` tag.
      3. The ``description`` value parses as a kind:9734 zap request
         JSON. (NIP-57 §"Zap Receipt": ``description`` MUST contain the
         JSON-encoded zap request.)
      4. The zap request targets ``expected_anchor`` via an ``a`` tag.
      5. The zap request's ``amount`` tag (msat) matches the bolt11's
         encoded amount. Amount-less bolt11s are accepted because BOLT-11
         lets a wallet mint "any amount" invoices.
      6. ``sha256(description_tag_value)`` equals the bolt11's tagged
         ``h`` description hash (NIP-57's binding between receipt and
         request). Receipts without an ``h`` field are rejected.
      7. When the caller supplies ``expected_lnurl_pubkey`` (the
         ``nostrPubkey`` advertised by the recipient's LNURL endpoint),
         the receipt's signer pubkey must equal it. This is the only
         way to know the receipt was minted by the lightning service we
         actually paid; relays cannot enforce it.

    Returns the receipt or ``None`` for any failure.
    """
    if event.get("kind") != NIP57_ZAP_RECEIPT_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    receipt_event_id, service_pubkey, created_at = shared
    tags = _tags(event)

    # NIP-57: exactly one bolt11 and one description tag.
    bolt11_tags = _find_all(tags, "bolt11")
    description_tags = _find_all(tags, "description")
    if len(bolt11_tags) != 1 or len(description_tags) != 1:
        return None
    bolt11 = bolt11_tags[0][1] if len(bolt11_tags[0]) >= 2 else ""
    raw_request = description_tags[0][1] if len(description_tags[0]) >= 2 else ""
    if not isinstance(bolt11, str) or not bolt11:
        return None
    if not isinstance(raw_request, str) or not raw_request:
        return None

    # Decode the bolt11. We need its amount (cross-check) + description
    # hash (binding). Anything malformed fails the receipt outright.
    try:
        bolt11_summary = decode_bolt11(bolt11)
    except Bolt11Error:
        return None
    if bolt11_summary.hex_description_hash is None:
        # NIP-57 requires the hash form; an inline-description bolt11
        # can't be cryptographically bound to the zap request.
        return None

    # Description binding: sha256(description-tag-value) == bolt11 h.
    request_hash = hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
    if request_hash != bolt11_summary.hex_description_hash.lower():
        return None

    # Parse the embedded zap request.
    try:
        zap_request = json.loads(raw_request)
    except (TypeError, ValueError):
        return None
    if not isinstance(zap_request, dict):
        return None
    if zap_request.get("kind") != NIP57_ZAP_REQUEST_KIND:
        return None

    request_tags = _tags(zap_request)

    # Anchor must match.
    anchor = _find_anchor_in_a_tags(request_tags, expected_anchor)
    if anchor is None:
        return None

    # Zap request signer.
    zapper_pubkey = _hex_pubkey(zap_request.get("pubkey"))
    if zapper_pubkey is None:
        return None

    # Amount in millisats per NIP-57. The bolt11's HRP must match the
    # zap request exactly — we deliberately reject amount-less bolt11s
    # in receipt context because they let a forger publish a 9735 for
    # any claimed amount.
    amount_tag = _find_first(request_tags, "amount")
    if amount_tag is None or len(amount_tag) < 2:
        return None
    try:
        amount_msat = int(amount_tag[1])
    except (TypeError, ValueError):
        return None
    if amount_msat < 0:
        return None
    if bolt11_summary.amount_msat is None:
        # Amount-less bolt11 + zap request amount = forgery surface.
        return None
    if bolt11_summary.amount_msat != amount_msat:
        return None
    amount_sats = amount_msat // 1000
    if amount_sats <= 0:
        return None

    # When the caller knows which LNURL service should have minted the
    # receipt (from a kind:0 / lightning-address resolution), enforce
    # that the receipt's signer matches. Without this check anyone can
    # fabricate "fake" 9735s for any plugin.
    if expected_lnurl_pubkey:
        if service_pubkey != expected_lnurl_pubkey.lower():
            return None

    content = zap_request.get("content", "")
    if not isinstance(content, str):
        content = ""

    return ZapReceipt(
        event_id=receipt_event_id,
        zapper_pubkey=zapper_pubkey,
        anchor=anchor,
        amount_sats=amount_sats,
        created_at=created_at,
        content=content,
    )


# --------------------------------------------------------------------------- #
# NIP-09 deletions                                                            #
# --------------------------------------------------------------------------- #

def parse_deletion(event: dict) -> Optional[DeletionRequest]:
    """Parse a ``kind:5`` deletion request.

    NIP-09 lets an author publish a deletion that references their own
    earlier events via ``e`` tags (or ``a`` tags for addressable events).
    The aggregator honors the deletion only when the deletion's author
    matches the target event's author.
    """
    if event.get("kind") != NIP09_DELETION_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    event_id, author_pubkey, created_at = shared
    tags = _tags(event)

    target_event_ids: List[str] = []
    for tag in _find_all(tags, "e"):
        eid = _hex_event_id(tag[1] if len(tag) >= 2 else None)
        if eid is not None:
            target_event_ids.append(eid)

    target_coords: List[str] = []
    for tag in _find_all(tags, "a"):
        if len(tag) >= 2 and isinstance(tag[1], str) and ":" in tag[1]:
            target_coords.append(tag[1])

    if not target_event_ids and not target_coords:
        return None

    return DeletionRequest(
        event_id=event_id,
        author_pubkey=author_pubkey,
        target_event_ids=tuple(target_event_ids),
        target_coords=tuple(target_coords),
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# NIP-51 mute lists                                                            #
# --------------------------------------------------------------------------- #

def parse_mute_list(event: dict) -> Optional[MuteList]:
    """Parse a NIP-51 mute list (kind ``10000``).

    Only the ``p`` tags (muted pubkeys) are surfaced. NIP-51 also
    supports ``t``, ``e`` and ``word`` entries but the aggregator
    doesn't use them yet.
    """
    if event.get("kind") != NIP51_MUTE_LIST_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    _event_id, owner, created_at = shared
    tags = _tags(event)

    muted: List[str] = []
    for tag in _find_all(tags, "p"):
        pk = _hex_pubkey(tag[1] if len(tag) >= 2 else None)
        if pk is not None:
            muted.append(pk)

    return MuteList(
        owner_pubkey=owner,
        muted_pubkeys=frozenset(muted),
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# NIP-02 contact lists                                                         #
# --------------------------------------------------------------------------- #

def parse_contact_list(event: dict) -> Optional[ContactList]:
    """Parse a NIP-02 contact list (kind ``3``).

    Only the ``p`` tags (follows) are surfaced. NIP-02 contact lists
    also carry a ``content`` field with relay hints that we don't use.
    """
    if event.get("kind") != NIP02_CONTACT_LIST_KIND:
        return None
    shared = _shared_event_fields(event)
    if shared is None:
        return None
    _event_id, owner, created_at = shared
    tags = _tags(event)

    follows: List[str] = []
    for tag in _find_all(tags, "p"):
        pk = _hex_pubkey(tag[1] if len(tag) >= 2 else None)
        if pk is not None:
            follows.append(pk)

    return ContactList(
        owner_pubkey=owner,
        follows=frozenset(follows),
        created_at=created_at,
    )
