# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Imports window's shared rules, as plain functions.

Which posts an action applies to (Mail's rule), which can be chosen,
the one word for what became of a post, how a date reads, and the posts
of a source read while it is shown.
"""

from __future__ import annotations

from dataclasses import replace

import i18n
from nostr.imports.inbox_store import DRAFTED, NEW, OLDER, PUBLISHED, REMOVED, SKIPPED
from nostr.imports.workspace import (
    INBOX_ORIGIN,
    Collection,
    Post,
    checked_text,
    date_text,
    matches,
    post_from_item,
    selectable,
    state_word,
    targets,
)
from tests.imports_fakes import make_item

NOW = 1_800_000_000  # 2027-01-15


def post(key="a", d_tag="rss-a", state=NEW, **kw):
    base = Post(key=key, origin=INBOX_ORIGIN, source_key="s", source_title="Field notes",
                source_url="https://s.example/feed", d_tag=d_tag, title=f"Post {key}",
                excerpt="An excerpt", image="", link="https://s.example/a", author="",
                published_at=NOW - 100, found_at=NOW, read_minutes=1, image_count=0,
                state=state)
    return replace(base, **kw)


class TestTargets:
    def test_the_checked_posts_win_over_the_open_one(self):
        a, b, c = post("a", "rss-a"), post("b", "rss-b"), post("c", "rss-c")
        assert targets([a, b], c) == [a, b]

    def test_without_checks_the_open_post(self):
        a = post("a")
        assert targets([], a) == [a]
        assert targets([], None) == []

    def test_imported_and_busy_posts_are_never_targets(self):
        done = post("a", "rss-a", state=DRAFTED)
        busy = post("b", "rss-b")
        assert targets([], done) == []
        assert targets([done, busy], None, busy=["rss-b"]) == []
        assert not selectable(post(d_tag=""))

    def test_skipped_posts_can_still_be_imported(self):
        skipped = post(state=SKIPPED)
        assert selectable(skipped)
        assert skipped.skipped and not skipped.skippable
        assert post(state=OLDER).skippable


class TestStateWord:
    def test_the_five_words(self):
        assert state_word(post(state=NEW)) == "New"
        assert state_word(post(state=DRAFTED)) == "Imported"
        assert state_word(post(state=PUBLISHED)) == "Imported"
        assert state_word(post(state=REMOVED)) == "Imported"
        assert state_word(post(state=SKIPPED)) == "Skipped"
        assert state_word(post(), importing=True) == "Importing"
        assert state_word(post(), failed=True) == "Failed"
        assert state_word(post(state=OLDER)) == ""


class TestDates:
    def test_recent_dates_read_relative(self):
        assert date_text(NOW - 30, now=NOW) == "Just now"
        assert date_text(NOW - 5 * 60, now=NOW) == "5 min ago"
        assert date_text(NOW - 2 * 3600, now=NOW) == "2 h ago"
        assert date_text(NOW - 30 * 3600, now=NOW) == "Yesterday"
        assert date_text(NOW - 3 * 86400, now=NOW) == "3 days ago"
        assert date_text(0, now=NOW) == ""

    def test_older_dates_read_as_dates(self):
        assert date_text(NOW - 10 * 86400, now=NOW + 200 * 86400) == "Jan 5"
        assert date_text(NOW - 30 * 86400, now=NOW) == "Dec 16, 2026"

    def test_german_dates(self):
        i18n.install("de")
        try:
            from nostr.imports import workspace
            assert workspace.date_text(NOW - 2 * 3600, now=NOW) == "vor 2 Std."
            assert workspace.date_text(NOW - 30 * 86400, now=NOW).startswith("16. Dez")
        finally:
            i18n.install("en")


def test_selected_count():
    assert checked_text(3, 14) == "3 of 14 selected"


def test_search_over_held_posts():
    p = post(title="Lightning notes", excerpt="Channels and fees")
    assert matches(p, "LIGHTNING")
    assert matches(p, "fees")
    assert matches(p, "field")          # the source
    assert matches(p, "  ")
    assert not matches(p, "nostr")


def test_a_collection_lists_each_post_once_newest_first():
    older = make_item("Older", guid="g1", published_at=10)
    newer = make_item("Newer", guid="g2", published_at=20)
    twin = make_item("Older again", guid="g1", published_at=5)
    collection = Collection(id="m-x", kind="manual", label="Old blog",
                            items=[older, newer, twin])
    titles = [p.title for p in collection.posts()]
    assert titles == ["Newer", "Older"]
    d_tag = collection.posts()[1].d_tag
    collection.states[d_tag] = DRAFTED
    assert collection.posts()[1].imported
    assert collection.item(d_tag).title == "Older"


def test_a_held_post_has_the_stored_figures():
    item = make_item("Pictures", guid="g", content_html="<p>one two</p><img src='https://x/1.png'>",
                     image=None)
    held = post_from_item(item, collection="c", source_title="Blog")
    assert (held.image, held.image_count, held.read_minutes) == ("https://x/1.png", 1, 1)
    assert held.key == f"m:c:{held.d_tag}"


def test_a_held_lists_rows_are_made_once(monkeypatch):
    """Review M10: a source with a thousand long posts cost a third of a
    second each time its rows were asked for."""
    from nostr.imports import workspace
    made = []
    original = workspace.post_from_item
    monkeypatch.setattr(workspace, "post_from_item",
                        lambda *a, **kw: made.append(1) or original(*a, **kw))
    collection = workspace.Collection(id="m-x", kind="manual", label="Blog",
                                      items=[make_item(f"P{i}", guid=f"g{i}")
                                             for i in range(50)])
    first = collection.posts()
    assert len(made) == 50
    collection.states[first[0].d_tag] = "drafted"
    again = collection.posts()
    assert len(made) == 50
    assert again[0].state == "drafted"
    collection.items = collection.items + [make_item("New", guid="g-new")]
    assert len(collection.posts()) == 51
