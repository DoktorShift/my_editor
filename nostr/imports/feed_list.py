# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The synced list of sources, as EINUNDZWANZIG STANDUP writes it.

One person can use both apps, so both read and write one list: a kind
30078 event, ``d`` = ``einundzwanzig:feed-sources``, whose content is
this payload encrypted to the person themselves (NIP-44)::

    { "feeds": [ { "url": "...", "title": "...", "lastFetchedAt": 1759700000 } ],
      "sourceOptions": { "<url, trimmed and lower-cased>":
                         { "rehostImages": true, "fetchFullText": false } },
      "updatedAt": 1759700000 }

``sourceOptions`` holds the per-source defaults for new drafts, also for
sources STANDUP's server checks and that are not in ``feeds``.

Pure functions, no Qt and no network, so every rule here is tested on
its own (tests/test_imports_feed_list.py):

- :func:`read_payload` turns a decrypted payload into a :class:`FeedList`,
  keeping what this app does not understand (rows it cannot resolve,
  keys it does not know) so writing the list back never deletes another
  app's data.
- :func:`merge` is the three-way merge that keeps both apps' edits:
  ``(remote + added here) - removed here``; a title or an option changed
  here wins over the remote one, otherwise the remote one is kept; the
  later ``lastFetchedAt`` wins.
- :func:`write_payload` turns a list back into STANDUP's payload.

The list this app wrote before it shared STANDUP's (``d`` =
``myeditor:feed-sources``, snake_case keys) reads through the same
:func:`read_payload`, so it can be merged in once.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .constants import FEED_LIST_DTAG, LEGACY_FEED_LIST_DTAG
from .registry import can_resolve_source

__all__ = ["FEED_LIST_DTAG", "LEGACY_FEED_LIST_DTAG", "FeedList", "FeedSubscription",
           "SourceOptions", "clean_options", "is_storable_url", "merge", "read_payload",
           "source_key", "write_payload"]
# STANDUP's MAX_FEED_URL_LENGTH.
MAX_URL_LENGTH = 2048
OPTION_KEYS = ("rehostImages", "fetchFullText")
DEFAULT_OPTIONS = {"rehostImages": True, "fetchFullText": True}


def source_key(url: str) -> str:
    """The key a source is known by in both apps: trimmed, lower-cased."""
    return (url or "").strip().lower()


def is_storable_url(url) -> bool:
    """A value that may be a source at all: STANDUP's rule (a string of at
    most 2048 characters, not starting with ``<`` or ``>``)."""
    if not isinstance(url, str):
        return False
    stripped = url.strip()
    return bool(stripped) and len(url) <= MAX_URL_LENGTH and stripped[0] not in "<>"


@dataclass(frozen=True)
class SourceOptions:
    """The defaults for drafts made from one source."""

    rehost_images: bool = True
    fetch_full_text: bool = True


@dataclass(frozen=True)
class FeedSubscription:
    url: str
    title: str = ""
    last_fetched_at: int = 0


@dataclass
class FeedList:
    """One version of the list.

    ``rows`` are the feed objects as written (unknown keys kept), in
    order, one per source key. ``options`` maps a source key to the
    option values set for it. ``extra`` holds every other top-level key.
    """

    rows: List[dict] = field(default_factory=list)
    options: Dict[str, Dict[str, bool]] = field(default_factory=dict)
    extra: Dict[str, object] = field(default_factory=dict)

    def copy(self) -> "FeedList":
        return copy.deepcopy(self)

    # -- reading -------------------------------------------------------------

    def keys(self) -> List[str]:
        return [source_key(row["url"]) for row in self.rows]

    def row(self, url: str) -> Optional[dict]:
        key = source_key(url)
        return next((row for row in self.rows if source_key(row["url"]) == key), None)

    def feeds(self) -> List[FeedSubscription]:
        """The sources this app can read, in order."""
        out = []
        for row in self.rows:
            url = row["url"].strip()
            if not can_resolve_source(url):
                continue
            out.append(FeedSubscription(url=url, title=_text(row.get("title")),
                                        last_fetched_at=_fetched_at(row)))
        return out

    def options_for(self, url: str) -> SourceOptions:
        chosen = {**DEFAULT_OPTIONS, **self.options.get(source_key(url), {})}
        return SourceOptions(rehost_images=chosen["rehostImages"],
                             fetch_full_text=chosen["fetchFullText"])

    def same_sources(self, other: "FeedList") -> bool:
        """Whether two versions say the same, apart from read times:
        those only travel along with a real change."""
        return _essence(self) == _essence(other)

    # -- changing -------------------------------------------------------------

    def add(self, url: str, title: str = "") -> bool:
        if self.row(url) is not None:
            return False
        row = {"url": url.strip()}
        if title:
            row["title"] = title
        self.rows.append(row)
        return True

    def remove(self, url: str) -> bool:
        key = source_key(url)
        kept = [row for row in self.rows if source_key(row["url"]) != key]
        changed = len(kept) != len(self.rows)
        self.rows = kept
        return changed

    def set_title(self, url: str, title: str) -> bool:
        row = self.row(url)
        if row is None or _text(row.get("title")) == (title or ""):
            return False
        if title:
            row["title"] = title
        else:
            row.pop("title", None)
        return True

    def set_fetched(self, url: str, when: int) -> bool:
        row = self.row(url)
        if row is None or _fetched_at(row) == int(when):
            return False
        row["lastFetchedAt"] = int(when)
        row.pop("last_fetched_at", None)
        return True

    def set_options(self, url: str, **values: bool) -> bool:
        key = source_key(url)
        if not key or len(key) > MAX_URL_LENGTH:
            return False
        entry = dict(self.options.get(key, {}))
        for name, value in values.items():
            if name in OPTION_KEYS and isinstance(value, bool):
                entry[name] = value
        if entry == self.options.get(key, {}):
            return False
        self.options[key] = entry
        return True


def read_payload(payload) -> FeedList:
    """A :class:`FeedList` from a decrypted payload (STANDUP's, or this
    app's older one). Rows that cannot be a source at all are dropped;
    rows this app cannot resolve are kept, for the app that can."""
    if not isinstance(payload, dict):
        return FeedList()
    result = FeedList(extra={k: copy.deepcopy(v) for k, v in payload.items()
                             if k not in ("feeds", "sourceOptions", "updatedAt",
                                          "updated_at")})
    seen = set()
    for raw in payload.get("feeds") if isinstance(payload.get("feeds"), list) else []:
        if not isinstance(raw, dict) or not is_storable_url(raw.get("url")):
            continue
        key = source_key(raw["url"])
        if key in seen:
            continue
        seen.add(key)
        row = copy.deepcopy(raw)
        if "last_fetched_at" in row:
            # This app's older list; STANDUP's key from now on.
            legacy = row.pop("last_fetched_at")
            row.setdefault("lastFetchedAt", legacy)
        result.rows.append(row)
    result.options = clean_options(payload.get("sourceOptions"))
    return result


def clean_options(raw) -> Dict[str, Dict[str, bool]]:
    """Only the known options with boolean values, under source keys."""
    out: Dict[str, Dict[str, bool]] = {}
    if not isinstance(raw, dict):
        return out
    for url, value in raw.items():
        if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
            continue
        if not isinstance(value, dict):
            continue
        entry = {k: value[k] for k in OPTION_KEYS if isinstance(value.get(k), bool)}
        if entry:
            out[source_key(url)] = entry
    return out


def write_payload(feed_list: FeedList, updated_at: int) -> dict:
    """STANDUP's payload for ``feed_list``."""
    payload = copy.deepcopy(feed_list.extra)
    payload["feeds"] = copy.deepcopy(feed_list.rows)
    payload["sourceOptions"] = copy.deepcopy(feed_list.options)
    payload["updatedAt"] = int(updated_at)
    return payload


def merge(base: Optional[FeedList], local: FeedList, remote: Optional[FeedList]) -> FeedList:
    """Both apps' edits since ``base`` (the version last known to be on the
    relays) in one list.

    Sources: the remote ones, plus those added here, minus those removed
    here. A title or an option changed here wins; otherwise the remote
    one is kept. The later read time wins. Without a remote version, the
    local one is the answer.
    """
    if remote is None:
        return local.copy()
    base = base or FeedList()
    base_keys = set(base.keys())
    local_keys = local.keys()
    removed_here = base_keys - set(local_keys)

    result = FeedList(extra=copy.deepcopy(remote.extra))
    for row in remote.rows:
        key = source_key(row["url"])
        if key in removed_here:
            continue
        merged = copy.deepcopy(row)
        mine = local.row(row["url"])
        if mine is not None:
            before = base.row(row["url"])
            title = _text(mine.get("title"))
            if before is None:
                # Added in both apps: a title from either is better than none.
                if title:
                    merged["title"] = title
            elif title != _text(before.get("title")):
                if title:
                    merged["title"] = title
                else:
                    merged.pop("title", None)
            latest = max(_fetched_at(row), _fetched_at(mine))
            if latest:
                merged["lastFetchedAt"] = latest
        result.rows.append(merged)
    remote_keys = set(remote.keys())
    for row in local.rows:
        key = source_key(row["url"])
        if key not in remote_keys and key not in base_keys:
            result.rows.append(copy.deepcopy(row))

    for key in _ordered_union(remote.options, local.options):
        entry = dict(remote.options.get(key, {}))
        mine = local.options.get(key, {})
        before = base.options.get(key, {})
        for name in OPTION_KEYS:
            if name in mine and mine.get(name) != before.get(name):
                entry[name] = mine[name]
        if entry:
            result.options[key] = entry
    return result


# -- helpers -------------------------------------------------------------------

def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _fetched_at(row: dict) -> int:
    value = row.get("lastFetchedAt", row.get("last_fetched_at", 0))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _ordered_union(*maps: Iterable[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for mapping in maps:
        for key in mapping:
            seen.setdefault(key, None)
    return list(seen)


def _essence(feed_list: FeedList):
    rows = []
    for row in feed_list.rows:
        rows.append({k: v for k, v in row.items() if k not in ("lastFetchedAt",)})
    return rows, feed_list.options, feed_list.extra
