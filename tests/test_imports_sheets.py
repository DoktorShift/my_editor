# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Imports window's sheets: Follow a Website, Import a File, Import a
Link.

Each shows what it found before anything is kept, and its default button
says what it will do: Continue, Follow, Show Source, Import Once, Follow
All. Nothing is stored before that button is pressed.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, Qt, QUrl

from nostr.imports.feed_list import source_key
from nostr.imports.sources.opml import parse_opml
from nostr.imports_controller import NO_SOURCES_IN_LIST
from nostr.ui.imports_sheets import (
    FOLLOWED,
    FOUND,
    IDLE,
    INVALID_ADDRESS,
    NEEDS_ADDRESS,
    SINGLE_POST,
    SOURCES,
    FileSheet,
    FollowSheet,
    LinkSheet,
    dropped_file,
)
from tests.accessibility import unnamed_controls
from tests.imports_fakes import TWO_ITEM_FEED, FakeFetcher, rss_feed
from tests.outbox_fakes import settle
from tests.test_imports_controller import Harness, profile
from tests.test_imports_files import OPML, WXR
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


FEED = "https://blog.example/feed"
LIST = "https://reader.example/subscriptions.opml"
POST = "https://raw.githubusercontent.com/owner/repo/main/hello.md"


@pytest.fixture
def controller(tmp_path):
    fetcher = FakeFetcher({FEED: ("ok", TWO_ITEM_FEED), LIST: ("ok", OPML),
                           POST: ("ok", "---\ntitle: Hello\n---\nA single post.\n")})
    h = Harness(tmp_path, fetcher=fetcher)
    h.controller.account_changed(profile())
    settle()
    yield h.controller
    h.controller.account_changed(None)
    settle()


def buttons(sheet):
    """The buttons, leading to trailing, and the default one."""
    row = sheet._button_row
    shown = [row.itemAt(i).widget() for i in range(row.count())
             if row.itemAt(i).widget() is not None]
    default = next((b.text() for b in shown if b.isDefault()), None)
    return [b.text() for b in shown], default


def enabled(sheet, key):
    return sheet.buttons[key].isEnabled()


@pytest.fixture
def follow(controller):
    sheet = FollowSheet(controller)
    sheet.show()
    yield sheet
    sheet.close()


def type_address(sheet, address):
    sheet.address.setText(address)
    sheet.look_up()
    settle()


class TestFollow:
    def test_a_sheet_of_the_window(self, follow):
        assert follow.windowModality() == Qt.WindowModality.WindowModal

    def test_continue_waits_for_an_address(self, follow):
        assert buttons(follow) == (["Cancel", "Continue"], "Continue")
        assert not enabled(follow, "continue")
        follow.address.setText("blog.example")
        assert enabled(follow, "continue")

    def test_a_feed_is_shown_before_it_is_followed(self, follow, controller):
        type_address(follow, FEED)
        assert follow.state == FOUND
        assert follow.card.title.text() == "My Blog"
        assert follow.card.detail.text().startswith("Feed  ·  2 posts found, newest ")
        assert follow.note.text() == "MyEditor checks this feed for new posts while it's open."
        assert buttons(follow) == (["Cancel", "Follow"], "Follow")
        # Nothing is kept before Follow.
        assert controller.sources() == []

    def test_follow_keeps_it_and_says_which(self, follow, controller):
        followed = []
        follow.followed.connect(followed.append)
        type_address(follow, FEED)
        follow.buttons["follow"].click()
        assert followed == [source_key(FEED)]
        assert controller.is_followed(FEED)
        assert follow.result() == follow.DialogCode.Accepted

    def test_a_source_followed_already(self, follow, controller):
        controller.follow(FEED)
        shown = []
        follow.show_source.connect(shown.append)
        type_address(follow, FEED)
        assert follow.state == FOLLOWED
        assert follow.note.text() == "You already follow this source."
        assert buttons(follow) == (["Cancel", "Show Source"], "Show Source")
        follow.buttons["show"].click()
        assert shown == [source_key(FEED)]

    def test_a_single_post_is_imported_once(self, follow, controller):
        once = []
        follow.import_once.connect(once.append)
        type_address(follow, POST)
        assert follow.state == SINGLE_POST
        assert follow.card.detail.text().startswith("Single post")
        assert "not kept as a source" in follow.note.text()
        assert buttons(follow) == (["Cancel", "Import Once"], "Import Once")
        follow.buttons["import"].click()
        assert [item.title for item in once[0].feed.items] == ["Hello"]
        assert controller.sources() == []

    def test_a_list_of_sources_from_its_address(self, follow, controller):
        lists = []
        follow.followed_list.connect(lambda *counts: lists.append(counts))
        type_address(follow, LIST)
        count = len(parse_opml(OPML).feeds)
        assert follow.state == SOURCES
        assert follow.note.text() == f"This list holds {count} sources."
        assert buttons(follow)[1] == f"Follow All {count}"
        follow.buttons["follow_all"].click()
        assert lists == [(count, 0, 0)]
        assert len(controller.sources()) == count

    def test_a_list_address_with_no_list(self, controller):
        sheet = FollowSheet(controller)
        sheet.show()
        type_address(sheet, "https://reader.example/missing.opml")
        assert sheet.state == IDLE
        assert not sheet.error.isHidden() and sheet.error.text()
        sheet.close()

    def test_a_list_with_no_sources(self, controller):
        sheet = FollowSheet(controller)
        sheet.use_paste()
        sheet.paste.setPlainText('<?xml version="1.0"?><opml version="2.0"><body>'
                                 '<outline xmlUrl=""/></body></opml>')
        sheet.look_up()
        assert sheet.error.text() == NO_SOURCES_IN_LIST

    def test_a_failure_offers_to_paste_the_feed(self, follow):
        type_address(follow, "https://gone.example/feed")
        assert follow.state == IDLE
        assert not follow.error.isHidden() and follow.error.text()
        assert not follow.paste_button.isHidden()
        follow.paste_button.click()
        assert not follow.paste_box.isHidden()
        assert follow.error.isHidden()

    def test_pasted_xml_needs_its_address(self, follow, controller):
        follow.use_paste()
        follow.address.setText("")
        follow.paste.setPlainText(TWO_ITEM_FEED)
        follow.look_up()
        settle()
        assert follow.error.text() == NEEDS_ADDRESS
        follow.address.setText("https://blocked.example/feed")
        follow.paste.setPlainText(TWO_ITEM_FEED + " ")
        follow.look_up()
        settle()
        assert follow.state == FOUND
        follow.buttons["follow"].click()
        assert controller.is_followed("https://blocked.example/feed")
        # Its posts are there at once, though the site turns readers away.
        assert controller.source(source_key("https://blocked.example/feed")).older_count == 2

    def test_a_pasted_feed_on_this_computer_says_why(self, follow):
        """CP2 review L-e: it said only "This address can't be followed."
        without Q-6's reason, which the lookup path gives."""
        from nostr.imports.errors import LOCAL_NETWORK_MESSAGE
        follow.use_paste()
        follow.address.setText("http://192.168.1.10/feed")
        follow.paste.setPlainText(TWO_ITEM_FEED)
        follow.look_up()
        settle()
        assert follow.state == FOUND            # pasted: nothing was fetched
        follow.buttons["follow"].click()
        assert follow.error.text() == LOCAL_NETWORK_MESSAGE

    def test_a_pasted_list_of_sources(self, follow):
        follow.use_paste()
        follow.paste.setPlainText(OPML)
        follow.look_up()
        assert follow.state == SOURCES

    def test_use_an_address_again(self, follow):
        follow.use_paste()
        follow.use_address()
        assert follow.paste_box.isHidden()

    def test_an_address_nothing_reads(self, follow):
        type_address(follow, "not an address at all")
        assert follow.error.text() == INVALID_ADDRESS

    def test_a_changed_address_forgets_what_was_found(self, follow):
        type_address(follow, FEED)
        follow.address.setText(FEED + "/other")
        assert follow.state == IDLE
        assert follow.card.isHidden()
        assert buttons(follow)[1] == "Continue"

    def test_what_works_here(self, follow):
        assert follow.examples.isHidden()
        follow.examples_button.click()
        assert not follow.examples.isHidden()
        assert "WordPress: example.com/feed/" in follow.examples.text()
        assert follow.examples_button.text() == "Hide What Works Here"
        follow.examples_button.click()
        assert follow.examples.isHidden()

    def test_an_answer_after_cancel_changes_nothing(self, controller):
        held = []
        controller.look_up = lambda address, on_done: held.append(on_done)
        sheet = FollowSheet(controller)
        sheet.show()
        sheet.address.setText(FEED)
        sheet.look_up()
        assert sheet.state == "looking"
        assert not enabled(sheet, "continue")
        sheet.reject()
        held[0](None, "late")
        assert sheet.error.isHidden()

    def test_opened_with_an_address_looks_it_up(self, controller):
        sheet = FollowSheet(controller, address=FEED)
        sheet.show()
        sheet.look_up()
        settle()
        assert sheet.state == FOUND
        sheet.close()


class TestFile:
    def write(self, tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_choose_file_is_the_default(self, controller):
        sheet = FileSheet(controller)
        assert buttons(sheet) == (["Cancel", "Choose File…"], "Choose File…")
        assert sheet.windowModality() == Qt.WindowModality.WindowModal

    def test_an_export_opens_in_the_window(self, controller, tmp_path):
        sheet = FileSheet(controller)
        sheet.show()
        opened = []
        sheet.opened.connect(opened.append)
        sheet.read(self.write(tmp_path, "blog.xml", WXR))
        settle()
        assert len(opened) == 1
        assert controller.collection(opened[0]).label == "blog.xml"
        assert sheet.result() == sheet.DialogCode.Accepted

    def test_a_list_of_sources_offers_follow_all(self, controller, tmp_path):
        sheet = FileSheet(controller)
        sheet.show()
        lists = []
        sheet.followed_list.connect(lambda *counts: lists.append(counts))
        sheet.read(self.write(tmp_path, "subs.opml", OPML))
        settle()
        count = len(parse_opml(OPML).feeds)
        texts, default = buttons(sheet)
        assert texts == ["Choose Another File…", "Cancel", f"Follow All {count}"]
        assert default == f"Follow All {count}"
        sheet.buttons["follow_all"].click()
        assert lists == [(count, 0, 0)]

    @pytest.mark.parametrize("mac", [True, False])
    def test_return_after_reading_chooses_the_sheets_action(self, controller, tmp_path,
                                                             monkeypatch, mac):
        # Review M1 of the merge: on Windows and Linux, reading dimmed the
        # focused Choose File…, the focus fell to the "Show Where to Find
        # Your Export" link, and Return pressed that instead of Follow All.
        # A link never takes Return, and the focus goes to the action.
        import alerts
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        monkeypatch.setattr(alerts, "IS_MAC", mac)
        sheet = FileSheet(controller)
        sheet.show()
        sheet.activateWindow()
        settle()                                    # in front, as when a person uses it
        lists = []
        sheet.followed_list.connect(lambda *counts: lists.append(counts))
        sheet.buttons["choose"].setFocus(Qt.FocusReason.TabFocusReason)
        sheet.read(self.write(tmp_path, "subs.opml", OPML))
        settle()
        assert QApplication.focusWidget() is not sheet.guides_button
        QTest.keyClick(QApplication.focusWidget() or sheet, Qt.Key.Key_Return)
        assert lists == [(len(parse_opml(OPML).feeds), 0, 0)]

    def test_a_ghost_export_asks_where_the_blog_lives(self, controller, tmp_path):
        from tests.test_imports_files import GHOST_4
        sheet = FileSheet(controller)
        sheet.show()
        opened = []
        sheet.opened.connect(opened.append)
        sheet.read(self.write(tmp_path, "ghost.json", GHOST_4))
        settle()
        assert not sheet.site.isHidden()
        assert buttons(sheet) == (["Continue Without Address", "Cancel", "Continue"],
                                  "Continue")
        assert not enabled(sheet, "continue")
        sheet.site_address.setText("blog.example")
        assert enabled(sheet, "continue")
        sheet.buttons["continue"].click()
        settle()
        post = controller.collection(opened[0]).items[0]
        assert post.link == "https://blog.example/first-post/"
        assert post.image == "https://blog.example/content/images/hero.jpg"

    def test_a_ghost_export_opens_without_an_address_too(self, controller, tmp_path):
        from tests.test_imports_files import GHOST_4
        sheet = FileSheet(controller)
        sheet.show()
        opened = []
        sheet.opened.connect(opened.append)
        sheet.read(self.write(tmp_path, "ghost.json", GHOST_4))
        settle()
        sheet.buttons["without"].click()
        settle()
        assert len(opened) == 1

    def test_a_file_that_is_nothing_to_import(self, controller, tmp_path):
        sheet = FileSheet(controller)
        sheet.show()
        sheet.read(self.write(tmp_path, "notes.txt", "just words"))
        settle()
        assert not sheet.error.isHidden() and sheet.error.text()
        assert buttons(sheet)[1] == "Choose File…"
        sheet.close()

    def test_where_to_find_an_export(self, controller):
        sheet = FileSheet(controller)
        sheet.guides_button.click()
        assert "WordPress: Tools > Export, then choose the .xml file here." in \
            sheet.guides.text()

    def test_a_dropped_file_is_read(self, controller, tmp_path):
        path = self.write(tmp_path, "blog.xml", WXR)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        assert dropped_file(mime) == path
        web = QMimeData()
        web.setUrls([QUrl("https://example.com/feed")])
        assert dropped_file(web) == ""
        assert dropped_file(None) == ""

    def test_a_chosen_file_is_read_in_the_apps_spelling(self, controller, tmp_path,
                                                         monkeypatch):
        # Qt's open panel answers with forward slashes, also on Windows.
        (tmp_path / "sub").mkdir()
        path = self.write(tmp_path, "blog.xml", WXR)
        asked = []
        monkeypatch.setattr(controller, "read_file", lambda p, **_kw: asked.append(p))
        FileSheet(controller).read((tmp_path / "sub").as_posix() + "/../blog.xml")
        assert asked == [path]


class TestLink:
    def test_open_is_the_default(self, controller):
        sheet = LinkSheet(controller)
        assert buttons(sheet) == (["Cancel", "Open"], "Open")
        assert not enabled(sheet, "open")

    def test_a_post_opens_in_the_window(self, controller):
        sheet = LinkSheet(controller)
        sheet.show()
        opened = []
        sheet.opened.connect(opened.append)
        sheet.address.setText(POST)
        sheet.open_link()
        settle()
        collection = controller.collection(opened[0])
        assert collection.kind == "link" and collection.label == "Hello"
        assert controller.sources() == []

    def test_a_whole_source_offers_follow(self, controller):
        sheet = LinkSheet(controller)
        sheet.show()
        follow = []
        sheet.follow.connect(follow.append)
        sheet.address.setText(POST)
        assert sheet.source_note.isHidden()
        sheet.address.setText(FEED)
        assert not sheet.source_note.isHidden()
        sheet.follow_button.click()
        assert follow == [FEED]

    def test_a_link_with_nothing_to_import(self, controller):
        sheet = LinkSheet(controller)
        sheet.show()
        sheet.address.setText("https://gone.example/post.md")
        sheet.open_link()
        settle()
        assert not sheet.error.isHidden()
        sheet.close()


@pytest.mark.parametrize("sheet_class", [FollowSheet, FileSheet, LinkSheet])
def test_every_control_has_a_name(controller, sheet_class):
    sheet = sheet_class(controller)
    assert unnamed_controls(sheet) == []


def test_every_state_of_follow_keeps_its_names(follow):
    type_address(follow, FEED)
    assert unnamed_controls(follow) == []
    follow.use_paste()
    assert unnamed_controls(follow) == []


class TestOneArticle:
    """CP2 review N5: a link to one article opened the site's whole feed,
    every post checked ("Create 25 Drafts", 50 signer requests)."""

    ARTICLE = "https://example.com/b/"          # the feed names it without "/"
    ELSEWHERE = "https://example.com/2019/an-old-one"
    PAGE = ("<!doctype html><html><head><title>An old one</title>"
            "<link rel=\"alternate\" type=\"application/rss+xml\" href=\"/feed.xml\">"
            "</head><body><nav>menu</nav><article><h1>An old one</h1>"
            + "<p>Paragraph of the article that is long enough to read. </p>" * 12
            + "</article><footer>footer</footer></body></html>")

    @pytest.fixture
    def links(self, tmp_path):
        fetcher = FakeFetcher({self.ARTICLE: ("ok", self.PAGE),
                               self.ELSEWHERE: ("ok", self.PAGE),
                               "https://example.com/feed.xml": ("ok", TWO_ITEM_FEED),
                               "https://example.com/": ("ok", self.PAGE)})
        h = Harness(tmp_path, fetcher=fetcher)
        h.controller.account_changed(profile())
        settle()
        yield h.controller
        h.controller.account_changed(None)
        settle()

    def open(self, controller, address):
        out = {}
        controller.open_link(address, on_done=lambda collection, error: out.update(
            collection=collection, error=error))
        settle()
        return out

    def test_the_article_is_the_one_post(self, links):
        out = self.open(links, self.ARTICLE)
        posts = out["collection"].posts()
        assert [p.title for p in posts] == ["Second"]
        assert out["collection"].label == "Second"

    def test_an_article_the_feed_no_longer_holds_is_read_from_its_page(self, links):
        out = self.open(links, self.ELSEWHERE)
        (post,) = out["collection"].posts()
        assert post.title == "An old one"
        assert "long enough to read" in links.item(post).content_html

    def test_a_one_post_feed_is_no_shortcut_to_another_post(self, links):
        """CP3 review L1: with one post in the site's feed, that post opened
        whatever article the link named."""
        newest = "https://example.com/2026/the-newest"
        links._fetcher.responses[newest] = ("ok", self.PAGE)
        links._fetcher.responses["https://example.com/feed.xml"] = ("ok", rss_feed([
            {"title": "The newest", "link": newest, "guid": "n1",
             "pubdate": "Mon, 05 Oct 2026 00:00:00 GMT"}]))
        (post,) = self.open(links, self.ELSEWHERE)["collection"].posts()
        assert post.title == "An old one"
        (post,) = self.open(links, newest)["collection"].posts()
        assert post.title == "The newest"

    def test_a_feed_or_a_start_page_opens_whole(self, links):
        assert len(self.open(links, "https://example.com/feed.xml")["collection"].posts()) == 2
        assert len(self.open(links, "https://example.com/")["collection"].posts()) == 2

    def test_the_sheet_calls_only_whole_sources_whole(self, links):
        sheet = LinkSheet(links)
        sheet.show()
        sheet.address.setText(self.ARTICLE)
        assert sheet.source_note.isHidden()
        for whole in ("https://example.com/", "https://example.com/feed.xml",
                      "https://medium.com/feed/@someone", "https://example.com/rss/"):
            sheet.address.setText(whole)
            assert not sheet.source_note.isHidden(), whole
        sheet.close()

    def test_in_the_window_only_that_post_is_there_to_create(self, links):
        from nostr.ui.imports_window import ImportsWindow
        window = ImportsWindow(links)
        window.show()
        window.import_link()
        sheet = window.sheet()
        sheet.address.setText(self.ARTICLE)
        sheet.open_link()
        settle()
        assert window.posts.model_.rowCount() == 1
        assert window.action_bar.create.text() == "Create Draft"
        window.close()
