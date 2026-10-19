# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what a document says about its file, and where the app's version
is said (document_notice.py, the window's notices).

What must hold, as a Mac document app behaves (TextEdit, NSDocument) and
as Apple's guidelines ask (information in context, an alert only when a
decision protects work, nothing over the text at startup):

  A file changed by another app, with nothing unsaved: the document
  follows it by itself, the caret where it was, and the status bar says
  so. With unsaved changes: nothing is lost; a notice says so and offers
  Revert…, which asks first (Cancel keeps the changes and the notice).
  Saving then asks before writing over the newer file (Save Anyway,
  Cancel, Revert…). A file moved or deleted: a notice with Save As….
  Opening a file already open brings its tab forward and says nothing.

  A drop of files MyEditor can't take is not accepted; one that is
  partly so says which it can't open, in an alert.

  A newer version is said by a button at the toolbar's end, never a bar
  over the documents; after an update, What's New, for that session.

  The notice keeps its own height, paints its own background, and
  Escape closes it. A newer draft's notice is the same, and its Reload
  asks first when there are unsaved edits.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from document_notice import CAUTION, DocumentNotice
from nostr.ui.draft_conflict_banner import DraftConflictBanner
from widgets import UpdateButton
from tests.app_process import run_window_script


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# --------------------------------------------------------------------------- #
# The notice
# --------------------------------------------------------------------------- #

def _seen(notice):
    seen = []
    notice.acted.connect(lambda key: seen.append(key))
    notice.closed.connect(lambda: seen.append("closed"))
    return seen


def test_a_notice_says_its_sentence_with_its_actions():
    notice = DocumentNotice(is_dark=False)
    seen = _seen(notice)
    notice.present("“a.md” was changed in another app.", kind=CAUTION,
                   actions=[("revert", "Revert…")])
    assert notice.isVisibleTo(None) or not notice.isHidden()
    assert notice.text() == "“a.md” was changed in another app."
    assert notice.action_labels() == ["Revert…"]
    assert notice.accessibleName() == notice.text()
    notice.trigger("revert")
    assert seen == ["revert"] and notice.isHidden()


def test_presenting_again_replaces_the_actions():
    notice = DocumentNotice()
    notice.present("One.", actions=[("a", "A"), ("b", "B")])
    notice.present("Two.", actions=[("c", "C")])
    assert notice.action_labels() == ["C"]


def test_escape_and_close_keep_everything_as_it_is():
    notice = DocumentNotice()
    seen = _seen(notice)
    notice.present("Something happened.")
    notice.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    assert seen == ["closed"] and notice.isHidden()


def test_a_notice_keeps_its_own_height_and_paints_its_background():
    # The file bar it replaces was stretched to 97 px by the tab's layout
    # and painted no background of its own.
    page = QWidget()
    column = QVBoxLayout(page)
    column.setContentsMargins(0, 0, 0, 0)
    notice = DocumentNotice()
    column.addWidget(notice)
    column.addWidget(QLabel("text"), 1)
    notice.present("“a.md” was moved or deleted.", actions=[("save_as", "Save As…")])
    page.resize(900, 600)
    page.show()
    QApplication.processEvents()
    assert notice.height() <= notice.sizeHint().height() <= 48
    assert notice.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    page.close()


def test_a_notice_wraps_its_sentence_in_a_narrow_window_and_is_never_cut():
    page = QWidget()
    column = QVBoxLayout(page)
    column.setContentsMargins(0, 0, 0, 0)
    notice = DocumentNotice()
    column.addWidget(notice)
    column.addWidget(QLabel("text"), 1)
    notice.present("„Ein sehr langer Dateiname für ein Kapitel.md“ wurde in einer anderen "
                   "App geändert, seit du die Datei geöffnet hast.",
                   kind=CAUTION, actions=[("revert", "Zurücksetzen …")])
    page.resize(380, 600)
    page.show()
    QApplication.processEvents()
    assert notice.height() >= notice.heightForWidth(notice.width())
    page.resize(1400, 600)
    QApplication.processEvents()
    assert notice.height() <= notice.sizeHint().height() + 2, "one line when wide, not stretched"
    page.close()


def test_closing_from_the_keyboard_gives_the_focus_back_to_the_document():
    page = QWidget()
    column = QVBoxLayout(page)
    notice = DocumentNotice()
    document = QLabel("text")
    document.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    column.addWidget(notice)
    column.addWidget(document)
    notice.set_focus_return(document)
    notice.present("Something happened.", actions=[("a", "Act")])
    page.show()
    QApplication.processEvents()
    page.activateWindow()
    notice.findChild(QPushButton).setFocus()
    QApplication.processEvents()
    notice.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    QApplication.processEvents()
    assert QApplication.focusWidget() is document
    page.close()


def test_a_newer_draft_notice_offers_view_and_reload():
    banner = DraftConflictBanner(is_dark=True)
    events = []
    banner.view_remote.connect(lambda: events.append("view"))
    banner.reload.connect(lambda: events.append("reload"))
    banner.dismissed.connect(lambda: events.append("dismissed"))
    assert banner.action_labels() == ["View", "Reload"]
    banner.trigger("reload")
    assert events == ["reload"], "the window takes it away once reloaded"
    banner.trigger("view")
    assert events == ["reload", "view", "dismissed"]


def test_the_update_button_says_the_version_at_the_toolbars_end():
    button = UpdateButton()
    asked = []
    button.update_requested.connect(lambda: asked.append("update"))
    button.whats_new_requested.connect(lambda: asked.append("news"))
    assert button.isHidden()
    button.show_update("4.1.0")
    assert button.text() == "Update Available"
    assert button.toolTip() == "MyEditor 4.1.0 is available."
    button.click()
    assert asked == ["update"] and not button.isHidden()      # Later keeps it
    button.show_updated("4.1.0")
    assert button.text() == "What’s New"
    button.click()
    assert asked == ["update", "news"] and button.isHidden()   # read once


# --------------------------------------------------------------------------- #
# In the window
# --------------------------------------------------------------------------- #

SCRIPT = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from update_check import ReleaseInfo
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

home = os.environ["HOME"]
path = os.path.join(home, "a.md")
with open(path, "w", encoding="utf-8") as f:
    f.write("First line.\n\nSecond line.\n")
answers, asked = {}, []
def confirm(parent, **kw):
    asked.append(kw["title"]); return answers.get("confirm", False)
def ask(parent, **kw):
    asked.append(kw["title"]); return answers.get("ask", False)
def inform(parent, **kw):
    asked.append(kw["title"])
main_window.confirm_destructive = confirm
main_window.ask = ask
main_window.inform = inform

def wait(done, seconds=10.0):
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        app.processEvents(); time.sleep(0.01)

def write(text, target=None):
    before = w._file_changed_count
    with open(target or path, "wb" if isinstance(text, bytes) else "w",
              **({} if isinstance(text, bytes) else {"encoding": "utf-8"})) as f:
        f.write(text)
    wait(lambda: w._file_changed_count > before)
    wait(lambda: False, 0.6)                    # looked at once the writing settles

w = main_window.MainWindow()
w._file_changed_count = 0
w._watcher.fileChanged.connect(lambda _p: setattr(w, "_file_changed_count", w._file_changed_count + 1))
w.show()
ed = w.open_path(path)
notice = w._notice_from_widget(w.tabs.currentWidget())
r = {}

# Nothing unsaved: the document follows its file, the caret where it was.
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit
def mark():
    # A highlight, as the spelling's underline of a word is.
    selection = QTextEdit.ExtraSelection()
    selection.cursor = QTextCursor(ed.document())
    selection.cursor.setPosition(0)
    selection.cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    ed.set_highlights("spelling", [selection])
mark()
cursor = ed.textCursor(); cursor.setPosition(5); ed.setTextCursor(cursor)
write("First line.\n\nSecond line, from another app.\n")
r["followed"] = "from another app" in ed.toPlainText()
r["caret"] = ed.textCursor().position()
r["followed_said"] = w.status.currentMessage()
r["followed_notice"] = notice.isVisible()
r["followed_asked"] = list(asked)
r["highlights_after_reload"] = ed.highlights("spelling")
# Replacing a text with highlights in it, again and again: Qt crashed when
# they were renewed while it read the new text in.
for n in range(20):
    mark()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"First line.\n\nVersion {n}, *from* another app.\n")
    w._reload_from_disk(ed, quiet=True)
r["reloads_with_highlights"] = "Version 19" in ed.toPlainText()

# Unsaved changes: nothing lost, a notice with Revert….
ed.textCursor().insertText("Mine. ")
write("Third version, from another app.\n")
r["kept"] = "Mine." in ed.toPlainText()
r["notice"] = [notice.isVisible(), notice.text(), notice.action_labels()]
w._update_revert_command()
r["revert_command_with_changes"] = w.act_revert.isEnabled()
answers["confirm"] = False
notice.trigger("revert")                        # Revert…, then Cancel
r["revert_cancelled"] = ["Mine." in ed.toPlainText(), notice.isVisible()]
r["revert_asked"] = asked[-1]

# Saving asks before writing over the newer file.
answers["ask"] = False
r["save_cancelled"] = w._save_to(path)
r["disk_after_cancel"] = open(path, encoding="utf-8").read()
r["save_asked"] = asked[-1]
answers["ask"] = True
r["save_anyway"] = w._save_to(path)
r["disk_after_save"] = "Mine." in open(path, encoding="utf-8").read()
r["notice_after_save"] = notice.isVisible()
r["asked_again"] = (w._save_to(path), asked.count(r["save_asked"]))

# Revert… confirmed: the version on disk.
ed.textCursor().insertText("More of mine. ")
write("Fourth version.\n")
answers["confirm"] = True
notice.trigger("revert")
r["reverted"] = [ed.toPlainText().strip(), ed.document().isModified(), notice.isVisible()]
w._update_revert_command()
r["revert_command_when_clean"] = w.act_revert.isEnabled()

# A file it can't read by itself: never a question out of nowhere.
asked.clear()
write(b"\xff\xfe not text")
r["unreadable"] = [list(asked), notice.isVisible(), notice.text(), ed._changed_on_disk]

# The caret never lands inside a character of two units.
write("First.\n")
answers["confirm"] = True
notice.trigger("revert")
cursor = ed.textCursor(); cursor.setPosition(2); ed.setTextCursor(cursor)
write("a\U0001F600bcdef\n")
r["caret_in_emoji"] = ed.textCursor().position()

# A file differing only in the case of its name is the same file.
ed.textCursor().insertText("Mine again. ")
write("Changed once more.\n")
answers["ask"] = False
asked.clear()
w._save_to(os.path.join(home, "A.md"))
r["case_asked"] = asked[:1]

# Revert… at the save question while closing: closing goes on.
answers["ask"] = "revert"
answers["confirm"] = True
main_window._ask_save_changes = lambda parent, title, **kw: "save"
tabs_before = w.tabs.count()
w.close_tab(w._editor_widget_index(ed))
r["closed_after_revert"] = w.tabs.count() == tabs_before - 1
ed = w.open_path(path)
notice = w._notice_from_widget(w.tabs.currentWidget())

# Moved or deleted: the only copy now, so closing asks; put back, followed.
os.remove(path)
wait(notice.isVisible)
wait(lambda: False, 0.6)
r["deleted"] = [notice.text(), notice.action_labels(), ed.document().isModified(),
                w.tabs.tabText(w._editor_widget_index(ed)).endswith("*")]
with open(path, "w", encoding="utf-8") as f:
    f.write("Put back by another app.\n")
wait(lambda: "Put back" in ed.toPlainText(), 5)
r["put_back_clean"] = [notice.isVisible(), ed.toPlainText().strip(), ed.document().isModified()]
# With changes of its own: put back, nothing lost, saving asks.
ed.textCursor().insertText("Mine. ")
os.remove(path)
wait(notice.isVisible)
wait(lambda: False, 0.6)
with open(path, "w", encoding="utf-8") as f:
    f.write("Put back again.\n")
wait(lambda: "another app" in notice.text(), 5)
wait(lambda: False, 0.6)
r["put_back"] = [notice.isVisible(), notice.text(), getattr(ed, "_changed_on_disk", False),
                 "Mine." in ed.toPlainText()]

# Opening a file already open says nothing.
other = os.path.join(home, "b.md")
with open(other, "w", encoding="utf-8") as f:
    f.write("B.\n")
b = w.open_path(other)
w.tabs.setCurrentIndex(0)
w.open_path(other)
r["already_open"] = [w.current_editor() is b, w._notice_from_widget(w.tabs.currentWidget()).isVisible()]

# Drops.
svg = QUrl.fromLocalFile(os.path.join(home, "logo.svg"))
md = QUrl.fromLocalFile(other)
r["drops"] = [w._accepts_drop([svg]), w._accepts_drop([md]), w._accepts_drop([svg, md]),
              w._accepts_drop([QUrl("https://example.com/a")])]
before = len(asked)
w._handle_dropped_urls([svg, md])
app.processEvents()
r["drop_said"] = asked[before:]

# The version, at the toolbar's end.
w._on_auto_update_available(ReleaseInfo("9.9.9", "https://example.invalid", ()))
r["update"] = [w.update_button.isVisible(), w.update_button.text()]
w._skip_update_version("9.9.9")
r["skipped"] = w.update_button.isVisible()

for e in w._all_editors():
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


@pytest.fixture(scope="module")
def r(tmp_path_factory):
    return run_window_script(SCRIPT, tmp_path_factory.mktemp("home"))


def test_with_nothing_unsaved_the_document_follows_its_file(r):
    assert r["followed"] is True and r["caret"] == 5
    # Its highlights went with the old text (the spelling makes them anew).
    assert r["highlights_after_reload"] == [] and r["reloads_with_highlights"] is True
    assert r["followed_said"] == "“a.md” was updated from disk."
    assert r["followed_notice"] is False and r["followed_asked"] == []


def test_unsaved_changes_are_kept_and_the_notice_offers_revert(r):
    assert r["kept"] is True
    assert r["notice"] == [True, "“a.md” was changed in another app.", ["Revert…"]]
    assert r["revert_asked"] == "Revert to the version on disk?"
    assert r["revert_cancelled"] == [True, True], "Cancel keeps the changes and the notice"


def test_saving_over_the_newer_file_asks_first(r):
    assert r["save_asked"] == "“a.md” was changed in another app since you opened it."
    assert r["save_cancelled"] is False
    assert r["disk_after_cancel"] == "Third version, from another app.\n"
    assert r["save_anyway"] is True and r["disk_after_save"] is True
    assert r["notice_after_save"] is False
    # Asked for the cancelled save and for Save Anyway; not again after it.
    assert r["asked_again"] == [True, 2]


def test_revert_confirmed_takes_the_version_on_disk(r):
    assert r["reverted"] == ["Fourth version.", False, False]


def test_file_revert_to_saved_is_there_while_there_is_something_to_revert(r):
    # The keyboard's way to Revert…, as a Mac's File > Revert To.
    assert r["revert_command_with_changes"] is True
    assert r["revert_command_when_clean"] is False


def test_a_file_moved_or_deleted_offers_save_as_and_is_followed_back(r):
    assert r["deleted"] == ["“a.md” was moved or deleted.", ["Save As…"], True, True]
    # Put back with nothing of its own (a save that deletes and writes anew,
    # as many apps save): followed.
    assert r["put_back_clean"] == [False, "Put back by another app.", False]
    # With changes of its own: nothing lost, saving asks.
    assert r["put_back"] == [True, "“a.md” was changed in another app.", True, True]


def test_a_file_that_cant_be_read_by_itself_asks_nothing_and_guards_saving(r):
    # Another password, or text that isn't UTF-8: no alert out of nowhere;
    # the notice says it changed, and saving asks before writing over it.
    assert r["unreadable"] == [[], True, "“a.md” was changed in another app.", True]


def test_the_caret_never_lands_inside_a_character_of_two_units(r):
    assert r["caret_in_emoji"] == 1


def test_a_name_differing_only_in_case_is_the_same_file(r):
    assert r["case_asked"] == ["“A.md” was changed in another app since you opened it."]


def test_reverting_at_the_save_question_lets_the_tab_close(r):
    assert r["closed_after_revert"] is True


def test_opening_a_file_already_open_says_nothing(r):
    assert r["already_open"] == [True, False]


def test_only_drops_that_bring_something_are_accepted(r):
    assert r["drops"] == [False, True, True, True]
    assert r["drop_said"] == ["MyEditor can’t open “logo.svg”"]


def test_a_newer_version_is_a_button_not_a_bar(r):
    assert r["update"] == [True, "Update Available"]
    assert r["skipped"] is False
