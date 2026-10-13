# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Publish as Article and Publish as Note as sheets on the document's
window (nostr/ui/publish_article_dialog.py, publish_note_dialog.py and
publish_sheet.py).

What must hold:

  Both are sheets: window-modal, a sheet on macOS, opened with open()
  (never exec()); what changed in Publish as Article goes back to the
  document when it closes, published or not. A note is composed for
  sharing and changes nothing in the document.

  Publish as Article has no second copy of the text: it publishes the
  document's text. Its labels are in sentence case and end in a colon,
  at the trailing edge of their column on a Mac. The card, named
  "Preview", shows the title, summary, cover and the author with the
  reading time, and follows the fields. Hashtags are tags and are
  published as t tags. The link name follows the title until it is
  typed, shows how it ends the article's address, sits under Show
  Details (Show Details / Hide Details), and an article that has one
  keeps it.

  People is everyone the event tags, by name, never by key: those the
  text mentions (read-only, "mentioned"), the authors of what it quotes
  ("quoted"), and the co-authors ("co-author"), added with the person
  picker and removed with x as in Article Details; the event's p tags
  are exactly the people shown.

  A failure is said in the status line in the theme's red; every
  control has a name; Tab goes top to bottom, left to right.

  In a note, a mention shows as "@Name", whether it was in the text,
  pasted, or chosen after typing "@"; Backspace takes it away whole;
  published and copied it is the person's nostr: address.
"""

import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPlainTextEdit, QTextEdit, QWidget,
)

import theme  # noqa: E402
from nostr import bech32  # noqa: E402
from nostr.article_details import AUTHOR_MARKER, ArticleDetails  # noqa: E402
from nostr.blossom.store import MediaFile  # noqa: E402
from nostr.known_people import KnownPeople, Person  # noqa: E402
from nostr.ui import publish_article_dialog as article_module  # noqa: E402
from nostr.ui import publish_note_dialog as note_module  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    PK, FakeClient, FakePool, FakeRelayDirectory, FakeSessionPool, HandPool,
)

BOB = bech32.decode_npub("npub1f2ktq2xcmh9wpmv0s6wqu6r9uvlvp7qr6sdqqk87x3dmnc9w0juswgtagx")
BOB_NPUB = bech32.encode_npub(BOB)
CAROL = "c3" * 32
STRANGER = "d4" * 32
NOTE_ID = "e5" * 32
RELAY = "wss://relay.example.com"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Captured:
    """Stands in for PublishJob: keeps the event it was given."""

    made = []

    def __init__(self, **kwargs):
        Captured.made.append(kwargs["unsigned_event"])
        for name in ("status_changed", "signed", "completed", "failed"):
            setattr(self, name, types.SimpleNamespace(connect=lambda *_a: None))

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    Captured.made = []
    monkeypatch.setattr(article_module, "PublishJob", Captured)
    monkeypatch.setattr(note_module, "PublishJob", Captured)
    return Captured.made


def common(tmp_path, **more):
    from nostr.avatar_store import AvatarStore
    from nostr.profiles import Profile, ProfileStore
    from nostr.search import Nip50SearchClient
    store = ProfileStore(path=tmp_path / "profiles.json")
    profile = Profile(user_pubkey=PK, bunker_pubkey="b" * 64,
                      bunker_relays=["wss://bunker.example"], local_secret_hex="0" * 64,
                      display_name="DrShift")
    store.upsert(profile)
    people = KnownPeople(path=tmp_path / "people.json")
    people.upsert(Person(pubkey=BOB, display_name="Bob Writer"))
    people.upsert(Person(pubkey=CAROL, display_name="Carol"))
    args = dict(active_profile=profile, store=store, relay_pool=FakePool(),
                relay_directory=FakeRelayDirectory(), session_pool=FakeSessionPool(FakeClient()),
                known_people=people, search_client=Nip50SearchClient(HandPool(), people),
                avatars=AvatarStore(), is_dark=False)
    args.update(more)
    return args


def article(tmp_path, body=None, **more):
    more.setdefault("published_at_lookup", lambda author, slug, done: None)
    body = body if body is not None else f"*Fafuui S s*nostr:{BOB_NPUB}\n\nThe article."
    return article_module.PublishArticleDialog(body_markdown=body, **common(tmp_path, **more))


def note(tmp_path, content=None, **more):
    content = content if content is not None else f"Thanks nostr:{BOB_NPUB} for the review!"
    return note_module.PublishNoteDialog(content=content, **common(tmp_path, **more))


def p_tags(event):
    return [tag for tag in event["tags"] if tag[0] == "p"]


# -- both: sheets on the document's window ------------------------------------------------

@pytest.mark.parametrize("make", [article, note])
def test_both_are_sheets_on_their_window(tmp_path, make):
    sheet = make(tmp_path)
    assert sheet.windowModality() == Qt.WindowModality.WindowModal
    assert bool(sheet.windowFlags() & Qt.WindowType.Sheet & ~Qt.WindowType.Window) == (
        sys.platform == "darwin")
    sheet.close()


def test_the_window_opens_them_as_sheets_and_takes_back_what_changed():
    import inspect
    from main_window import MainWindow
    for handler in (MainWindow._on_nostr_publish_article, MainWindow._on_nostr_publish_note):
        source = inspect.getsource(handler)
        assert "dialog.open()" in source and ".exec()" not in source
    article_source = inspect.getsource(MainWindow._on_nostr_publish_article)
    assert "dialog.finished.connect" in article_source
    # A note is composed for sharing: nothing goes back to the document.
    assert "finished" not in inspect.getsource(MainWindow._on_nostr_publish_note)


def test_what_changed_goes_back_when_the_sheet_closes_published_or_not(tmp_path):
    from main_window import MainWindow
    from editor import HtmlEditor
    sheet = article(tmp_path)
    ed = HtmlEditor()
    set_ = []
    window = types.SimpleNamespace(_set_article_details=lambda e, d: set_.append((e, d)))
    MainWindow._article_sheet_closed(window, sheet, ed)
    assert set_ == []                                         # nothing changed
    sheet._summary_edit.setText("One sentence.")
    sheet.reject()
    MainWindow._article_sheet_closed(window, sheet, ed)
    assert set_[0][0] is ed and set_[0][1].summary == "One sentence."


# -- Publish as Article ---------------------------------------------------------------------

def test_it_publishes_the_documents_text_and_has_no_copy_of_it(tmp_path, captured):
    sheet = article(tmp_path, body="# Hello\n\nBody.", default_title="Hello")
    assert sheet.findChildren(QPlainTextEdit) == [] and sheet.findChildren(QTextEdit) == []
    sheet._on_publish()
    # (no date known: asked of the relays first)
    sheet._on_published_at("hello", PK, article_module.FirstPublication(article_module.NEVER))
    assert captured[0]["content"] == "# Hello\n\nBody."


def test_labels_are_sentence_case_with_a_colon_on_the_platforms_side(tmp_path):
    sheet = article(tmp_path)
    form = sheet._form
    words = [label.text() for label in sheet._labels if label.text()]
    assert words == ["Title:", "Summary:", "Hashtags:", "Cover image:", "People:",
                     "Link name:", "Cover image address:"]
    assert all(word == word[0] + word[1:].lower() for word in words)
    trailing = Qt.AlignmentFlag.AlignRight if sys.platform == "darwin" \
        else Qt.AlignmentFlag.AlignLeft
    assert form.labelAlignment() & trailing
    assert all(label.alignment() & trailing for label in sheet._labels)
    assert sheet._title_edit.font().pointSizeF() == QApplication.font().pointSizeF()


def test_the_card_shows_the_article_as_readers_see_it_and_follows_the_fields(tmp_path):
    sheet = article(tmp_path, default_title="Fafuui S s @Bob Writer")
    card = sheet._card
    assert card.accessibleName() == "Preview"
    assert card.title.text() == "Fafuui S s @Bob Writer"
    assert card.byline.text() == "DrShift · 1 min read"
    sheet._summary_edit.setText("Why a calm editor matters.")
    sheet._title_edit.setText("A new title")
    assert card.title.text() == "A new title" and card.summary.text() == \
        "Why a calm editor matters."
    assert "A new title" in card.accessibleDescription()
    picture = QPixmap(64, 36)
    picture.fill(QColor("#336699"))
    sheet._cover._picture = picture
    sheet._cover.picture_changed.emit()
    assert not card.cover.pixmap().isNull()
    sheet._title_edit.setText("")
    assert card.title.text() == "Untitled"


def test_hashtags_are_tags_published_as_t_tags(tmp_path, captured):
    sheet = article(tmp_path, default_title="Hello",
                    details=ArticleDetails(hashtags=("bitcoin",)))
    QTest.keyClicks(sheet._hashtags.entry, "#Essay, nostr")
    QTest.keyClick(sheet._hashtags.entry, Qt.Key.Key_Return)
    assert sheet.details().hashtags == ("bitcoin", "Essay", "nostr")
    sheet._publish("hello", sheet._body, 1_800_000_000)
    assert [tag[1] for tag in captured[0]["tags"] if tag[0] == "t"] == [
        "bitcoin", "essay", "nostr"]


def test_the_link_name_follows_the_title_until_typed_and_says_how_it_ends(tmp_path):
    sheet = article(tmp_path, default_title="Fafuui S s @Bob Writer")
    assert sheet._details_toggle.text() == "Show Details"
    assert not sheet._slug_edit.isVisibleTo(sheet)
    sheet._details_toggle.click()
    assert sheet._details_toggle.text() == "Hide Details"
    assert sheet._slug_edit.isVisibleTo(sheet) and sheet._image_edit.isVisibleTo(sheet)
    assert sheet._slug_edit.text() == "fafuui-s-s-bob-writer"
    assert sheet._slug_hint.text() == "…/fafuui-s-s-bob-writer"
    sheet._title_edit.setText(f"Mine nostr:{BOB_NPUB}")
    assert sheet._slug_edit.text() == "mine"                    # never the key
    QTest.keyClicks(sheet._slug_edit, "-too")
    sheet._title_edit.setText("Another title")
    assert sheet._slug_edit.text() == "mine-too"                # typed: it stays


def test_an_article_that_has_a_link_name_keeps_it(tmp_path):
    sheet = article(tmp_path, default_title="T", default_slug="imported-post",
                    details=ArticleDetails(identifier="imported-post", title="T"))
    sheet._title_edit.setText("A new title")
    assert sheet._slug_edit.text() == "imported-post"


def test_people_are_everyone_tagged_by_name_never_by_key(tmp_path, captured):
    quoted = bech32.encode_nevent(NOTE_ID, [RELAY], STRANGER)
    body = (f"*Fafuui S s*nostr:{BOB_NPUB}\n\nAs this note says:\n\nnostr:{quoted}\n")
    sheet = article(tmp_path, body=body, default_title="T",
                    details=ArticleDetails(coauthors=((CAROL, ""),)))
    names = [name for _pubkey, name in sheet._people.people()]
    stranger = "@" not in names[1] and names[1].startswith("npub1") and "…" in names[1]
    assert names[0] == "Bob Writer, mentioned" and stranger and names[1].endswith(", quoted")
    assert names[2] == "Carol, co-author"
    assert not any(BOB_NPUB in label.text() or BOB in label.text()
                   for label in sheet._people.findChildren(QLabel))
    sheet._publish("t", sheet._body, 1_800_000_000)
    tagged = [tag[1] for tag in p_tags(captured[0]) if tag[1] != PK]
    assert tagged == [pubkey for pubkey, _name in sheet._people.people()]


def test_co_authors_are_added_and_removed_as_in_article_details(tmp_path, captured):
    sheet = article(tmp_path, default_title="T")
    sheet._add_coauthor.click()
    picker = sheet._coauthor_picker
    assert picker is not None and picker.isVisible()
    picker.picked.emit(Person(pubkey=CAROL, display_name="Carol", relay_hint=RELAY))
    picker.picked.emit(Person(pubkey=CAROL))                   # twice: nothing
    picker.picked.emit(Person(pubkey=PK))                      # the author: nothing
    assert sheet.details().coauthors == ((CAROL, RELAY),) and sheet.details_changed()
    assert [name for _p, name in sheet._people.people()][-1] == "Carol, co-author"
    sheet._publish("t", sheet._body, 1_800_000_000)
    assert ["p", CAROL, RELAY, AUTHOR_MARKER] in p_tags(captured[0])
    sheet._on_failed("relay down")                            # to try again
    remove = [b for b in sheet.findChildren(QWidget) if b.accessibleName() == "Remove Carol"]
    remove[0].click()
    assert sheet.details().coauthors == ()


def test_a_failure_is_said_in_red_and_publish_can_be_tried_again(tmp_path, captured):
    sheet = article(tmp_path, default_title="T")
    sheet._publish("t", sheet._body, 1_800_000_000)
    # While it goes out, nothing can change, and nothing moves.
    assert not sheet._publish_btn.isEnabled() and not sheet._hashtags.isEnabled()
    assert not sheet._hashtags.entry.isHidden() and not sheet._account.isEnabled()
    sheet._on_completed([("wss://a.example", False, "blocked")])
    assert sheet._hashtags.isEnabled() and sheet._account.isEnabled()
    assert not sheet._status.isHidden() and "no relay took it" in sheet._status.text()
    assert theme.error_text_color(False) in sheet._status.styleSheet()
    assert sheet._publish_btn.isEnabled()


def test_every_control_is_named_and_tab_goes_top_to_bottom(tmp_path):
    sheet = article(tmp_path, default_title="T",
                    details=ArticleDetails(hashtags=("bitcoin",), coauthors=((CAROL, ""),)),
                    media_store=_Store())
    sheet._details_toggle.setChecked(True)
    sheet.show()
    QApplication.processEvents()
    assert unnamed_controls(sheet) == []
    chain, widget = [], sheet._title_edit
    for _step in range(16):
        chain.append(widget.accessibleName() or widget.text())
        widget = widget.nextInFocusChain()
        while not (widget.focusPolicy() & Qt.FocusPolicy.TabFocus
                   and widget.isVisibleTo(sheet)):
            widget = widget.nextInFocusChain()
    left_to_right = sorted((sheet._cancel_btn, sheet._publish_btn), key=lambda b: b.x())
    assert chain[:13] == [
        "Title", "Summary", "Remove bitcoin", "Hashtags", "Choose Cover Image",
        "Remove Carol", "Add Co-Author…", "Hide Details", "Link name",
        "Cover image address", "Publishing as DrShift"] + [b.text() for b in left_to_right]
    sheet.close()


class _Store(QObject):
    """A media store that holds nothing and uploads nothing."""

    upload_finished = Signal(str, object)
    upload_failed = Signal(str, str)
    upload_progress = Signal(str, int, int)
    files: dict = {}

    def upload_bytes(self, body, *, name, mime_type="application/octet-stream"):
        self.uploaded = name


# -- Publish as Note ----------------------------------------------------------------------

def test_a_mention_in_a_note_is_a_name_and_goes_out_as_its_address(tmp_path, captured):
    sheet = note(tmp_path)
    assert sheet._text_edit.toPlainText() == "Thanks @Bob Writer for the review!"
    assert sheet._text_edit.note() == f"Thanks nostr:{BOB_NPUB} for the review!"
    assert [name for _p, name in sheet._people.people()] == ["Bob Writer, mentioned"]
    sheet._on_publish()
    assert captured[0]["content"] == f"Thanks nostr:{BOB_NPUB} for the review!"
    assert [tag[1] for tag in p_tags(captured[0])] == [BOB]


def test_a_pasted_address_shows_the_name_and_copies_as_the_address(tmp_path):
    sheet = note(tmp_path, content="Hello ")
    assert sheet._people_line.isHidden()                    # nobody tagged: no row
    cursor = sheet._text_edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    sheet._text_edit.setTextCursor(cursor)
    data = QMimeData()
    data.setText(f"nostr:{BOB_NPUB} and nostr:{bech32.encode_npub(STRANGER)}")
    sheet._text_edit.insertFromMimeData(data)
    shown = sheet._text_edit.toPlainText()
    assert shown.startswith("Hello @Bob Writer and @npub1") and "nostr:" not in shown
    assert not sheet._people_line.isHidden()
    sheet._text_edit.selectAll()
    assert sheet._text_edit.createMimeDataFromSelection().text() == sheet._text_edit.note()


def test_typing_at_asks_whom_to_mention_and_backspace_takes_it_whole(tmp_path):
    sheet = note(tmp_path, content="Hi")
    sheet.show()
    edit = sheet._text_edit
    edit.setFocus()
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    QTest.keyClicks(edit, " @")
    assert sheet._picker is not None and sheet._picker.isVisible()
    sheet._picker.picked.emit(Person(pubkey=CAROL, display_name="Carol"))
    assert edit.toPlainText() == "Hi @Carol "
    assert edit.note() == f"Hi nostr:{bech32.encode_npub(CAROL)}"
    QTest.keyClick(edit, Qt.Key.Key_Backspace)                # the space
    QTest.keyClick(edit, Qt.Key.Key_Backspace)                # the whole mention
    assert edit.toPlainText() == "Hi "
    QTest.keyClicks(edit, "me@example.com")                   # an email address: no picker
    sheet._picker.hide()
    assert edit.toPlainText() == "Hi me@example.com"
    sheet.close()


def test_the_note_sheet_names_every_control(tmp_path):
    sheet = note(tmp_path)
    assert unnamed_controls(sheet) == []
    sheet.close()


# -- one word for an article's identifier ----------------------------------------------------

def test_an_articles_identifier_is_its_link_name_wherever_it_is_named():
    """Publish as Article, Article Details, Save as Nostr Draft and the
    drafts' messages call the article's identifier its link name: never
    "slug", "article ID" or "d-tag", not even in an example."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "i18n_script", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "i18n.py"))
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    messages, _problems = script.extract()
    named = [key[1] for key in messages
             if re.search(r"slug|article ID|d-tag", key[1], re.IGNORECASE)]
    assert named == []
    from nostr.ui.secret_key_check import SLUG_HOLDS_A_KEY
    assert "link name" in SLUG_HOLDS_A_KEY


def test_the_sheets_never_wrap_the_items_of_a_layout_qt_owns():
    """Asking a QDialogButtonBox's or a QFormLayout's layout for its items
    (itemAt) gives Python wrappers of items Qt deletes on its own: one sheet
    in a few hundred then crashed the app at a later allocation. The
    sheets keep their own lists, and ask the buttons where they are."""
    import inspect
    from nostr.ui import publish_sheet
    for module in (publish_sheet, article_module, note_module):
        assert ".itemAt(" not in inspect.getsource(module), module.__name__


def test_publish_waits_for_a_dropped_cover_and_a_failure_is_said_under_it(
        tmp_path, captured, monkeypatch):
    """Review M2: an article published while its dropped cover was still
    uploading went out without it."""
    from tests.test_cover_field import picture_file
    from nostr.ui import cover_field
    from tests.test_cover_field import FakeLoader
    monkeypatch.setattr(cover_field, "ThumbnailLoader", FakeLoader)
    store = _Store()
    sheet = article(tmp_path, default_title="T", media_store=store)
    sheet._cover.drop(str(picture_file(tmp_path)))
    assert not sheet._publish_btn.isEnabled()
    sheet._on_publish()
    assert captured == []
    url = f"https://blossom.example/{'ef' * 32}.png"
    store.upload_finished.emit(store.uploaded, MediaFile(hash="ef" * 32, url=url,
                                                         mime_type="image/png"))
    assert sheet._publish_btn.isEnabled()
    sheet._publish("t", sheet._body, 1_800_000_000)
    assert ["image", url] in captured[0]["tags"]
    sheet._on_failed("relay down")
    sheet._cover.drop(str(picture_file(tmp_path, "b.png")))
    store.upload_failed.emit(store.uploaded, "the server said no")
    assert sheet._publish_btn.isEnabled()
    assert not sheet._cover_problem.isHidden()
    assert "the server said no" in sheet._cover_problem.text()
