# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins mentions in the text (the editor's "@", rich_text's mention
functions, the mention picker, Insert > Mention).

What must hold:

  "@" typed at the start of a word (not inside an email address, not in
  code) asks for a person, while a Nostr account is in use. Picking one
  puts "@Name", a link to the person's Nostr address, in place of the
  "@", then a space; Escape keeps the "@". One step to undo. Backspace
  right after a mention takes it away whole.

  Markdown writes a mention as the bare address (NIP-27), which every
  reader shows as the name and the publisher tags (p); read back, it is
  "@Name" again (or the person's short key while the name is unknown).
  Mentions look like names: the link color, no underline.

  The picker shows a person's Nostr address only once their domain has
  confirmed it, and keeps finding people by it; it names its parts for
  screen readers.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown  # noqa: E402
from nostr import bech32  # noqa: E402
from nostr.known_people import KnownPeople, Person  # noqa: E402
from nostr.publisher import extract_inline_mentions  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402
from tests.rich_text_helpers import press, type_text  # noqa: E402

ALICE = "a1" * 32
NPUB = bech32.encode_npub(ALICE)
HREF = "nostr:" + NPUB


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(nostr: bool = True) -> HtmlEditor:
    ed = HtmlEditor()
    ed.set_nostr_check(lambda: nostr)
    asked = []
    ed.mention_requested.connect(asked.append)
    ed.asked = asked
    return ed


@pytest.mark.parametrize("typed, asks", [
    ("@", True), ("Hi @", True), ("(@", True), ("me@", False), ("x@", False),
])
def test_an_at_sign_starting_a_word_asks_for_a_person(typed, asks):
    ed = editor()
    type_text(ed, typed)
    assert (ed.asked == [len(typed) - 1]) is asks


def test_no_mention_without_an_account_or_in_code():
    ed = editor(nostr=False)
    type_text(ed, "@")
    assert ed.asked == []
    code = editor()
    code.make_code_block("")
    type_text(code, "@")
    assert code.asked == []


def test_a_picked_person_takes_the_place_of_the_at_sign():
    ed = editor()
    type_text(ed, "Hi @")
    ed.insert_mention(ed.asked[0], HREF, "Alice")
    type_text(ed, "and you")
    assert ed.toPlainText() == "Hi @Alice and you"
    assert document_to_markdown(ed.document()) == f"Hi {HREF} and you\n"
    assert extract_inline_mentions(document_to_markdown(ed.document())) == [(ALICE, "")]
    for _letter in "and you":
        ed.document().undo()
    ed.document().undo()                              # the mention, as one step
    assert ed.toPlainText() == "Hi @"


def test_backspace_after_a_mention_takes_it_away_whole():
    ed = editor()
    type_text(ed, "Hi @")
    ed.insert_mention(ed.asked[0], HREF, "Alice")
    press(ed, Qt.Key.Key_Backspace)                   # the space
    press(ed, Qt.Key.Key_Backspace)                   # the whole mention
    assert ed.toPlainText() == "Hi "


def test_a_mention_is_one_unit_for_typing_delete_and_paste():
    def mentioned():
        ed = editor()
        type_text(ed, "Hi @")
        ed.insert_mention(ed.asked[0], HREF, "Alice")
        type_text(ed, "and you")
        return ed

    def caret(ed, position):
        cursor = ed.textCursor()
        cursor.setPosition(position)
        ed.setTextCursor(cursor)

    ed = mentioned()
    caret(ed, len("Hi @Ali"))
    type_text(ed, "ce, ")                             # goes after the name, kept
    assert ed.toPlainText() == "Hi @Alicece,  and you"
    # (Written apart from the address, which would otherwise take the letters in.)
    assert document_to_markdown(ed.document()) == f"Hi {HREF} ce,  and you\n"
    ed = mentioned()
    caret(ed, len("Hi "))
    press(ed, Qt.Key.Key_Delete)                      # at its start: the whole mention
    assert ed.toPlainText() == "Hi  and you"
    ed = mentioned()
    caret(ed, len("Hi @Al"))
    press(ed, Qt.Key.Key_Backspace)                   # inside it: the whole mention too
    assert ed.toPlainText() == "Hi  and you"
    ed = mentioned()
    caret(ed, len("Hi @Al"))
    pasted = QMimeData()
    pasted.setText("dear")
    ed.insertFromMimeData(pasted)
    assert "@Alicedear" in ed.toPlainText()
    assert "dear" in document_to_markdown(ed.document())


def test_a_mention_read_back_from_markdown_has_its_name_again():
    from main_window import MainWindow
    ed = HtmlEditor()
    ed._mention_label = lambda entity: "Alice" if entity == NPUB else "?"
    MainWindow._load_markdown(ed, f"Hello {HREF} there, `{HREF}` stays code.\n")
    assert ed.toPlainText() == f"Hello @Alice there, {HREF} stays code."
    assert document_to_markdown(ed.document()) == (
        f"Hello {HREF} there, `{HREF}` stays code.\n")
    unknown = HtmlEditor()
    MainWindow._load_markdown(unknown, f"Hello {HREF}\n")
    assert unknown.toPlainText() == f"Hello @{NPUB[:9]}…{NPUB[-4:]}"


def test_a_mention_looks_like_a_name():
    from highlighter import RichTextLook
    ed = editor()
    type_text(ed, "@")
    ed.insert_mention(0, HREF, "Alice")
    look = RichTextLook(ed.document(), is_dark=False)
    look.rehighlight()
    ranges = ed.document().begin().layout().formats()          # kept: formats live in it
    mention = [r for r in ranges if r.start == 0]
    assert mention and not mention[0].format.fontUnderline()
    assert mention[0].format.fontWeight() == 500


# -- the picker ------------------------------------------------------------------------------

class FakeCheck(QObject):
    checked = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.verdicts = {}

    def confirmed(self, pubkey, text):
        return self.verdicts.get((pubkey, text))


class FakeSearch(QObject):
    results = Signal(str, list)

    def search(self, query):
        pass

    def cancel(self):
        pass


def test_the_picker_shows_an_address_only_once_confirmed(tmp_path):
    from nostr.ui.mention_picker import MentionPicker
    people = KnownPeople(tmp_path / "people.json")
    people.upsert(Person(pubkey=ALICE, display_name="Alice", nip05="alice@example.com",
                         source="contact"))
    check = FakeCheck()
    picker = MentionPicker(people, FakeSearch(), address_check=check, is_dark=False)
    picker.open_at(picker.pos())
    person = people.get(ALICE)
    assert picker.confirmed_address(person) == ""              # not yet answered
    spoken = picker._list.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
    assert spoken == "Alice, Following"
    check.verdicts[(ALICE, "alice@example.com")] = True
    check.checked.emit(ALICE, "alice@example.com")
    spoken = picker._list.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
    assert spoken == "Alice, alice@example.com, Following"
    assert unnamed_controls(picker) == []
    # Searching by the address finds her, confirmed or not.
    check.verdicts.clear()
    picker._search_edit.setText("alice@exa")
    assert picker._list.count() == 1
    picker.hide()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_return_in_the_picker_picks_the_person_on_every_system(tmp_path, key):
    from PySide6.QtTest import QTest
    from nostr.ui.mention_picker import MentionPicker
    people = KnownPeople(tmp_path / "people.json")
    people.upsert(Person(pubkey=ALICE, display_name="Alice", source="contact"))
    picker = MentionPicker(people, FakeSearch(), is_dark=False)
    picked = []
    picker.picked.connect(picked.append)
    picker.open_at(picker.pos())
    QTest.keyClicks(picker._search_edit, "Ali")
    QTest.keyClick(picker._search_edit, key)
    assert [person.display_name for person in picked] == ["Alice"]
    assert not picker.isVisible()


# -- in the window ----------------------------------------------------------------------------

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from markdown_writer import document_to_markdown
from nostr.known_people import Person
from tests.rich_text_helpers import type_text

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
r = {"slash_without": "Insert Mention…" in [c.title for c in w.commands.rank(
    "mention", nostr=w.nostr_state.active)]}
type_text(ed, "Hi @")
r["picker_without"] = w._mention_picker is not None and w._mention_picker.isVisible()
w._fill_insert_menu()
r["insert_without"] = "nostr.insert_mention" in [a.objectName() for a in w.m_insert.actions()]
w.nostr_state._active = True
w._fill_insert_menu()
r["insert_with"] = "nostr.insert_mention" in [a.objectName() for a in w.m_insert.actions()]
alice = Person(pubkey="a1" * 32, display_name="Alice", relay_hint="wss://relay.example.com")
w._known_people.upsert(alice)
type_text(ed, " @")
picker = w._mention_picker
r["picker"] = picker is not None and picker.isVisible()
picker.picked.emit(alice)
app.processEvents()
r["text"] = ed.toPlainText()
r["markdown"] = document_to_markdown(ed.document())
r["slash_with"] = "Insert Mention…" in [c.title for c in w.commands.rank(
    "mention", nostr=True)]
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_mentions_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["slash_without"] is False and r["picker_without"] is False
    assert r["insert_without"] is False and r["insert_with"] is True       # D-1
    assert r["picker"]
    assert r["text"] == "Hi @ @Alice "
    nprofile = bech32.encode_nprofile(ALICE, ["wss://relay.example.com"])
    assert r["markdown"] == f"Hi @ nostr:{nprofile}\n"
    assert r["slash_with"]
