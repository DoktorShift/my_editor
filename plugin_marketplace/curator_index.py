"""Read a curator's signed plugin index from a Nostr ``kind:30750`` event.

Schema (project convention, kept stable so curators can migrate
without breaking installed clients):

  ``kind == 30750``                    addressable curator index
  ``["d", "<curator-list-id>"]``       distinguishes lists for one curator
  ``["title", "<display name>"]``      optional
  ``["description", "<text>"]``        optional
  ``["a", "30700:<author>:<plugin>", "<relay-hint>"]``   one per entry
  ``["t", "<topic>"]``                 optional repeated

For each ``a`` tag the marketplace then fetches the referenced
kind:30700 plugin listing event and parses it into a ``PluginListing``.
The combination of curator signature + per-plugin signed listing gives
us trust-on-first-fetch for the curator, plus protocol-level integrity
for each plugin payload.

This module never opens its own sockets; it takes a relay-pool
``fetch_latest_event`` helper and rides the existing routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from nostr.bech32 import bech32_decode, convertbits
from nostr.events import verify_event
from nostr.queries import fetch_latest_event

from .models import PluginListing, RegistryParseError
from .social.aggregator import EngagementAggregator
from .social.models import PluginAnchor
from .social.parser import (
    NIP09_DELETION_KIND,
    PLUGIN_LISTING_KIND,
    parse_deletion,
)


CURATOR_INDEX_KIND: int = 30750


# --------------------------------------------------------------------------- #
# naddr decode (curator list pointer)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CuratorIndexAddress:
    """A decoded ``naddr`` pointing at a curator's kind:30750 list.

    ``relay_hints`` may be empty — the fetcher falls back to seed
    relays in that case.
    """

    kind: int
    author_pubkey: str
    identifier: str           # the d-tag
    relay_hints: tuple[str, ...]


def decode_curator_naddr(naddr: str) -> CuratorIndexAddress:
    """Decode an ``naddr1...`` into its TLV components.

    Mirrors NIP-19: type 0 = d-tag, type 1 = relay, type 2 = author,
    type 3 = kind (uint32 big-endian).
    """
    hrp, data = bech32_decode(naddr)
    if hrp != "naddr":
        raise RegistryParseError(f"expected naddr, got {hrp!r}")
    raw_bytes = bytes(convertbits(data, 5, 8, False))
    cursor = 0
    identifier = ""
    author_pubkey = ""
    kind: Optional[int] = None
    relays: List[str] = []
    while cursor + 2 <= len(raw_bytes):
        tlv_type = raw_bytes[cursor]
        length = raw_bytes[cursor + 1]
        cursor += 2
        value = raw_bytes[cursor : cursor + length]
        cursor += length
        if tlv_type == 0:
            identifier = value.decode("utf-8", errors="replace")
        elif tlv_type == 1:
            relays.append(value.decode("utf-8", errors="replace"))
        elif tlv_type == 2:
            if len(value) == 32:
                author_pubkey = value.hex()
        elif tlv_type == 3:
            if len(value) == 4:
                kind = int.from_bytes(value, "big")
    if not author_pubkey or kind is None:
        raise RegistryParseError("naddr missing required TLV fields")
    return CuratorIndexAddress(
        kind=kind,
        author_pubkey=author_pubkey.lower(),
        identifier=identifier,
        relay_hints=tuple(relays),
    )


# --------------------------------------------------------------------------- #
# Curator event -> plugin anchors
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CuratorIndex:
    """Parsed kind:30750 event ready for the marketplace to expand."""

    curator_pubkey: str
    identifier: str
    title: str
    description: str
    topics: tuple[str, ...]
    anchors: tuple[PluginAnchor, ...]
    relay_hints: tuple[str, ...]


def parse_curator_index(event: dict) -> Optional[CuratorIndex]:
    """Validate + parse a curator index event.

    Returns ``None`` if the event is malformed; the fetcher treats
    that the same as "no listings here." We verify the signature
    before trusting any tag.
    """
    if not isinstance(event, dict) or event.get("kind") != CURATOR_INDEX_KIND:
        return None
    if not verify_event(event):
        return None
    pubkey = (event.get("pubkey") or "").lower()
    if len(pubkey) != 64:
        return None
    identifier = ""
    title = ""
    description = ""
    topics: List[str] = []
    anchors: List[PluginAnchor] = []
    relay_hints: List[str] = []
    for tag in event.get("tags", []) or []:
        if not isinstance(tag, list) or not tag:
            continue
        head = tag[0]
        if head == "d" and len(tag) >= 2:
            identifier = str(tag[1])
        elif head == "title" and len(tag) >= 2:
            title = str(tag[1])
        elif head == "description" and len(tag) >= 2:
            description = str(tag[1])
        elif head == "t" and len(tag) >= 2:
            topics.append(str(tag[1]))
        elif head == "a" and len(tag) >= 2:
            anchor = PluginAnchor.parse(str(tag[1]))
            if anchor is not None and anchor.kind == PLUGIN_LISTING_KIND:
                anchors.append(anchor)
                if len(tag) >= 3 and isinstance(tag[2], str) and tag[2].strip():
                    relay_hints.append(tag[2].strip())
        elif head == "r" and len(tag) >= 2 and isinstance(tag[1], str):
            relay_hints.append(tag[1].strip())
    if not anchors:
        return None
    return CuratorIndex(
        curator_pubkey=pubkey,
        identifier=identifier,
        title=title,
        description=description,
        topics=tuple(topics),
        anchors=tuple(anchors),
        relay_hints=tuple(dict.fromkeys(relay_hints)),
    )


# --------------------------------------------------------------------------- #
# Listing event -> PluginListing
# --------------------------------------------------------------------------- #

def parse_listing_event(
    event: dict,
    *,
    anchor: PluginAnchor,
    source_name: str,
    listing_relay_hint: str = "",
) -> Optional[PluginListing]:
    """Convert a kind:30700 event into a ``PluginListing``.

    Tag layout mirrors the publisher's ``publish_listing`` output.
    Required fields (``name``, ``download`` URL + sha256) are
    enforced; missing required fields cause us to skip the entry
    rather than raise, so one bad plugin in a curator's index doesn't
    suppress the rest.
    """
    if event.get("kind") != PLUGIN_LISTING_KIND:
        return None
    if not verify_event(event):
        return None
    if (event.get("pubkey") or "").lower() != anchor.author_pubkey:
        return None
    fields: Dict[str, str] = {}
    download_url = ""
    sha256 = ""
    topics: List[str] = []
    screenshots: List[str] = []
    matching_d = False
    for tag in event.get("tags", []) or []:
        if not isinstance(tag, list) or not tag:
            continue
        head = tag[0]
        if head == "d" and len(tag) >= 2:
            matching_d = (str(tag[1]) == anchor.plugin_id)
            fields["d"] = str(tag[1])
        elif head in ("name", "version", "description", "summary",
                      "homepage", "license", "zap", "price") and len(tag) >= 2:
            fields[head] = str(tag[1])
        elif head == "t" and len(tag) >= 2:
            topics.append(str(tag[1]))
        elif head == "image" and len(tag) >= 2:
            screenshots.append(str(tag[1]))
        elif head == "download" and len(tag) >= 3:
            download_url = str(tag[1])
            sha256 = str(tag[2])
    if not matching_d:
        return None
    if not fields.get("name") or not download_url or not sha256:
        return None
    try:
        price_sats = int(fields.get("price", "0")) if fields.get("price") else 0
    except ValueError:
        price_sats = 0
    return PluginListing(
        plugin_id=anchor.plugin_id,
        name=fields["name"],
        version=fields.get("version", ""),
        download_url=download_url,
        sha256=sha256,
        author="",  # curators don't carry pet-name; the social panel resolves it
        description=fields.get("description", ""),
        long_description=fields.get("summary", ""),
        homepage=fields.get("homepage", ""),
        license=fields.get("license", ""),
        tags=tuple(topics),
        price_sats=price_sats,
        lightning_address=fields.get("zap", ""),
        screenshots=tuple(screenshots),
        source_name=source_name,
        nostr_naddr=_encode_naddr_listing(anchor, listing_relay_hint),
    )


# --------------------------------------------------------------------------- #
# Async resolution (relay-backed)
# --------------------------------------------------------------------------- #

def fetch_curator_index(
    *,
    pool,
    address: CuratorIndexAddress,
    seed_relays: List[str],
    on_done: Callable[[Optional[CuratorIndex]], None],
    timeout_ms: int = 8_000,
) -> None:
    """Fetch the most recent kind:30750 event for ``address``.

    Calls ``on_done`` with the parsed ``CuratorIndex`` or ``None`` if
    nothing matching was found within the timeout.
    """
    relays = _dedupe([*address.relay_hints, *seed_relays])

    def _on_event(event: Optional[dict]) -> None:
        if event is None:
            on_done(None)
            return
        parsed = parse_curator_index(event)
        on_done(parsed)

    fetch_latest_event(
        pool, relays,
        filters=[{
            "kinds": [CURATOR_INDEX_KIND],
            "authors": [address.author_pubkey],
            "#d": [address.identifier] if address.identifier else None,
            "limit": 1,
        }],
        on_done=_on_event,
        timeout_ms=timeout_ms,
    )


def fetch_listings_for_anchors(
    *,
    pool,
    anchors: List[PluginAnchor],
    seed_relays: List[str],
    source_name: str,
    on_done: Callable[[List[PluginListing]], None],
    timeout_ms: int = 8_000,
) -> None:
    """Fetch every plugin listing the curator references.

    Two pass strategy:

      1. Fetch the latest kind:30700 per author (one REQ per author,
         with ``#d`` narrowing to the d-tags the curator named).
      2. Fetch any NIP-09 ``kind:5`` deletions authored by the same
         author whose ``#a`` filter matches the curator's coord set.
      3. Apply the deletions before handing the listings back: a
         tombstoned listing is dropped from the result entirely so a
         creator who deleted their plugin never sees it resurface from
         a cached curator index.

    ``on_done`` fires exactly once. Concurrent relay callbacks are
    serialised on the dialog's event loop, so the pending counter is
    safe to mutate without a lock.
    """
    if not anchors:
        on_done([])
        return
    # Curators with malicious or runaway lists could otherwise force a
    # massive fan-out of REQs. 500 entries is comfortably more than
    # any legitimate curator would ship; trim with a deterministic
    # head() so the surfaced subset stays consistent across reloads.
    _MAX_CURATOR_ENTRIES = 500
    if len(anchors) > _MAX_CURATOR_ENTRIES:
        anchors = anchors[:_MAX_CURATOR_ENTRIES]
    raw_listings: Dict[str, dict] = {}    # coord -> raw event
    deletions: List[dict] = []
    by_author: Dict[str, List[PluginAnchor]] = {}
    for anchor in anchors:
        by_author.setdefault(anchor.author_pubkey, []).append(anchor)

    # 2 REQs per author (listing + deletion). Both must complete before
    # we hand the result to the caller; using a single counter for both
    # phases keeps the bookkeeping trivial.
    pending = len(by_author) * 2
    done_emitted = [False]

    def _emit_done() -> None:
        if done_emitted[0]:
            return
        done_emitted[0] = True
        # Apply deletions: per-author authority means we only honor a
        # kind:5 whose pubkey matches the listing it targets.
        listings: List[PluginListing] = []
        for coord, event in raw_listings.items():
            anchor = next(
                (a for a in anchors if a.coord == coord),
                None,
            )
            if anchor is None:
                continue
            if _is_tombstoned(
                deletions, coord=coord,
                author=anchor.author_pubkey,
                listing_created_at=int(event.get("created_at", 0)),
            ):
                continue
            listing = parse_listing_event(
                event, anchor=anchor, source_name=source_name,
            )
            if listing is not None:
                listings.append(listing)
        on_done(listings)

    def _step(value: int = 1) -> None:
        nonlocal pending
        pending -= value
        if pending <= 0:
            _emit_done()

    def _on_listing(event: Optional[dict], *, author_anchors: List[PluginAnchor]) -> None:
        if event is not None:
            d_tag = next(
                (t[1] for t in event.get("tags", []) if isinstance(t, list) and t and t[0] == "d"),
                None,
            )
            anchor = next(
                (a for a in author_anchors if a.plugin_id == d_tag),
                None,
            )
            if anchor is not None:
                raw_listings[anchor.coord] = event
        _step()

    def _on_deletion(event: Optional[dict]) -> None:
        if event is not None:
            deletions.append(event)
        _step()

    for author, author_anchors in by_author.items():
        d_values = [a.plugin_id for a in author_anchors]
        coords = [a.coord for a in author_anchors]
        fetch_latest_event(
            pool, seed_relays,
            filters=[{
                "kinds": [PLUGIN_LISTING_KIND],
                "authors": [author],
                "#d": d_values,
                "limit": len(d_values),
            }],
            on_done=lambda e, aa=author_anchors: _on_listing(e, author_anchors=aa),
            timeout_ms=timeout_ms,
        )
        fetch_latest_event(
            pool, seed_relays,
            filters=[{
                "kinds": [NIP09_DELETION_KIND],
                "authors": [author],
                "#a": coords,
                "limit": len(coords),
            }],
            on_done=_on_deletion,
            timeout_ms=timeout_ms,
        )


def _is_tombstoned(
    raw_deletion_events: List[dict],
    *,
    coord: str,
    author: str,
    listing_created_at: int,
) -> bool:
    """``True`` when a valid NIP-09 deletion tombstones ``coord``.

    Only deletions authored by ``author`` count (NIP-09's same-author
    rule). The deletion's ``created_at`` must be >= the listing's, per
    "wipes every version up to created_at."
    """
    parsed: List = []
    for event in raw_deletion_events:
        deletion = parse_deletion(event)
        if deletion is None:
            continue
        if deletion.author_pubkey != author:
            continue
        if coord not in deletion.target_coords:
            continue
        parsed.append(deletion)
    if not parsed:
        return False
    return EngagementAggregator.is_listing_deleted(
        parsed, coord=coord, author_pubkey=author,
        event_created_at=listing_created_at,
    )


# --------------------------------------------------------------------------- #
# naddr encoding for the resolved listing
# --------------------------------------------------------------------------- #

def _encode_naddr_listing(anchor: PluginAnchor, relay_hint: str = "") -> str:
    """Build a NIP-19 ``naddr`` pointing at the listing.

    Used so the marketplace's "View on Nostr" button works when the
    user opens a curator-discovered plugin.
    """
    from nostr.bech32 import bech32_encode, convertbits

    parts: List[bytes] = []
    if anchor.plugin_id:
        d_bytes = anchor.plugin_id.encode("utf-8")
        parts.append(bytes([0, len(d_bytes)]) + d_bytes)
    if relay_hint:
        relay_bytes = relay_hint.encode("utf-8")
        parts.append(bytes([1, len(relay_bytes)]) + relay_bytes)
    pubkey_bytes = bytes.fromhex(anchor.author_pubkey)
    parts.append(bytes([2, len(pubkey_bytes)]) + pubkey_bytes)
    kind_bytes = anchor.kind.to_bytes(4, "big")
    parts.append(bytes([3, len(kind_bytes)]) + kind_bytes)
    payload = b"".join(parts)
    data = convertbits(list(payload), 8, 5, True)
    return bech32_encode("naddr", data)


def _dedupe(urls: List[str]) -> List[str]:
    seen: Dict[str, None] = {}
    out: List[str] = []
    for url in urls:
        if not url:
            continue
        key = url.strip().rstrip("/").lower()
        if key in seen:
            continue
        seen[key] = None
        out.append(url.strip())
    return out
