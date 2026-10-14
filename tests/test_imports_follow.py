# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Following, files and links, as the imports controller does them.

A followed feed has its posts at once, as the first check would put
them: in Older Posts, never in the Inbox (they were there before it was
followed). A list of sources follows each of them once. A file or a link
opens its posts to be imported once, and is never kept as a source.
"""

from __future__ import annotations

import pytest

from nostr.imports.feed_list import source_key
from nostr.imports.inbox_store import OLDER, OLDER_POSTS, View
from nostr.imports.sources.opml import parse_opml
from nostr.imports_controller import NO_SOURCES_IN_LIST
from tests.imports_fakes import TWO_ITEM_FEED, FakeFetcher
from tests.outbox_fakes import settle
from tests.test_imports_controller import Harness, profile
from tests.test_imports_files import OPML, WXR, medium_zip

FEED = "https://blog.example/feed"
LIST = "https://reader.example/subscriptions.opml"


@pytest.fixture
def harness(tmp_path):
    fetcher = FakeFetcher({FEED: ("ok", TWO_ITEM_FEED), LIST: ("ok", OPML),
                           "https://reader.example/not-a-list.opml": ("ok", "<html/>")})
    h = Harness(tmp_path, fetcher=fetcher)
    h.controller.account_changed(profile())
    settle()
    yield h
    h.controller.account_changed(None)
    settle()


def look_up(controller, address):
    answers = []
    controller.look_up(address, on_done=lambda result, error: answers.append((result, error)))
    settle()
    return answers[0]


def test_looking_up_a_feed_reads_it(harness):
    result, error = look_up(harness.controller, FEED)
    assert error == ""
    assert result.url == FEED
    assert [item.title for item in result.feed.items] == ["First", "Second"]


def test_a_failed_look_up_says_why_in_words(harness):
    result, error = look_up(harness.controller, "https://gone.example/feed")
    assert result is None
    assert error and "404" not in error.split()[0]


def test_following_a_feed_puts_its_posts_in_older_posts(harness):
    controller = harness.controller
    result, _error = look_up(controller, FEED)
    key = controller.follow(result.url, result.feed.title, result=result)
    assert key == source_key(FEED)
    assert controller.is_followed(FEED)
    source = controller.source(key)
    assert (source.new_count, source.older_count) == (0, 2)
    assert controller.counts().inbox == 0
    assert [post.title for post in controller.page(View(OLDER_POSTS))] == ["Second", "First"]
    # Its first check is done: the checker waits the usual time.
    assert source.last_checked > 0


def test_following_again_changes_nothing(harness):
    controller = harness.controller
    result, _error = look_up(controller, FEED)
    controller.follow(result.url, result=result)
    assert controller.follow(result.url, result=result) == source_key(FEED)
    assert len(controller.sources()) == 1
    assert controller.source(source_key(FEED)).older_count == 2


def test_an_address_that_cannot_be_followed(harness):
    assert harness.controller.follow("not an address") == ""
    assert harness.controller.sources() == []


def test_a_list_of_sources_follows_each_once(harness):
    controller = harness.controller
    document = parse_opml(OPML)
    controller.follow(document.feeds[0].xml_url)
    assert controller.follow_all(document) == (len(document.feeds) - 1, 1, 0)
    assert len(controller.sources()) == len(document.feeds)


def test_an_address_on_this_computer_is_never_followed(harness):
    # Q-6: not followed, also when a list names it.
    from nostr.imports.sources.opml import OpmlDocument, OpmlFeed
    controller = harness.controller
    assert controller.follow("http://localhost:8080/feed") == ""
    assert controller.follow("https://192.168.1.10/rss") == ""
    document = OpmlDocument(title="", feeds=(
        OpmlFeed(xml_url="http://127.0.0.1/feed", title="Mine", html_url=None),
        OpmlFeed(xml_url="https://nas.local/feed", title="NAS", html_url=None),
        OpmlFeed(xml_url=FEED, title="Blog", html_url=None)))
    assert controller.follow_all(document) == (1, 0, 2)
    assert [s.url for s in controller.sources()] == [FEED]


def test_a_list_is_read_from_its_address(harness):
    answers = []
    harness.controller.read_list(LIST, on_done=lambda doc, error: answers.append((doc, error)))
    settle()
    document, error = answers[0]
    assert error == "" and len(document.feeds) == len(parse_opml(OPML).feeds)


def test_an_address_that_holds_no_list(harness):
    answers = []
    harness.controller.read_list("https://reader.example/not-a-list.opml",
                                 on_done=lambda doc, error: answers.append((doc, error)))
    settle()
    assert answers == [(None, NO_SOURCES_IN_LIST)]


class TestFilesAndLinks:
    def read(self, controller, tmp_path, name, data):
        path = tmp_path / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        answers = []
        controller.read_file(str(path), on_done=lambda *answer: answers.append(answer))
        settle()
        return answers[0]

    def test_a_wordpress_export_opens_its_posts(self, harness, tmp_path):
        collection, document, error = self.read(harness.controller, tmp_path, "blog.xml", WXR)
        assert document is None and error == ""
        assert collection.kind == "file" and collection.label == "blog.xml"
        # Published posts and pages; the draft stays out.
        assert sorted(post.title for post in collection.posts()) == ["A Page",
                                                                     "Published Post"]
        assert harness.controller.collections("file", "link") == [collection]
        # Only shown, never followed.
        assert harness.controller.sources() == []

    def test_a_zip_export_opens_its_posts(self, harness, tmp_path):
        collection, _document, error = self.read(harness.controller, tmp_path, "medium.zip",
                                                 medium_zip())
        assert error == "" and collection.items

    def test_a_list_of_sources_in_a_file(self, harness, tmp_path):
        collection, document, error = self.read(harness.controller, tmp_path, "subs.opml",
                                                OPML)
        assert collection is None and error == ""
        assert document.feeds

    def test_a_file_that_is_nothing_to_import(self, harness, tmp_path):
        collection, document, error = self.read(harness.controller, tmp_path, "notes.txt",
                                                "just some words")
        assert (collection, document) == (None, None)
        assert error

    def test_a_link_opens_like_a_file_and_goes_when_removed(self, harness):
        controller = harness.controller
        result, _error = look_up(controller, FEED)
        changed = []
        controller.sources_changed.connect(lambda: changed.append(1))
        link = controller.open_posts(kind="link", label="A post", items=result.feed.items,
                                     source_url=FEED)
        assert changed and controller.collection(link.id) is link
        other = controller.open_posts(kind="link", label="Another", items=[])
        assert other.id != link.id
        controller.close_collection(link.id)
        assert controller.collection(link.id) is None
        assert controller.collections("file", "link") == [other]

    def test_files_go_with_the_account(self, harness):
        controller = harness.controller
        controller.open_posts(kind="file", label="blog.xml", items=[])
        controller.account_changed(None)
        settle()
        assert controller.collections() == []


def test_a_followed_sources_older_posts_are_checked_against_drafts(harness):
    # The posts of a newly followed feed are looked up like the first
    # check's (another app may have imported them already).
    controller = harness.controller
    asked = []
    controller.catalogue.look_up = lambda profile, tags, on_ready, on_unavailable: asked.append(
        sorted(tags))
    result, _error = look_up(controller, FEED)
    controller.follow(result.url, result=result)
    assert asked and len(asked[-1]) == 2
    assert all(post.state == OLDER for post in controller.page(View(OLDER_POSTS)))
