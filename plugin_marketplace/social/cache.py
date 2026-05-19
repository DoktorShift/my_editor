"""SQLite cache for engagement events + derived snapshots.

Two tables:

  - ``engagement_event`` holds the raw events (one row per Nostr
    event id). Filter toggles in the UI recompute the snapshot from
    this table, so flipping "verified only" is local and instant.
  - ``plugin_aggregate`` holds the most recent snapshot's headline
    figures (rating average, count, comment count, zap sats, when
    we last refreshed). Used by the marketplace grid so list rows
    can show "★ 4.8 (47)" without subscribing to every plugin.

Also stored:

  - ``nip05_verification`` rows for each ``(pubkey, identifier)``
    pair we have checked; TTL is enforced when reading.
  - ``profile_metadata`` rows for kind:0 metadata snippets we need
    to render comments (display name, picture URL, nip05 claim).

This module never touches Qt or the network. The fetcher writes
into it; the aggregator reads from it; the UI consumes the
aggregator's output.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

from .models import (
    CommentEvent,
    DeletionRequest,
    EngagementSnapshot,
    Nip05Verification,
    PluginAnchor,
    RatingEvent,
    ZapReceipt,
)
from .parser import (
    NIP09_DELETION_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    parse_comment,
    parse_deletion,
    parse_rating,
    parse_zap_receipt,
)


# --------------------------------------------------------------------------- #
# Schema                                                                      #
# --------------------------------------------------------------------------- #

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS engagement_event (
    event_id        TEXT PRIMARY KEY,
    plugin_anchor   TEXT NOT NULL,
    kind            INTEGER NOT NULL,
    pubkey          TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    raw_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS engagement_event_by_anchor
    ON engagement_event (plugin_anchor, kind);
CREATE INDEX IF NOT EXISTS engagement_event_by_author
    ON engagement_event (pubkey);

CREATE TABLE IF NOT EXISTS plugin_aggregate (
    plugin_anchor TEXT PRIMARY KEY,
    rating_avg REAL NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    rating_distribution TEXT NOT NULL DEFAULT '[0,0,0,0,0]',
    comment_count INTEGER NOT NULL DEFAULT 0,
    zaps_sats INTEGER NOT NULL DEFAULT 0,
    zap_count INTEGER NOT NULL DEFAULT 0,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS nip05_verification (
    pubkey TEXT NOT NULL,
    identifier TEXT NOT NULL,
    verified INTEGER,             -- 1 = true, 0 = false, NULL = pending
    checked_at INTEGER,
    PRIMARY KEY (pubkey, identifier)
);

CREATE TABLE IF NOT EXISTS profile_metadata (
    pubkey      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    picture_url TEXT NOT NULL DEFAULT '',
    nip05       TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL
);

-- LNURL nostrPubkey resolution per lightning address. NIP-57 zap
-- receipts can only be trusted when their signer matches the
-- nostrPubkey advertised by the recipient's LNURL endpoint. We
-- cache that resolution per address so receipts parse strictly even
-- when the dialog isn't open.
CREATE TABLE IF NOT EXISTS lnurl_pubkey (
    lightning_address TEXT PRIMARY KEY,
    nostr_pubkey      TEXT NOT NULL,
    updated_at        INTEGER NOT NULL
);
"""


# Aggregate freshness: 5 minutes. Read paths refresh in the background
# when the row is older. Tight enough to feel live, loose enough that
# scrolling the grid doesn't slam every relay.
_AGGREGATE_TTL_S: int = 5 * 60

# NIP-05 verification TTL: 24 hours. The DNS check is cheap but we
# don't want to re-fetch every time the user opens the marketplace.
_NIP05_TTL_S: int = 24 * 60 * 60


# --------------------------------------------------------------------------- #
# Cache                                                                       #
# --------------------------------------------------------------------------- #

class EngagementCache:
    """Thin wrapper around a SQLite file holding the social cache.

    A single instance is shared across the marketplace UI lifetime;
    the fetcher writes, the aggregator + UI read. Per-call connection
    management keeps the wrapper threadsafe enough for our workers
    without needing a connection pool.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Per-call short-lived connection.

        Using one shared long-lived connection would force every UI
        and worker thread to fight for the same write lock. Opening
        a fresh connection per call gives SQLite's WAL mode room to
        let readers and writers proceed independently.
        """
        conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,    # autocommit; we wrap explicit txns when needed
            timeout=5.0,
        )
        try:
            yield conn
        finally:
            conn.close()

    # ----------------------------------------------------------------------
    # Raw events
    # ----------------------------------------------------------------------

    def upsert_event(self, *, anchor: PluginAnchor, event: dict) -> None:
        """Insert or replace one raw event row.

        Idempotent on ``event_id`` so re-receiving an event from a
        second relay is a no-op. We store the raw JSON so the
        aggregator can re-parse with stricter rules in a later version
        without needing a re-fetch.
        """
        event_id = event.get("id")
        kind = event.get("kind")
        pubkey = event.get("pubkey")
        created_at = event.get("created_at")
        if not isinstance(event_id, str) or not isinstance(kind, int):
            return
        if not isinstance(pubkey, str) or not isinstance(created_at, int):
            return
        raw = json.dumps(event, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO engagement_event"
                " (event_id, plugin_anchor, kind, pubkey, created_at, raw_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, anchor.coord, kind, pubkey, created_at, raw),
            )

    def load_events(
        self,
        anchor: PluginAnchor,
        *,
        kinds: Optional[Iterable[int]] = None,
    ) -> List[dict]:
        """Return raw events stored under ``anchor`` (optionally filtered by kind).

        Events come back as the parsed-JSON dicts the parser layer
        expects. Ordering is by ``created_at`` ascending so the
        aggregator processes older events first; thread-building
        works either way but a stable order makes tests trivial.
        """
        sql = (
            "SELECT raw_json FROM engagement_event"
            " WHERE plugin_anchor = ?"
        )
        params: List = [anchor.coord]
        if kinds is not None:
            placeholders = ",".join("?" * len(list(kinds)))
            # Re-materialize since we just consumed the iterator above.
            kinds_list = list(kinds)
            placeholders = ",".join("?" * len(kinds_list))
            sql += f" AND kind IN ({placeholders})"
            params.extend(kinds_list)
        sql += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: List[dict] = []
        for (raw,) in rows:
            try:
                out.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return out

    def event_kind(self, anchor: PluginAnchor, event_id: str) -> Optional[int]:
        """Return the kind of one cached event under ``anchor``, or
        ``None`` when no row matches.

        Used by the deletion path so the publisher can emit the
        correct advisory ``["k", "<kind>"]`` tag without the dialog
        having to re-parse the raw JSON.
        """
        if not event_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT kind FROM engagement_event"
                " WHERE plugin_anchor = ? AND event_id = ?",
                (anchor.coord, event_id),
            ).fetchone()
        if row is None:
            return None
        return int(row[0])

    def parsed_for_anchor(
        self,
        anchor: PluginAnchor,
        *,
        expected_lnurl_pubkey: Optional[str] = None,
    ) -> Tuple[List[RatingEvent], List[CommentEvent], List[ZapReceipt], List[DeletionRequest]]:
        """Re-parse every cached event for ``anchor`` into typed records.

        Called by the aggregator when it needs to recompute a snapshot.
        Parsing is cheap (no hashing, no signature verification here)
        and centralizes the strictness rules in one place.

        ``expected_lnurl_pubkey`` is forwarded to ``parse_zap_receipt``
        for the NIP-57 strict-mode receipt check (receipt signer ==
        LNURL service's advertised ``nostrPubkey``). Pass ``None`` to
        run the looser parse that accepts any signer; the strict mode
        is preferred whenever the lightning address has been resolved.
        """
        events = self.load_events(anchor)
        ratings: List[RatingEvent] = []
        comments: List[CommentEvent] = []
        zaps: List[ZapReceipt] = []
        deletions: List[DeletionRequest] = []
        for event in events:
            kind = event.get("kind")
            if kind == NIP32_LABEL_KIND:
                rating = parse_rating(event, anchor)
                if rating is not None:
                    ratings.append(rating)
            elif kind == NIP22_COMMENT_KIND:
                comment = parse_comment(event, anchor)
                if comment is not None:
                    comments.append(comment)
            elif kind == NIP57_ZAP_RECEIPT_KIND:
                zap = parse_zap_receipt(
                    event, anchor,
                    expected_lnurl_pubkey=expected_lnurl_pubkey,
                )
                if zap is not None:
                    zaps.append(zap)
            elif kind == NIP09_DELETION_KIND:
                deletion = parse_deletion(event)
                if deletion is not None:
                    deletions.append(deletion)
        return ratings, comments, zaps, deletions

    # ----------------------------------------------------------------------
    # Aggregate snapshots
    # ----------------------------------------------------------------------

    def store_snapshot(self, snapshot: EngagementSnapshot) -> None:
        """Persist the headline figures so list rows can render them."""
        distribution = [
            snapshot.distribution.one,
            snapshot.distribution.two,
            snapshot.distribution.three,
            snapshot.distribution.four,
            snapshot.distribution.five,
        ]
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plugin_aggregate"
                " (plugin_anchor, rating_avg, rating_count, rating_distribution,"
                "  comment_count, zaps_sats, zap_count, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.anchor.coord,
                    float(snapshot.distribution.average),
                    snapshot.distribution.total,
                    json.dumps(distribution),
                    snapshot.comment_count,
                    snapshot.zaps_sats,
                    snapshot.zap_count,
                    int(time.time()),
                ),
            )

    def aggregate(self, anchor: PluginAnchor) -> Optional[dict]:
        """Return the cached headline figures for ``anchor`` or ``None``.

        Stale rows are still returned: the caller decides whether to
        trust them, refresh in the background, or both. Use
        ``aggregate_is_fresh`` to check freshness without re-reading.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rating_avg, rating_count, rating_distribution,"
                " comment_count, zaps_sats, zap_count, fetched_at"
                " FROM plugin_aggregate WHERE plugin_anchor = ?",
                (anchor.coord,),
            ).fetchone()
        if row is None:
            return None
        rating_avg, rating_count, dist_json, comment_count, zaps_sats, zap_count, fetched_at = row
        try:
            distribution = json.loads(dist_json)
        except (TypeError, ValueError):
            distribution = [0, 0, 0, 0, 0]
        return {
            "rating_avg": rating_avg,
            "rating_count": rating_count,
            "distribution": distribution,
            "comment_count": comment_count,
            "zaps_sats": zaps_sats,
            "zap_count": zap_count,
            "fetched_at": fetched_at,
        }

    def aggregate_is_fresh(self, anchor: PluginAnchor) -> bool:
        agg = self.aggregate(anchor)
        if agg is None:
            return False
        return (int(time.time()) - int(agg["fetched_at"])) < _AGGREGATE_TTL_S

    # ----------------------------------------------------------------------
    # NIP-05 verification
    # ----------------------------------------------------------------------

    def get_nip05(self, pubkey: str, identifier: str) -> Optional[Nip05Verification]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT verified, checked_at FROM nip05_verification"
                " WHERE pubkey = ? AND identifier = ?",
                (pubkey, identifier),
            ).fetchone()
        if row is None:
            return None
        verified_raw, checked_at = row
        if verified_raw is None:
            return Nip05Verification(
                pubkey=pubkey, identifier=identifier,
                verified=None, checked_at=checked_at,
            )
        return Nip05Verification(
            pubkey=pubkey, identifier=identifier,
            verified=bool(verified_raw), checked_at=checked_at,
        )

    def store_nip05(self, verification: Nip05Verification) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO nip05_verification"
                " (pubkey, identifier, verified, checked_at) VALUES (?, ?, ?, ?)",
                (
                    verification.pubkey,
                    verification.identifier,
                    None if verification.verified is None else (1 if verification.verified else 0),
                    verification.checked_at,
                ),
            )

    def nip05_is_fresh(self, verification: Nip05Verification) -> bool:
        if verification.checked_at is None:
            return False
        return (int(time.time()) - int(verification.checked_at)) < _NIP05_TTL_S

    def verified_pubkeys(self) -> List[str]:
        """Return every pubkey known to be NIP-05 verified AND fresh.

        Honours the same TTL the per-key ``nip05_is_fresh`` check
        uses, so a verification that expired isn't laundered into the
        trust filter. A pubkey that lost its NIP-05 record between
        verifications stops being treated as verified within the TTL.
        """
        cutoff = int(time.time()) - _NIP05_TTL_S
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT pubkey FROM nip05_verification"
                " WHERE verified = 1 AND checked_at IS NOT NULL"
                " AND checked_at >= ?",
                (cutoff,),
            ).fetchall()
        return [r[0] for r in rows]

    # ----------------------------------------------------------------------
    # Profile metadata
    # ----------------------------------------------------------------------

    def store_profile(
        self,
        pubkey: str,
        *,
        display_name: str = "",
        picture_url: str = "",
        nip05: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO profile_metadata"
                " (pubkey, display_name, picture_url, nip05, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (pubkey, display_name, picture_url, nip05, int(time.time())),
            )

    def get_profile(self, pubkey: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT display_name, picture_url, nip05, updated_at"
                " FROM profile_metadata WHERE pubkey = ?",
                (pubkey,),
            ).fetchone()
        if row is None:
            return None
        display_name, picture_url, nip05, updated_at = row
        return {
            "display_name": display_name,
            "picture_url": picture_url,
            "nip05": nip05,
            "updated_at": updated_at,
        }

    # ----------------------------------------------------------------------
    # LNURL nostrPubkey resolution (per lightning address)
    # ----------------------------------------------------------------------

    def store_lnurl_pubkey(self, lightning_address: str, nostr_pubkey: str) -> None:
        """Remember the LNURL service's nostrPubkey for a lightning address.

        Used by the NIP-57 receipt parser to verify the signer of a
        kind:9735 actually is the service we paid. ``nostr_pubkey`` is
        stored lowercased so comparison stays exact.
        """
        address = lightning_address.strip().lower()
        if not address:
            return
        pubkey = nostr_pubkey.strip().lower()
        if len(pubkey) != 64:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lnurl_pubkey"
                " (lightning_address, nostr_pubkey, updated_at)"
                " VALUES (?, ?, ?)",
                (address, pubkey, int(time.time())),
            )

    def get_lnurl_pubkey(self, lightning_address: str) -> Optional[str]:
        """Return the cached LNURL nostrPubkey, or ``None`` when unresolved."""
        address = lightning_address.strip().lower()
        if not address:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT nostr_pubkey FROM lnurl_pubkey WHERE lightning_address = ?",
                (address,),
            ).fetchone()
        return row[0] if row else None

    # ----------------------------------------------------------------------
    # Maintenance
    # ----------------------------------------------------------------------

    def purge_anchor(self, anchor: PluginAnchor) -> None:
        """Drop every cached row for one plugin.

        Useful when a plugin is uninstalled; we keep the user's
        verified-pubkeys cache because they likely apply to other
        plugins too.
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM engagement_event WHERE plugin_anchor = ?",
                (anchor.coord,),
            )
            conn.execute(
                "DELETE FROM plugin_aggregate WHERE plugin_anchor = ?",
                (anchor.coord,),
            )
