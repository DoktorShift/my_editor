# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The imports inbox of one account, kept on this computer (SQLite).

What the shared list of sources does not hold lives here, per account,
in ``~/.config/my_editor/imports/<pubkey>.sqlite3``: each source's check
times and health, the posts found on it, what the person did with each
post, a ledger of what was imported, and the import jobs with their
rows. None of it is synced: EINUNDZWANZIG STANDUP keeps the same things
on its own server, and only what both apps can see on Nostr (drafts,
articles, deletions) decides what counts as imported in both.

A post's state is the one STANDUP uses:

- ``new``: found by a check after the source was followed (the Inbox),
- ``older``: already on the source when it was followed, or published
  before that (Older Posts),
- ``skipped``: set aside by the person (Skipped; Undo brings it back),
- ``drafted`` / ``published`` / ``removed``: a draft or an article with
  its identifier exists, or existed and was deleted (Imported). Once a
  post is here it never goes back to the Inbox: a deleted draft is
  never offered again.

The rules come from STANDUP's server (backend/core/connected_sources.py)
so both inboxes fill the same way: the first check of a source is a
baseline (every post ``older``), a post already stored is never updated,
a post dated more than five minutes ahead waits for a later check, and a
source holds at most 5000 posts. Skip and Undo carry a revision, so an
Undo cannot bring back a post that was imported in the meantime.

Every method runs on the caller's thread; the database is small and
each call is one short transaction. ``clock`` is injectable for tests.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from i18n import _

from ..rss.parser import FeedItem
from . import snapshots

SCHEMA_VERSION = 1
CHECK_INTERVAL = 15 * 60
MAX_BACKOFF = 24 * 60 * 60
MAX_STORED_POSTS = 5000
MAX_BODY_BYTES = 256 * 1024
FUTURE_GRACE = 5 * 60
CHECK_LEASE = 120
MANUAL_CHECK_GAP = 60
PAGE_SIZE = 50
FINISHED_JOB_DAYS = 7

NEW, OLDER, SKIPPED = "new", "older", "skipped"
DRAFTED, PUBLISHED, REMOVED = "drafted", "published", "removed"
IMPORTED_STATES = (DRAFTED, PUBLISHED, REMOVED)

# The smart lists of the sidebar.
INBOX, OLDER_POSTS, IMPORTED, SKIPPED_POSTS = "inbox", "older", "imported", "skipped"

# Job and row statuses (STANDUP's import-jobs.store.js).
JOB_UNFINISHED = ("paused", "running", "pausing", "stopping")
JOB_FINISHED = ("stopped", "partial", "completed")
ROW_PENDING, ROW_DONE, ROW_EXISTING, ROW_FAILED = "pending", "done", "existing", "failed"

INBOX_FULL = _(
    "This source has reached its inbox limit. Unsubscribe and follow it again to "
    "start a fresh inbox; your drafts are kept.")


class Conflict(Exception):
    """A post changed since it was read (another action got there first)."""


@dataclass(frozen=True)
class SourceRow:
    key: str
    url: str
    title: str
    automatic: bool
    created_at: int
    last_checked: int = 0
    last_attempt: int = 0
    next_check: int = 0
    failures: int = 0
    error: str = ""
    paused: bool = False
    etag: str = ""
    last_modified: str = ""
    final_url: str = ""
    site_url: str = ""
    feed_title: str = ""
    new_count: int = 0
    older_count: int = 0

    @property
    def display_title(self) -> str:
        return self.title or self.feed_title or self.url


@dataclass(frozen=True)
class PostRow:
    """A post as a list shows it (no body)."""

    source_key: str
    d_tag: str
    title: str
    excerpt: str
    image: str
    link: str
    author: str
    published_at: int
    found_at: int
    state: str
    revision: int
    read_minutes: int
    image_count: int
    source_title: str = ""
    source_url: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.source_key, self.d_tag)


@dataclass(frozen=True)
class Counts:
    inbox: int = 0
    older: int = 0
    imported: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class IngestResult:
    new: int = 0
    older: int = 0
    waiting: int = 0
    error: str = ""


@dataclass(frozen=True)
class View:
    """What a list shows: a smart list, or one source's new or older posts."""

    scope: str                    # INBOX | OLDER_POSTS | IMPORTED | SKIPPED_POSTS | "source"
    source_key: str = ""
    source_state: str = NEW       # for a source: NEW or OLDER


@dataclass
class JobRow:
    index: int
    d_tag: str
    title: str
    source_key: str = ""
    status: str = ROW_PENDING
    stage: str = ""
    error: str = ""
    signed_event: Optional[dict] = None
    snapshot: Optional[dict] = None


@dataclass
class Job:
    id: str
    label: str
    source_type: str              # "inbox" | "feed" | "file" | "link"
    source_url: str
    status: str
    options: dict
    created_at: int
    updated_at: int
    error: str = ""
    rows: List[JobRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    def count(self, *statuses: str) -> int:
        return sum(1 for row in self.rows if row.status in statuses)

    @property
    def finished(self) -> bool:
        return self.status in JOB_FINISHED


_SCHEMA = """
CREATE TABLE sources (key TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  automatic INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
  last_checked INTEGER NOT NULL DEFAULT 0, last_attempt INTEGER NOT NULL DEFAULT 0,
  next_check INTEGER NOT NULL DEFAULT 0, checking_until INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
  paused INTEGER NOT NULL DEFAULT 0, etag TEXT NOT NULL DEFAULT '',
  last_modified TEXT NOT NULL DEFAULT '', final_url TEXT NOT NULL DEFAULT '',
  site_url TEXT NOT NULL DEFAULT '', feed_title TEXT NOT NULL DEFAULT '');
CREATE TABLE items (source_key TEXT NOT NULL, d_tag TEXT NOT NULL, snapshot TEXT NOT NULL,
  found_at INTEGER NOT NULL, published_at INTEGER NOT NULL DEFAULT 0, position TEXT NOT NULL,
  state TEXT NOT NULL, previous_state TEXT NOT NULL DEFAULT 'new',
  revision INTEGER NOT NULL DEFAULT 1, title TEXT NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
  read_minutes INTEGER NOT NULL DEFAULT 0, image_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (source_key, d_tag));
CREATE INDEX idx_items_page ON items (state, position DESC, source_key DESC, d_tag DESC);
CREATE INDEX idx_items_d ON items (d_tag);
CREATE TABLE ledger (d_tag TEXT PRIMARY KEY, state TEXT NOT NULL, at INTEGER NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, label TEXT NOT NULL, source_type TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '');
CREATE TABLE job_rows (job_id TEXT NOT NULL, idx INTEGER NOT NULL, d_tag TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', source_key TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  signed_event TEXT, snapshot TEXT, PRIMARY KEY (job_id, idx));
"""


def database_path(config_dir: Path, pubkey: str) -> Path:
    return Path(config_dir) / "imports" / f"{pubkey.lower()}.sqlite3"


class InboxStore:
    """One account's inbox. ``path`` ``:memory:`` keeps it in memory (tests)."""

    def __init__(self, path, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=5)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        self._db.close()

    def _now(self) -> int:
        return int(self._clock())

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._db.executescript("BEGIN;" + _SCHEMA + "PRAGMA user_version = 1; COMMIT;")

    # ------------------------------------------------------------------ #
    # Sources                                                              #
    # ------------------------------------------------------------------ #

    def sync_sources(self, entries: Iterable[Tuple[str, str, str, bool]], *,
                     remove_missing: bool = True) -> None:
        """Make the sources match the followed list: ``(key, url, title,
        automatic)`` each. A new source starts now, so its first check is
        a baseline; a source no longer followed goes with its posts (the
        ledger and the drafts stay), but only with ``remove_missing``: a
        list not known yet says nothing about what is missing from it."""
        now = self._now()
        wanted = {key: (url, title, automatic) for key, url, title, automatic in entries}
        with self._db:
            known = {row["key"] for row in self._db.execute("SELECT key FROM sources")}
            for key in (known - set(wanted)) if remove_missing else ():
                self._db.execute("DELETE FROM items WHERE source_key = ?", (key,))
                self._db.execute("DELETE FROM sources WHERE key = ?", (key,))
            for key, (url, title, automatic) in wanted.items():
                if key in known:
                    self._db.execute(
                        "UPDATE sources SET url = ?, title = ?, automatic = ? WHERE key = ?",
                        (url, title, int(automatic), key))
                else:
                    self._db.execute(
                        "INSERT INTO sources (key, url, title, automatic, created_at) "
                        "VALUES (?, ?, ?, ?, ?)", (key, url, title, int(automatic), now))

    def sources(self) -> List[SourceRow]:
        """Every source, sorted by title (case-insensitive), with counts."""
        counts = self._source_counts()
        rows = [self._source(row, counts) for row in self._db.execute("SELECT * FROM sources")]
        return sorted(rows, key=lambda s: (s.display_title.casefold(), s.key))

    def source(self, key: str) -> Optional[SourceRow]:
        row = self._db.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()
        return self._source(row, self._source_counts(key)) if row else None

    def _source_counts(self, key: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        sql = ("SELECT source_key, state, COUNT(*) AS n FROM items "
               "WHERE state IN ('new', 'older')")
        args: tuple = ()
        if key is not None:
            sql += " AND source_key = ?"
            args = (key,)
        found: Dict[str, Dict[str, int]] = {}
        for row in self._db.execute(sql + " GROUP BY source_key, state", args):
            found.setdefault(row["source_key"], {})[row["state"]] = row["n"]
        return found

    @staticmethod
    def _source(row, counts) -> SourceRow:
        mine = counts.get(row["key"], {})
        return SourceRow(
            key=row["key"], url=row["url"], title=row["title"],
            automatic=bool(row["automatic"]), created_at=row["created_at"],
            last_checked=row["last_checked"], last_attempt=row["last_attempt"],
            next_check=row["next_check"], failures=row["failures"], error=row["error"],
            paused=bool(row["paused"]), etag=row["etag"], last_modified=row["last_modified"],
            final_url=row["final_url"], site_url=row["site_url"], feed_title=row["feed_title"],
            new_count=mine.get(NEW, 0), older_count=mine.get(OLDER, 0))

    def set_paused(self, key: str, paused: bool) -> None:
        with self._db:
            self._db.execute("UPDATE sources SET paused = ? WHERE key = ?", (int(paused), key))

    def set_site(self, key: str, *, site_url: str = "", feed_title: str = "") -> None:
        with self._db:
            self._db.execute(
                "UPDATE sources SET site_url = COALESCE(NULLIF(?, ''), site_url), "
                "feed_title = COALESCE(NULLIF(?, ''), feed_title) WHERE key = ?",
                (site_url, feed_title, key))

    def due_sources(self, limit: int) -> List[SourceRow]:
        """Sources checked automatically that are due, oldest first."""
        now = self._now()
        rows = self._db.execute(
            "SELECT * FROM sources WHERE automatic = 1 AND paused = 0 AND next_check <= ? "
            "AND checking_until <= ? ORDER BY next_check, key LIMIT ?", (now, now, limit))
        counts = self._source_counts()
        return [self._source(row, counts) for row in rows]

    def claim(self, key: str, *, manual: bool = False) -> bool:
        """Take the check of ``key`` (no other check of it runs until it is
        done or its lease runs out). A manual check is allowed once a
        minute; an automatic one only when due."""
        now = self._now()
        condition = "last_attempt <= ?" if manual else "next_check <= ?"
        limit = now - MANUAL_CHECK_GAP if manual else now
        with self._db:
            cursor = self._db.execute(
                "UPDATE sources SET checking_until = ?, last_attempt = ? WHERE key = ? "
                f"AND paused = 0 AND checking_until <= ? AND {condition}",
                (now + CHECK_LEASE, now, key, now, limit))
        return cursor.rowcount == 1

    def next_manual_check(self, key: str) -> int:
        """When a manual check of ``key`` is allowed again (0: now)."""
        row = self._db.execute("SELECT last_attempt FROM sources WHERE key = ?",
                               (key,)).fetchone()
        if row is None:
            return 0
        allowed = row["last_attempt"] + MANUAL_CHECK_GAP
        return allowed if allowed > self._now() else 0

    def record_unchanged(self, key: str, *, interval: int = CHECK_INTERVAL) -> None:
        """A check found nothing changed (HTTP 304)."""
        now = self._now()
        with self._db:
            self._db.execute(
                "UPDATE sources SET last_checked = ?, next_check = ?, checking_until = 0, "
                "failures = 0, error = '' WHERE key = ?", (now, now + interval, key))

    def record_failure(self, key: str, error: str) -> int:
        """A check failed: try again later, longer each time (30 minutes,
        an hour, two... at most a day). Returns when."""
        now = self._now()
        with self._db:
            row = self._db.execute("SELECT failures FROM sources WHERE key = ?",
                                   (key,)).fetchone()
            if row is None:
                return 0
            failures = row["failures"] + 1
            next_check = now + min(MAX_BACKOFF, CHECK_INTERVAL * 2 ** min(failures, 7))
            self._db.execute(
                "UPDATE sources SET failures = ?, next_check = ?, checking_until = 0, "
                "error = ? WHERE key = ?", (failures, next_check, (error or "")[:300], key))
        return next_check

    def release(self, key: str) -> None:
        """Give a claimed check back without a result (cancelled)."""
        with self._db:
            self._db.execute("UPDATE sources SET checking_until = 0 WHERE key = ?", (key,))

    # ------------------------------------------------------------------ #
    # Posts                                                                #
    # ------------------------------------------------------------------ #

    def ingest(self, key: str, items: Sequence[FeedItem], *, interval: int = CHECK_INTERVAL,
               etag: str = "", last_modified: str = "", final_url: str = "",
               feed_title: str = "", site_url: str = "") -> IngestResult:
        """Store what a successful check found (STANDUP's ``ingest``)."""
        now = self._now()
        source = self._db.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()
        if source is None:
            return IngestResult()
        baseline = source["last_checked"] == 0
        known = {row["d_tag"] for row in self._db.execute(
            "SELECT d_tag FROM items WHERE source_key = ?", (key,))}
        unseen: Dict[str, FeedItem] = {}
        for item in items:
            try:
                d_tag = snapshots.identifier_of(item)
            except ValueError:
                continue
            if d_tag not in known and d_tag not in unseen:
                unseen[d_tag] = item
        if len(known) + len(unseen) > MAX_STORED_POSTS:
            self.record_failure(key, INBOX_FULL)
            return IngestResult(error=INBOX_FULL)
        ledger = self.ledger_states(unseen)
        new = older = waiting = 0
        with self._db:
            for d_tag, item in unseen.items():
                published = item.published_at or 0
                if published > now + FUTURE_GRACE:
                    waiting += 1
                    continue
                snapshot = snapshots.to_snapshot(item)
                text = json.dumps(snapshot, ensure_ascii=False)
                if len(text.encode("utf-8")) > MAX_BODY_BYTES + 64 * 1024:
                    continue  # too large to keep; an export file imports it
                if d_tag in ledger:
                    state = ledger[d_tag]
                elif baseline or (published and published < source["created_at"]):
                    state = OLDER
                else:
                    state = NEW
                if state == NEW:
                    new += 1
                elif state == OLDER:
                    older += 1
                self._db.execute(
                    "INSERT INTO items (source_key, d_tag, snapshot, found_at, published_at, "
                    "position, state, previous_state, revision, title, excerpt, image, link, "
                    "author, read_minutes, image_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                    (key, d_tag, text, now, published, _position(now, published), state,
                     state if state in (NEW, OLDER) else NEW,
                     item.title or "", snapshots.excerpt(item), snapshots.cover(item),
                     item.link or "", item.author or "",
                     snapshots.reading_minutes(item.content_html),
                     snapshots.image_count(item.content_html)))
            self._db.execute(
                "UPDATE sources SET last_checked = ?, next_check = ?, checking_until = 0, "
                "failures = 0, error = '', etag = ?, last_modified = ?, "
                "final_url = COALESCE(NULLIF(?, ''), final_url), "
                "feed_title = COALESCE(NULLIF(?, ''), feed_title), "
                "site_url = COALESCE(NULLIF(?, ''), site_url) WHERE key = ?",
                (now, now + interval, etag, last_modified, final_url, feed_title,
                 site_url, key))
        return IngestResult(new=new, older=older, waiting=waiting)

    def page(self, view: View, *, query: str = "", after: Optional[tuple] = None,
             limit: int = PAGE_SIZE) -> List[PostRow]:
        """One page of a list, newest first. ``after`` is the ``cursor`` of
        the last row of the previous page."""
        where, args = self._view_filter(view)
        if query.strip():
            like = f"%{_escape_like(query.strip())}%"
            where.append("(i.title LIKE ? ESCAPE '\\' OR i.excerpt LIKE ? ESCAPE '\\' "
                         "OR s.title LIKE ? ESCAPE '\\' OR s.feed_title LIKE ? ESCAPE '\\')")
            args += [like, like, like, like]
        if after is not None:
            where.append("(i.position, i.source_key, i.d_tag) < (?, ?, ?)")
            args += list(after)
        sql = ("SELECT i.*, s.title AS s_title, s.feed_title AS s_feed_title, s.url AS s_url "
               "FROM items i JOIN sources s ON s.key = i.source_key "
               f"WHERE {' AND '.join(where) or '1'} "
               "ORDER BY i.position DESC, i.source_key DESC, i.d_tag DESC LIMIT ?")
        return [self._post(row) for row in self._db.execute(sql, (*args, limit))]

    @staticmethod
    def cursor(post: PostRow) -> tuple:
        return InboxStore.cursor_of(post.found_at, post.published_at, post.source_key,
                                    post.d_tag)

    @staticmethod
    def cursor_of(found_at: int, published_at: int, source_key: str, d_tag: str) -> tuple:
        """Where a page ends: the next page starts after this post."""
        return (_position(found_at, published_at), source_key, d_tag)

    def _view_filter(self, view: View) -> Tuple[List[str], list]:
        if view.scope == "source":
            return ["i.source_key = ?", "i.state = ?"], [view.source_key, view.source_state]
        if view.scope == IMPORTED:
            return [f"i.state IN ({', '.join('?' * len(IMPORTED_STATES))})"], list(IMPORTED_STATES)
        state = {INBOX: NEW, OLDER_POSTS: OLDER, SKIPPED_POSTS: SKIPPED}[view.scope]
        return ["i.state = ?"], [state]

    @staticmethod
    def _post(row) -> PostRow:
        return PostRow(
            source_key=row["source_key"], d_tag=row["d_tag"], title=row["title"],
            excerpt=row["excerpt"], image=row["image"], link=row["link"],
            author=row["author"], published_at=row["published_at"], found_at=row["found_at"],
            state=row["state"], revision=row["revision"], read_minutes=row["read_minutes"],
            image_count=row["image_count"],
            source_title=row["s_title"] or row["s_feed_title"] or row["s_url"],
            source_url=row["s_url"])

    def post(self, source_key: str, d_tag: str) -> Optional[PostRow]:
        row = self._db.execute(
            "SELECT i.*, s.title AS s_title, s.feed_title AS s_feed_title, s.url AS s_url "
            "FROM items i JOIN sources s ON s.key = i.source_key "
            "WHERE i.source_key = ? AND i.d_tag = ?", (source_key, d_tag)).fetchone()
        return self._post(row) if row else None

    def item(self, source_key: str, d_tag: str) -> Optional[FeedItem]:
        """The stored post itself, body included."""
        row = self._db.execute("SELECT snapshot FROM items WHERE source_key = ? AND d_tag = ?",
                               (source_key, d_tag)).fetchone()
        return snapshots.from_snapshot(json.loads(row["snapshot"])) if row else None

    def counts(self) -> Counts:
        found = {row["state"]: row["n"] for row in self._db.execute(
            "SELECT state, COUNT(*) AS n FROM items GROUP BY state")}
        return Counts(inbox=found.get(NEW, 0), older=found.get(OLDER, 0),
                      imported=sum(found.get(s, 0) for s in IMPORTED_STATES),
                      skipped=found.get(SKIPPED, 0))

    def d_tags(self, states: Sequence[str] = (NEW, OLDER, SKIPPED)) -> List[str]:
        """Identifiers of the posts in ``states``, for reconciling."""
        marks = ", ".join("?" * len(states))
        return [row["d_tag"] for row in self._db.execute(
            f"SELECT DISTINCT d_tag FROM items WHERE state IN ({marks})", tuple(states))]

    # -- skip and undo ---------------------------------------------------------

    def skip(self, source_key: str, d_tag: str, revision: int) -> int:
        """Set a post aside. Returns its new revision; raises Conflict when
        it changed since ``revision`` or was imported."""
        return self._move(source_key, d_tag, revision, skip=True)

    def restore(self, source_key: str, d_tag: str, revision: int) -> int:
        """Bring a skipped post back where it was."""
        return self._move(source_key, d_tag, revision, skip=False)

    def _move(self, source_key: str, d_tag: str, revision: int, *, skip: bool) -> int:
        with self._db:
            row = self._db.execute(
                "SELECT state, previous_state, revision FROM items "
                "WHERE source_key = ? AND d_tag = ?", (source_key, d_tag)).fetchone()
            if row is None or row["state"] in IMPORTED_STATES or row["revision"] != revision:
                raise Conflict(d_tag)
            if not skip and row["state"] != SKIPPED:
                raise Conflict(d_tag)
            if skip:
                state = SKIPPED
                previous = row["state"] if row["state"] != SKIPPED else row["previous_state"]
            else:
                state, previous = row["previous_state"], row["previous_state"]
            self._db.execute(
                "UPDATE items SET state = ?, previous_state = ?, revision = revision + 1 "
                "WHERE source_key = ? AND d_tag = ? AND revision = ?",
                (state, previous, source_key, d_tag, revision))
        return revision + 1

    # -- what is imported ---------------------------------------------------------

    def reconcile(self, *, drafted: Iterable[str] = (), published: Iterable[str] = (),
                  removed: Iterable[str] = ()) -> int:
        """Bring posts in line with what exists on Nostr (STANDUP's
        ``reconcile``), and remember it in the ledger. Returns how many
        posts changed."""
        now = self._now()
        changed = 0
        steps = ((PUBLISHED, set(published), (PUBLISHED,)),
                 (DRAFTED, set(drafted), (DRAFTED, PUBLISHED)),
                 (REMOVED, set(removed), IMPORTED_STATES))
        with self._db:
            for state, tags, keep in steps:
                for chunk in _chunks(sorted(tags), 400):
                    marks = ", ".join("?" * len(chunk))
                    kept = ", ".join("?" * len(keep))
                    cursor = self._db.execute(
                        f"UPDATE items SET state = ?, revision = revision + 1 "
                        f"WHERE d_tag IN ({marks}) AND state NOT IN ({kept})",
                        (state, *chunk, *keep))
                    changed += cursor.rowcount
                    for d_tag in chunk:
                        self._ledger(d_tag, state, now)
        return changed

    def mark_imported(self, d_tag: str, state: str = DRAFTED) -> None:
        """A draft was just made from ``d_tag`` here."""
        self.reconcile(**{state: [d_tag]})

    def _ledger(self, d_tag: str, state: str, now: int) -> None:
        """Remember that ``d_tag`` was imported, for good: a post here is
        never offered again, even after its draft expired on the relays.
        A published post stays published."""
        row = self._db.execute("SELECT state FROM ledger WHERE d_tag = ?", (d_tag,)).fetchone()
        if row is None or (state == PUBLISHED and row["state"] != PUBLISHED):
            self._db.execute("INSERT OR REPLACE INTO ledger (d_tag, state, at) VALUES (?, ?, ?)",
                             (d_tag, state, now))

    def ledger_states(self, d_tags: Iterable[str]) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for chunk in _chunks(list(d_tags), 400):
            marks = ", ".join("?" * len(chunk))
            for row in self._db.execute(
                    f"SELECT d_tag, state FROM ledger WHERE d_tag IN ({marks})", chunk):
                found[row["d_tag"]] = row["state"]
        return found

    # ------------------------------------------------------------------ #
    # Jobs                                                                 #
    # ------------------------------------------------------------------ #

    def create_job(self, *, label: str, source_type: str, rows: Sequence[JobRow],
                   source_url: str = "", options: Optional[dict] = None) -> Job:
        """A job, saved before anything runs, paused."""
        now = self._now()
        job = Job(id=uuid.uuid4().hex, label=label, source_type=source_type,
                  source_url=source_url, status="paused", options=dict(options or {}),
                  created_at=now, updated_at=now, rows=list(rows))
        with self._db:
            self._db.execute(
                "INSERT INTO jobs (id, label, source_type, source_url, status, options, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job.id, label, source_type, source_url, job.status,
                 json.dumps(job.options), now, now))
            for index, row in enumerate(job.rows):
                row.index = index
                self._db.execute(
                    "INSERT INTO job_rows (job_id, idx, d_tag, title, source_key, status, "
                    "snapshot) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job.id, index, row.d_tag, row.title, row.source_key, row.status,
                     json.dumps(row.snapshot, ensure_ascii=False)
                     if row.snapshot is not None else None))
        return job

    def job(self, job_id: str) -> Optional[Job]:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def jobs(self) -> List[Job]:
        """Every job, newest first."""
        return [self._job(row) for row in self._db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC")]

    def _job(self, row) -> Job:
        job = Job(id=row["id"], label=row["label"], source_type=row["source_type"],
                  source_url=row["source_url"], status=row["status"],
                  options=json.loads(row["options"] or "{}"), created_at=row["created_at"],
                  updated_at=row["updated_at"], error=row["error"])
        for r in self._db.execute("SELECT * FROM job_rows WHERE job_id = ? ORDER BY idx",
                                  (job.id,)):
            job.rows.append(JobRow(
                index=r["idx"], d_tag=r["d_tag"], title=r["title"], source_key=r["source_key"],
                status=r["status"], stage=r["stage"], error=r["error"],
                signed_event=json.loads(r["signed_event"]) if r["signed_event"] else None,
                snapshot=json.loads(r["snapshot"]) if r["snapshot"] else None))
        return job

    def save_job(self, job: Job) -> None:
        job.updated_at = self._now()
        with self._db:
            self._db.execute("UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                             (job.status, job.error, job.updated_at, job.id))

    def save_row(self, job_id: str, row: JobRow) -> None:
        # A finished row needs neither its checkpoint nor its post any more.
        finished = row.status in (ROW_DONE, ROW_EXISTING)
        with self._db:
            self._db.execute(
                "UPDATE job_rows SET status = ?, stage = ?, error = ?, signed_event = ?, "
                "snapshot = CASE WHEN ? THEN NULL ELSE snapshot END "
                "WHERE job_id = ? AND idx = ?",
                (row.status, row.stage, row.error,
                 json.dumps(row.signed_event) if row.signed_event and not finished else None,
                 int(finished), job_id, row.index))
        if finished:
            row.snapshot = None
            row.signed_event = None

    def recover_jobs(self) -> None:
        """After a restart: a job that was running is paused, one that was
        stopping is stopped. Nothing resumes on its own."""
        with self._db:
            self._db.execute("UPDATE jobs SET status = 'paused' "
                             "WHERE status IN ('running', 'pausing')")
            self._db.execute("UPDATE jobs SET status = 'stopped' WHERE status = 'stopping'")

    def prune_jobs(self) -> None:
        """Finished jobs older than a week go, rows and all."""
        limit = self._now() - FINISHED_JOB_DAYS * 24 * 3600
        marks = ", ".join("?" * len(JOB_FINISHED))
        with self._db:
            old = [row["id"] for row in self._db.execute(
                f"SELECT id FROM jobs WHERE status IN ({marks}) AND updated_at < ?",
                (*JOB_FINISHED, limit))]
            for job_id in old:
                self._db.execute("DELETE FROM job_rows WHERE job_id = ?", (job_id,))
                self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def remove_job(self, job_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM job_rows WHERE job_id = ?", (job_id,))
            self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def _position(found_at: int, published_at: int) -> str:
    """Sort key: newest found first, and within one check newest published."""
    return f"{int(found_at):012d}{max(0, int(published_at or 0)):012d}"


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _chunks(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start:start + size]
