# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that every step of Undo has a name, and the Undo and Redo history
the format toolbar's buttons list (editor.HtmlEditor.undo_history).

What must hold:

  Every change to a document is a step of Undo with the name of what made
  it: the command ("Increase Indent", "Add Row Above", "Remove Link",
  "Code Language", "Delete"), "Typing" for what a key does in the run of
  typing (Tab in code, Backspace taking a divider or a typed bullet away,
  Return or Tab making a table's new row), "Checklist" for a box ticked
  with the pointer, "Drag" for anything dropped. One Undo takes the whole
  step back.

  The history lists those names, newest first; moving back k steps in it
  is exactly k single Undos, and Redo's history works the same way.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor, QTextImageFormat  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import editor as editor_module  # noqa: E402
import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402

K = Qt.Key
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(editor_module, "time", fake)
    return fake


def structured(text=""):
    ed = HtmlEditor()
    ed.resize(500, 400)
    ed.show()
    ed.setFocus()
    ed.set_structure_check(lambda: True)
    if text:
        QTest.keyClicks(ed, text)
    return ed


def caret(ed, position):
    cursor = ed.textCursor()
    cursor.setPosition(position)
    ed.setTextCursor(cursor)


def to_block_start(ed):
    cursor = ed.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    ed.setTextCursor(cursor)


def key(ed, which, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(ed, which, modifiers)


def is_one_named_step(ed, change, name):
    """``change(ed)`` makes one step of Undo called ``name``, and one Undo
    takes all of it back."""
    ed.end_typing_run()
    before_html = ed.document().toHtml()
    before = ed.document().availableUndoSteps()
    change(ed)
    assert ed.document().availableUndoSteps() > before, "nothing changed"
    assert ed.undo_name() == name
    ed.undo()
    assert ed.document().availableUndoSteps() == before
    assert ed.document().toHtml() == before_html
    assert ed.redo_name() == name


def test_indenting_a_list_is_named_by_its_command():
    ed = structured("one")
    ed.toggle_list(rich_text.BULLET)
    key(ed, K.Key_Return)
    QTest.keyClicks(ed, "two")
    is_one_named_step(ed, lambda e: e.change_indent(+1), "Increase Indent")
    ed.change_indent(+1)
    is_one_named_step(ed, lambda e: e.change_indent(-1), "Decrease Indent")
    is_one_named_step(ed, lambda e: key(e, K.Key_Tab), "Increase Indent")
    ed.change_indent(+1)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backtab), "Decrease Indent")


def test_a_typed_bullet_is_indented_and_taken_away_by_named_steps():
    ed = structured()
    ed.set_structure_check(lambda: False)           # plain text: bullets are typed
    ed.textCursor().insertText("• item")
    ed.document().clearUndoRedoStacks()
    to_block_start(ed)
    is_one_named_step(ed, lambda e: key(e, K.Key_Tab), "Increase Indent")
    to_block_start(ed)
    key(ed, K.Key_Tab)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backtab), "Decrease Indent")
    caret(ed, ed.textCursor().block().text().index("•") + 2)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")


def code_block(text="code"):
    ed = structured(text)
    ed.make_code_block("python")
    return ed


def test_the_keys_of_a_code_block_are_typing():
    ed = code_block()
    is_one_named_step(ed, lambda e: key(e, K.Key_Tab), "Typing")
    ed = code_block()
    to_block_start(ed)
    key(ed, K.Key_Tab)
    caret(ed, 4)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")
    ed = code_block()
    is_one_named_step(ed, lambda e: key(e, K.Key_Down), "Typing")
    is_one_named_step(ed, lambda e: key(e, K.Key_Return, CTRL), "Typing")
    to_block_start(ed)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")


def test_indenting_lines_of_code_is_named_by_its_command():
    ed = code_block("a")
    key(ed, K.Key_Return)
    QTest.keyClicks(ed, "b")
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cursor)
    is_one_named_step(ed, lambda e: key(e, K.Key_Tab), "Increase Indent")
    key(ed, K.Key_Tab)
    is_one_named_step(ed, lambda e: key(e, K.Key_Backtab), "Decrease Indent")


def test_a_code_language_is_a_named_step():
    ed = code_block()
    is_one_named_step(ed, lambda e: e.set_code_language("rust"), "Code Language")


@pytest.mark.parametrize("command, name", [
    ("add_row_above", "Add Row Above"), ("add_row_below", "Add Row Below"),
    ("add_column_before", "Add Column Before"), ("add_column_after", "Add Column After"),
    ("delete_row", "Delete Row"), ("delete_column", "Delete Column"),
    ("delete_table", "Delete Table"),
])
def test_a_table_command_is_named_by_its_title(command, name):
    ed = structured()
    ed.insert_table(3, 3)
    key(ed, K.Key_Tab)
    is_one_named_step(ed, lambda e: e.table_command(command), name)


def test_the_keys_that_make_a_table_longer_or_leave_it_are_typing():
    ed = structured()
    ed.insert_table(2, 2)
    QTextCursor(ed.document().lastBlock()).insertText("after")
    for _ in range(3):
        key(ed, K.Key_Tab)                          # to the last cell
    is_one_named_step(ed, lambda e: key(e, K.Key_Tab), "Typing")
    is_one_named_step(ed, lambda e: key(e, K.Key_Return), "Typing")
    is_one_named_step(ed, lambda e: key(e, K.Key_Return, CTRL), "Typing")


def test_backspace_and_delete_take_a_divider_away_as_typing():
    ed = structured("a")
    ed.insert_divider()
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")
    cursor = ed.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
    ed.setTextCursor(cursor)
    assert rich_text.is_divider(ed.textCursor().block())
    is_one_named_step(ed, lambda e: key(e, K.Key_Delete), "Typing")
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")


def test_removing_a_link_and_inserting_a_reference_are_named_steps():
    ed = structured("word")
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cursor)
    ed.apply_link("", "https://example.com")
    caret(ed, 2)
    is_one_named_step(ed, lambda e: e.remove_link(), "Remove Link")
    is_one_named_step(ed, lambda e: e.insert_reference("nostr:nevent1qqqqqq"),
                      "Note or Article")


def test_a_mention_taken_away_with_a_key_is_typing():
    ed = structured("Hi ")
    ed.set_nostr_check(lambda: True)
    at = ed.textCursor().position()
    ed.insert_mention(at, "nostr:npub1" + "q" * 58, "Ann")
    caret(ed, at + 1)
    mention = rich_text.mention_at(ed.textCursor())
    assert mention is not None
    caret(ed, mention[1])
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")
    caret(ed, at)
    is_one_named_step(ed, lambda e: key(e, K.Key_Delete), "Typing")


def media_editor():
    """A document with an audio object ("m") in a paragraph of its own."""
    ed = structured("above")
    outside = types.SimpleNamespace(media=True, text="m", href="", src="")
    ed.set_object_lookup(lambda name: outside if name == "m" else None)
    image = QTextImageFormat()
    image.setName("m")
    rich_text.insert_own_paragraph(ed.textCursor(), image=image)
    ed.document().clearUndoRedoStacks()
    return ed


def test_the_keys_around_audio_or_video_are_typing():
    ed = media_editor()
    media = ed.document().findBlockByNumber(1)
    assert ed.is_media_block(media)
    caret(ed, media.position())
    is_one_named_step(ed, lambda e: key(e, K.Key_Return), "Typing")
    caret(ed, ed.document().findBlockByNumber(2).position())
    is_one_named_step(ed, lambda e: key(e, K.Key_Backspace), "Typing")
    caret(ed, ed.document().findBlockByNumber(0).position() + len("above"))
    is_one_named_step(ed, lambda e: key(e, K.Key_Delete), "Typing")


def test_a_box_ticked_with_the_pointer_is_a_checklist_step():
    ed = structured("task")
    ed.toggle_checklist()
    to_block_start(ed)
    rect = ed.cursorRect()
    box = QPoint(rect.left() - 8, rect.center().y())

    def click(e):
        QTest.mouseClick(e.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, box)

    is_one_named_step(ed, click, "Checklist")


WINDOW_SCRIPT = r"""
import json, os, sys, types
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
main_window.ask = lambda parent, **kw: False          # Keep Local
w = main_window.MainWindow()
w.show()
picture = os.path.join(sys.argv[2], "picture.png")
image = QImage(4, 4, QImage.Format.Format_RGB32)
image.fill(QColor("red"))
image.save(picture)
song = os.path.join(sys.argv[2], "song.mp3")
open(song, "wb").write(b"ID3" + b"sound" * 50)
r = {}

def step(label, ed, change):
    before = ed.document().availableUndoSteps()
    text = ed.toPlainText()
    change()
    after = ed.document().availableUndoSteps()
    name = ed.undo_name()
    ed.undo()
    r[label] = [after > before, name, ed.toPlainText() == text]

ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Some words")
def delete():
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    ed.setTextCursor(cursor)
    w.act_delete.trigger()
step("delete", ed, delete)
step("window_drop", ed, lambda: w._handle_dropped_text(" dropped"))
step("drop_images", ed, lambda: ed.urls_dropped.emit(
    [QUrl.fromLocalFile(picture), QUrl.fromLocalFile(picture)]))
def paste_file():
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(picture)])
    ed.insertFromMimeData(data)
step("paste_file", ed, paste_file)
main_window.resolve_pick = lambda media, **kw: types.SimpleNamespace(
    ok=True, reason="", mime="image/png", url="https://example.com/" + "ab" * 32 + ".png",
    sha256="ab" * 32, size=10)
step("library", ed, lambda: w._insert_media_at_cursor(None, ed, "a picture"))
w.nostr_state._active = True
w._update_nostr_commands()
w._media_store.upload_bytes = lambda body, name, mime_type: None
step("audio", ed, lambda: w._upload_into(ed, song))
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_what_the_window_puts_in_the_text_is_a_named_step(tmp_path):
    """Edit > Delete, words dropped on the window, pictures dropped or
    pasted (several at once: one step), a picture from the media library
    and audio or video: each is one step, named."""
    from tests.app_process import run_window_script
    r = run_window_script(WINDOW_SCRIPT, tmp_path, str(tmp_path))
    assert r == {
        "delete": [True, "Delete", True],
        "window_drop": [True, "Drag", True],
        "drop_images": [True, "Drag", True],
        "paste_file": [True, "Paste", True],
        "library": [True, "Image", True],
        "audio": [True, "Audio or Video", True],
    }
