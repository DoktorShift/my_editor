# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared list of sources: reading, writing and the three-way merge.

Pure functions, so each rule is pinned on its own: STANDUP's payload is
read and written back without losing anything, and a merge keeps the
edits both apps made since the last sync.
"""

from nostr.imports.feed_list import (
    FeedList,
    clean_options,
    is_storable_url,
    merge,
    read_payload,
    source_key,
    write_payload,
)

A = "https://a.example/feed"
B = "https://b.example/feed"
C = "https://c.example/feed"


def listing(*rows, options=None, **extra):
    return read_payload({"feeds": [r if isinstance(r, dict) else {"url": r} for r in rows],
                         "sourceOptions": options or {}, **extra})


def urls(feed_list):
    return [row["url"] for row in feed_list.rows]


class TestReading:
    def test_junk_rows_dropped(self):
        result = read_payload({"feeds": [
            {"url": A, "title": "ok", "lastFetchedAt": 5},
            {"url": "<?xml paste>"},
            {"url": ""},
            "not a dict",
            {"url": A.upper()},  # the same source
            {"url": B, "lastFetchedAt": "bad"},
            {"url": "x" * 2049},
        ]})
        assert urls(result) == [A, B]
        feeds = result.feeds()
        assert feeds[0].last_fetched_at == 5
        assert feeds[1].last_fetched_at == 0

    def test_rows_this_app_cannot_read_are_kept_but_not_shown(self):
        result = read_payload({"feeds": [{"url": A}, {"url": "standup-only"}]})
        assert urls(result) == [A, "standup-only"]
        assert [f.url for f in result.feeds()] == [A]

    def test_the_older_snake_case_read_time(self):
        result = read_payload({"feeds": [{"url": A, "last_fetched_at": 9}],
                               "updated_at": 1})
        assert result.rows == [{"url": A, "lastFetchedAt": 9}]
        assert result.extra == {}

    def test_non_dict_payload(self):
        assert read_payload(None).rows == []
        assert read_payload([1, 2]).rows == []

    def test_options_keep_only_known_booleans(self):
        assert clean_options({
            " HTTPS://A.example/Feed ": {"rehostImages": False, "other": 1,
                                         "fetchFullText": "yes"},
            B: {"fetchFullText": False},
            C: "nonsense",
        }) == {"https://a.example/feed": {"rehostImages": False},
               B: {"fetchFullText": False}}

    def test_storable(self):
        assert is_storable_url(A)
        assert not is_storable_url(" <rss>")
        assert not is_storable_url(5)
        assert source_key("  HTTPS://A.example ") == "https://a.example"


class TestWriting:
    def test_round_trip_keeps_everything(self):
        payload = {"feeds": [{"url": A, "title": "T", "lastFetchedAt": 3, "x": [1]}],
                   "sourceOptions": {A: {"rehostImages": False}},
                   "updatedAt": 1, "future": {"k": "v"}}
        written = write_payload(read_payload(payload), 99)
        assert written == {**payload, "updatedAt": 99}


class TestMerge:
    def test_without_a_remote_the_local_list_wins(self):
        local = listing(A)
        assert merge(None, local, None) == local

    def test_added_in_both_apps(self):
        base = listing(A)
        assert urls(merge(base, listing(A, B), listing(A, C))) == [A, C, B]

    def test_removed_here_stays_removed(self):
        base = listing(A, B)
        assert urls(merge(base, listing(A), listing(A, B, C))) == [A, C]

    def test_removed_there_stays_removed(self):
        base = listing(A, B)
        assert urls(merge(base, listing(A, B), listing(A))) == [A]

    def test_first_sync_is_a_union(self):
        assert urls(merge(None, listing(A, B), listing(C, A))) == [C, A, B]

    def test_titles(self):
        base = listing({"url": A, "title": "a"}, {"url": B, "title": "b"})
        local = listing({"url": A, "title": "a here"}, {"url": B, "title": "b"})
        remote = listing({"url": A, "title": "a"}, {"url": B, "title": "b there"})
        merged = merge(base, local, remote)
        assert [r.get("title") for r in merged.rows] == ["a here", "b there"]

    def test_a_title_added_in_both_keeps_one(self):
        merged = merge(None, listing(A), listing({"url": A, "title": "There"}))
        assert merged.rows[0]["title"] == "There"

    def test_the_later_read_time_wins(self):
        base = listing(A)
        local = listing({"url": A, "lastFetchedAt": 10})
        remote = listing({"url": A, "lastFetchedAt": 20, "pinned": True})
        assert merge(base, local, remote).rows == [
            {"url": A, "lastFetchedAt": 20, "pinned": True}]
        assert merge(base, listing({"url": A, "lastFetchedAt": 30}), remote).rows[0][
            "lastFetchedAt"] == 30

    def test_options_changed_here_win(self):
        base = listing(A, options={A: {"rehostImages": True}})
        local = listing(A, options={A: {"rehostImages": False}})
        remote = listing(A, options={A: {"rehostImages": True, "fetchFullText": False},
                                     B: {"fetchFullText": False}})
        merged = merge(base, local, remote)
        assert merged.options == {A: {"rehostImages": False, "fetchFullText": False},
                                  B: {"fetchFullText": False}}

    def test_unknown_top_level_keys_come_from_the_remote(self):
        merged = merge(listing(A), listing(A, B), listing(A, future=1))
        assert merged.extra == {"future": 1}

    def test_same_sources_ignores_read_times(self):
        one = listing({"url": A, "lastFetchedAt": 1})
        two = listing({"url": A, "lastFetchedAt": 2})
        assert one.same_sources(two)
        assert not one.same_sources(listing(A, B))

    def test_changing(self):
        feed_list = FeedList()
        assert feed_list.add(A, "T")
        assert not feed_list.add(A.upper())
        assert feed_list.set_title(A, "U")
        assert not feed_list.set_title(A, "U")
        assert feed_list.set_fetched(A, 4)
        assert feed_list.set_options(A, rehostImages=False, unknown=True)
        assert feed_list.options == {A: {"rehostImages": False}}
        assert feed_list.remove(A)
        assert not feed_list.remove(A)
