# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The imports inbox: how posts arrive, move and stay.

The rules are EINUNDZWANZIG STANDUP's, so both inboxes fill the same way
for the same source: a first check is a baseline, posts are never
rewritten, a post from the future waits, Skip and Undo carry a
revision, and a post that was imported never comes back to the Inbox.
"""

from __future__ import annotations

import pytest

from nostr.imports import snapshots
from nostr.imports.inbox_store import (
    IMPORTED,
    INBOX,
    INBOX_FULL,
    MAX_STORED_POSTS,
    OLDER_POSTS,
    SKIPPED_POSTS,
    Conflict,
    InboxStore,
    JobRow,
    View,
)
from nostr.imports.sources.podcast import PodcastEpisode, ValueRecipient
from tests.imports_fakes import make_item

T0 = 1_800_000_000
FEED = "https://blog.example/feed"
KEY = FEED


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    inbox = InboxStore(":memory:", clock=clock)
    inbox.sync_sources([(KEY, FEED, "Field notes", True)])
    yield inbox
    inbox.close()


def items(*titles, published=None, **kw):
    return [make_item(t, guid=f"g-{t}", published_at=published, **kw) for t in titles]


def view_titles(store, view, **kw):
    return [p.title for p in store.page(view, **kw)]


class TestIngest:
    def test_the_first_check_is_a_baseline(self, store):
        result = store.ingest(KEY, items("a", "b", published=T0 - 10))
        assert (result.new, result.older) == (0, 2)
        assert store.counts().older == 2
        assert store.counts().inbox == 0

    def test_later_posts_arrive_in_the_inbox(self, store, clock):
        store.ingest(KEY, items("a", published=T0 - 10))
        clock.now += 900
        result = store.ingest(KEY, items("a", "b", published=T0 + 100))
        assert result.new == 1
        assert view_titles(store, View(INBOX)) == ["b"]

    def test_a_post_published_before_the_follow_is_older(self, store, clock):
        store.ingest(KEY, items("a", published=T0))
        clock.now += 900
        store.ingest(KEY, items("old", published=T0 - 3600))
        assert view_titles(store, View(OLDER_POSTS)) == ["old", "a"]

    def test_a_post_from_the_future_waits(self, store, clock):
        store.ingest(KEY, [])
        clock.now += 900
        result = store.ingest(KEY, items("soon", published=clock.now + 3600))
        assert result.waiting == 1
        assert store.counts() == store.counts().__class__()
        clock.now += 7200
        assert store.ingest(KEY, items("soon", published=T0 + 900 + 3600)).new == 1

    def test_a_stored_post_is_never_rewritten(self, store, clock):
        store.ingest(KEY, items("a", published=T0))
        clock.now += 900
        edited = [make_item("a edited", guid="g-a", published_at=T0)]
        store.ingest(KEY, edited)
        assert view_titles(store, View(OLDER_POSTS)) == ["a"]

    def test_the_inbox_limit(self, store, monkeypatch):
        monkeypatch.setattr("nostr.imports.inbox_store.MAX_STORED_POSTS", 2)
        result = store.ingest(KEY, items("a", "b", "c"))
        assert result.error == INBOX_FULL
        assert store.source(KEY).error == INBOX_FULL
        assert MAX_STORED_POSTS == 5000

    def test_counts_per_source_and_stats(self, store, clock):
        store.ingest(KEY, items("a"))
        clock.now += 900
        body = "<p>" + " ".join(["word"] * 450) + "</p><img src='https://x/1.png'><IMG src=x>"
        store.ingest(KEY, [make_item("b", guid="g-b", published_at=T0 + 10,
                                     content_html=body)])
        source = store.source(KEY)
        assert (source.new_count, source.older_count) == (1, 1)
        post = store.page(View(INBOX))[0]
        assert (post.read_minutes, post.image_count) == (2, 2)
        assert post.image == "https://x/1.png"
        assert post.source_title == "Field notes"
        assert store.item(KEY, post.d_tag).content_html == body

    def test_identifiers_are_standups(self, store):
        store.ingest(KEY, [make_item("t", guid="urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a")])
        assert store.page(View(OLDER_POSTS))[0].d_tag == "rss-3841e5cf232f5111"


class TestSources:
    def test_sorted_by_title_and_removed_with_their_posts(self, store):
        store.sync_sources([(KEY, FEED, "zebra", True),
                            ("https://b.example/feed", "https://b.example/feed", "Apple", False)])
        assert [s.title for s in store.sources()] == ["Apple", "zebra"]
        store.ingest(KEY, items("a"))
        store.sync_sources([("https://b.example/feed", "https://b.example/feed", "Apple", False)])
        assert store.counts().older == 0
        assert [s.key for s in store.sources()] == ["https://b.example/feed"]

    def test_a_source_that_comes_back_starts_fresh(self, store, clock):
        store.ingest(KEY, items("a"))
        store.sync_sources([])
        clock.now += 900
        store.sync_sources([(KEY, FEED, "", True)])
        assert store.source(KEY).created_at == clock.now
        store.ingest(KEY, items("a", "b"))
        assert store.counts().inbox == 0        # a baseline again, never a flood

    def test_due_and_claimed_once(self, store, clock):
        assert [s.key for s in store.due_sources(2)] == [KEY]
        assert store.claim(KEY)
        assert not store.claim(KEY)             # held until done or the lease ends
        assert store.due_sources(2) == []
        store.ingest(KEY, [])
        assert store.due_sources(2) == []
        clock.now += 15 * 60
        assert [s.key for s in store.due_sources(2)] == [KEY]

    def test_paused_and_manual_sources_are_not_due(self, store):
        store.set_paused(KEY, True)
        assert store.due_sources(2) == []
        store.set_paused(KEY, False)
        store.sync_sources([(KEY, FEED, "", False)])
        assert store.due_sources(2) == []

    def test_backoff(self, store, clock):
        waits = []
        for _ in range(9):
            waits.append(store.record_failure(KEY, "boom") - clock.now)
        assert waits == [1800, 3600, 7200, 14400, 28800, 57600, 86400, 86400, 86400]
        assert store.source(KEY).error == "boom"
        store.ingest(KEY, [])
        assert store.source(KEY).failures == 0
        assert store.source(KEY).error == ""

    def test_a_manual_check_once_a_minute(self, store, clock):
        assert store.claim(KEY, manual=True)
        store.ingest(KEY, [])
        assert not store.claim(KEY, manual=True)
        assert store.next_manual_check(KEY) == T0 + 60
        clock.now += 60
        assert store.claim(KEY, manual=True)

    def test_unchanged(self, store, clock):
        store.claim(KEY)
        store.record_unchanged(KEY)
        assert store.source(KEY).next_check == clock.now + 900


class TestSkipAndUndo:
    def _post(self, store, clock):
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, items("a", published=T0 + 100))
        return store.page(View(INBOX))[0]

    def test_skip_then_undo(self, store, clock):
        post = self._post(store, clock)
        revision = store.skip(KEY, post.d_tag, post.revision)
        assert view_titles(store, View(SKIPPED_POSTS)) == ["a"]
        store.restore(KEY, post.d_tag, revision)
        assert view_titles(store, View(INBOX)) == ["a"]

    def test_a_stale_revision_is_refused(self, store, clock):
        post = self._post(store, clock)
        store.skip(KEY, post.d_tag, post.revision)
        with pytest.raises(Conflict):
            store.skip(KEY, post.d_tag, post.revision)

    def test_an_imported_post_cannot_be_skipped_or_brought_back(self, store, clock):
        post = self._post(store, clock)
        revision = store.skip(KEY, post.d_tag, post.revision)
        store.reconcile(drafted=[post.d_tag])
        with pytest.raises(Conflict):
            store.restore(KEY, post.d_tag, revision)
        assert view_titles(store, View(IMPORTED)) == ["a"]


class TestReconcile:
    def test_drafted_published_removed(self, store, clock):
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, items("a", "b", "c", published=T0 + 1))
        tags = {p.title: p.d_tag for p in store.page(View(INBOX))}
        assert store.reconcile(drafted=[tags["a"]], published=[tags["b"]],
                               removed=[tags["c"]]) == 3
        states = {p.title: p.state for p in store.page(View(IMPORTED))}
        assert states == {"a": "drafted", "b": "published", "c": "removed"}
        assert store.counts().inbox == 0
        assert store.counts().imported == 3

    def test_never_resurrected(self, store, clock):
        # A drafted post stays imported when its draft is deleted later,
        # and a post whose identifier is in the ledger never reaches the
        # Inbox again, also after its source was followed again.
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, items("a", published=T0 + 1))
        d_tag = store.page(View(INBOX))[0].d_tag
        store.mark_imported(d_tag)
        store.reconcile(removed=[d_tag])
        assert store.page(View(IMPORTED))[0].state == "drafted"
        store.sync_sources([])
        store.sync_sources([(KEY, FEED, "", True)])
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, items("a", published=clock.now))
        assert store.counts().inbox == 0
        assert store.page(View(IMPORTED))[0].state == "drafted"


class TestPaging:
    def test_keyset_pages_are_stable_and_complete(self, store, clock):
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, items(*[f"p{i:02d}" for i in range(7)], published=T0 + 5))
        first = store.page(View(INBOX), limit=3)
        second = store.page(View(INBOX), limit=3, after=store.cursor(first[-1]))
        third = store.page(View(INBOX), limit=3, after=store.cursor(second[-1]))
        seen = [p.d_tag for p in first + second + third]
        assert len(seen) == len(set(seen)) == 7

    def test_newest_found_first_then_newest_published(self, store, clock):
        store.ingest(KEY, [])
        clock.now += 900
        store.ingest(KEY, [make_item("older", guid="1", published_at=T0 + 1),
                           make_item("newer", guid="2", published_at=T0 + 50)])
        clock.now += 900
        store.ingest(KEY, [make_item("latest check", guid="3", published_at=T0 + 2)])
        assert view_titles(store, View(INBOX)) == ["latest check", "newer", "older"]

    def test_search(self, store, clock):
        store.ingest(KEY, [make_item("Lightning notes", guid="1"),
                           make_item("Other", guid="2", summary="about 100% self-custody")])
        assert view_titles(store, View(OLDER_POSTS), query="lightning") == ["Lightning notes"]
        assert view_titles(store, View(OLDER_POSTS), query="100%") == ["Other"]
        assert len(store.page(View(OLDER_POSTS), query="field notes")) == 2   # source title
        assert store.page(View(OLDER_POSTS), query="o_her") == []   # "_" is a character

    def test_a_source_view(self, store, clock):
        store.sync_sources([(KEY, FEED, "", True), ("b", "https://b.example/f", "", True)])
        store.ingest(KEY, items("a"))
        store.ingest("b", items("b"))
        assert view_titles(store, View("source", "b", "older")) == ["b"]


class TestJobs:
    def test_saved_paused_before_running_and_recovered(self, store):
        job = store.create_job(label="Field notes", source_type="inbox", rows=[
            JobRow(0, "rss-1", "One", snapshot={"title": "One"}),
            JobRow(0, "rss-2", "Two")])
        assert store.job(job.id).status == "paused"
        job.status = "running"
        store.save_job(job)
        store.recover_jobs()
        assert store.job(job.id).status == "paused"
        job.status = "stopping"
        store.save_job(job)
        store.recover_jobs()
        assert store.job(job.id).status == "stopped"

    def test_rows_keep_checkpoints_until_done(self, store):
        job = store.create_job(label="x", source_type="feed", rows=[
            JobRow(0, "rss-1", "One", snapshot={"title": "One"})])
        row = job.rows[0]
        row.signed_event = {"id": "e1"}
        row.stage = "sending"
        store.save_row(job.id, row)
        assert store.job(job.id).rows[0].signed_event == {"id": "e1"}
        assert store.job(job.id).rows[0].snapshot == {"title": "One"}
        row.status = "done"
        store.save_row(job.id, row)
        saved = store.job(job.id).rows[0]
        assert saved.signed_event is None and saved.snapshot is None

    def test_old_finished_jobs_are_pruned(self, store, clock):
        old = store.create_job(label="old", source_type="feed", rows=[])
        old.status = "completed"
        store.save_job(old)
        unfinished = store.create_job(label="paused", source_type="feed", rows=[])
        clock.now += 8 * 24 * 3600
        store.prune_jobs()
        assert [j.id for j in store.jobs()] == [unfinished.id]


class TestSnapshots:
    def test_round_trip_with_a_podcast(self):
        episode = PodcastEpisode(audio="https://cdn.example/e.mp3", duration=60,
                                 value=(ValueRecipient("host", "node", "abc", 90),))
        item = make_item("Episode", guid="e1", categories=("a", "b"), image="https://x/c.png",
                         published_at=5)
        item = item.__class__(**{**item.__dict__, "podcast": episode})
        again = snapshots.from_snapshot(snapshots.to_snapshot(item))
        assert again == item

    def test_excerpt_and_cover(self):
        item = make_item("t", summary=None, content_html="<p>Hello &amp; <b>world</b></p>"
                         "<img src='https://x/a.png'>", image=None)
        assert snapshots.excerpt(item) == "Hello & world"
        assert snapshots.cover(item) == "https://x/a.png"
        assert snapshots.reading_minutes("") == 0
        assert snapshots.reading_minutes("<p>one</p>") == 1
