# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export Sources: every source in an OPML file other readers import.

The file reads back with the importer's own OPML reader, whatever the
names hold (ampersands, quotes, angle brackets, a line break), and a
file that cannot be written says why.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from PySide6.QtWidgets import QFileDialog

from nostr.imports.sources.opml import OpmlFeed, is_opml, parse_opml, write_opml
from nostr.ui.imports_window import ImportsWindow
from tests.outbox_fakes import settle
from tests.test_imports_controller import NOSTR_AUTHOR
from tests.test_imports_window import FIELD, JOURNAL, controller_for, fill
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


WHEN = datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)


def test_the_file_reads_back_with_the_importer():
    feeds = [OpmlFeed("https://a.example/feed?x=1&y=2", 'Ben & Jerry\'s "<best>" blog',
                      "https://a.example/"),
             OpmlFeed("https://b.example/rss.xml", "Line\nbreak", None),
             OpmlFeed(NOSTR_AUTHOR, "An author", None)]
    text = write_opml(feeds, title="My <sources>", created=WHEN)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">')
    assert "<dateCreated>Wed, 07 Oct 2026 09:30:00 GMT</dateCreated>" in text
    assert is_opml(text)
    document = parse_opml(text)
    assert document.title == "My <sources>"
    assert document.feeds == (
        OpmlFeed("https://a.example/feed?x=1&y=2", 'Ben & Jerry\'s "<best>" blog',
                 "https://a.example/"),
        OpmlFeed("https://b.example/rss.xml", "Line break", None),
        OpmlFeed(NOSTR_AUTHOR, "An author", None))
    # Well-formed XML, which is what other readers need first.
    import xml.dom.minidom
    outlines = xml.dom.minidom.parseString(text.encode("utf-8")).getElementsByTagName("outline")
    assert [o.getAttribute("type") for o in outlines] == ["rss", "rss", "rss"]


def test_names_with_characters_xml_cannot_hold_still_make_a_valid_file():
    """CP3 review L10: a name holding U+FFFE or U+FFFF (or a lone
    surrogate) gave a file other readers reject."""
    import xml.dom.minidom
    feeds = [OpmlFeed("https://a.example/feed", "A\ufffeB\uffffC\ud800D", None)]
    text = write_opml(feeds, title="Mine\uffff", created=WHEN)
    xml.dom.minidom.parseString(text.encode("utf-8"))
    assert parse_opml(text).feeds[0].title == "A B C D"


@pytest.fixture
def controller(tmp_path):
    controller = controller_for(tmp_path)
    fill(controller)
    yield controller
    controller.account_changed(None)
    settle()


def test_every_source_goes_with_its_name_and_website(controller):
    controller.subscriptions.add_feed(NOSTR_AUTHOR, "An author")
    document = parse_opml(controller.sources_as_opml())
    by_url = {feed.xml_url: feed for feed in document.feeds}
    assert set(by_url) == {JOURNAL, FIELD, NOSTR_AUTHOR}
    assert by_url[JOURNAL].title == "A thoughtful journal"
    assert by_url[JOURNAL].html_url == "https://journal.example"
    assert by_url[NOSTR_AUTHOR].html_url is None       # no website to name
    assert document.title == "MyEditor Sources"


class TestFromTheWindow:
    @pytest.fixture
    def window(self, controller):
        win = ImportsWindow(controller, load_settings=lambda: {},
                            save_settings=lambda value: None)
        win.show()
        settle()
        yield win
        win.close()

    def test_it_is_in_the_add_menu_apart_from_the_ways_to_add(self, window):
        menu = window.add_button.menu()
        menu.aboutToShow.emit()
        actions = menu.actions()
        assert [a.text() for a in actions] == [
            "Follow a Website…", "Import a File…", "Import a Link…", "",
            "Export Sources…"]
        assert actions[3].isSeparator()
        assert window.act_export.isEnabled()

    def test_without_sources_there_is_nothing_to_export(self, tmp_path):
        controller = controller_for(tmp_path / "empty")
        win = ImportsWindow(controller, load_settings=lambda: {},
                            save_settings=lambda value: None)
        win.add_button.menu().aboutToShow.emit()
        assert not win.act_export.isEnabled()
        controller.account_changed(None)

    def test_the_save_panel_is_a_sheet_with_a_name(self, window, monkeypatch):
        opened = []
        monkeypatch.setattr(QFileDialog, "open", lambda dialog: opened.append(dialog))
        window.act_export.trigger()
        (dialog,) = opened
        assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
        assert dialog.defaultSuffix() == "opml"
        assert dialog.parent() is window

    def test_the_chosen_file_gets_the_list(self, window, tmp_path):
        path = tmp_path / "Sources.opml"
        window.write_sources(str(path))
        text = path.read_text(encoding="utf-8")
        assert {f.xml_url for f in parse_opml(text).feeds} == {JOURNAL, FIELD}

    def test_a_place_that_cannot_be_written_says_so(self, window, tmp_path, monkeypatch):
        told = []
        import alerts
        monkeypatch.setattr(alerts, "inform", lambda parent, **kw: told.append(kw))
        window.write_sources(str(tmp_path / "missing folder" / "Sources.opml"))
        (said,) = told
        assert said["title"] == "Couldn't save “Sources.opml”"
        assert said["message"].endswith("Choose another place and export again.")
