# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Write, Preview and Split (preview_pane.py and the window's views).

What must hold:

  The preview shows the document as it reads (no Nostr account in use),
  or what will be published as an article or a note, with what readers
  will not see; it draws only while shown, shortly after the text
  changed, and every control in it has a name.

  Write, Preview and Split are the View menu's commands and the switch at
  the end of the toolbar (the same actions), for documents that hold
  Markdown; plain-text and code files show only their text. The choice is
  remembered. Switching keeps the text's undo history and selection, and
  in Preview nothing edits the hidden text.

  Split needs room for two readable columns: narrower, the side used
  last shows, Split is dimmed and says why, and it splits again when
  there is room (decision Q-N).
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.preview import Article  # noqa: E402
from preview_pane import (  # noqa: E402
    ARTICLE, DOCUMENT, NOTE, PreviewContent, PreviewPane, ViewSwitch,
)
from tests.accessibility import unnamed_controls  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def pane_for(content):
    asked = []

    def give(kind):
        asked.append(kind)
        return content(kind)

    pane = PreviewPane(give)
    pane.resize(700, 500)
    return pane, asked


def test_the_document_as_it_reads_without_a_header():
    pane, asked = pane_for(lambda kind: PreviewContent(DOCUMENT, "# Title\n\nSome **words**.\n"))
    pane.show()
    QApplication.processEvents()
    text = pane.view.toPlainText()
    assert "Title" in text and "Some words." in text
    assert pane._header.isHidden() and pane._checks.isHidden()
    assert unnamed_controls(pane) == []


def test_an_article_shows_its_header_and_what_readers_will_not_see():
    content = PreviewContent(ARTICLE, "Body words.\n",
                             Article(title="My Title", author="Alice"),
                             checks=("A picture is not uploaded yet: readers will not see it.",))
    pane, _asked = pane_for(lambda kind: content)
    pane.show()
    QApplication.processEvents()
    text = pane.view.toPlainText()
    assert text.index("My Title") < text.index("Body words.")
    assert "Alice" in text
    assert not pane._header.isHidden()
    assert not pane._checks.isHidden() and "not uploaded" in pane._checks.text()
    assert unnamed_controls(pane) == []


def test_preview_as_note_draws_plain_text():
    pane, asked = pane_for(lambda kind: PreviewContent(
        kind, "**not bold** here" if kind == NOTE else "x", Article(title="T")))
    pane.show()
    pane.kind_box.setCurrentIndex(1)
    QApplication.processEvents()
    assert asked[-1] == NOTE
    assert "**not bold** here" in pane.view.toPlainText()


def test_a_hidden_preview_does_not_draw():
    pane, asked = pane_for(lambda kind: PreviewContent(DOCUMENT, "text"))
    pane.schedule()
    QApplication.processEvents()
    assert asked == [] and not pane._timer.isActive()


def test_the_switch_is_the_view_commands_and_has_names():
    actions = [QAction(title) for title in ("Write", "Preview", "Split")]
    for action in actions:
        action.setCheckable(True)
    switch = ViewSwitch(actions)
    assert [b.defaultAction() for b in switch.buttons] == actions
    assert [b.objectName() for b in switch.buttons] == ["first", "middle", "last"]
    assert unnamed_controls(switch) == []


# -- in the window ---------------------------------------------------------------------------

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, settings
from tests.accessibility import unnamed_controls

w = main_window.MainWindow()
w.resize(1500, 800)
w.show()
ed = w.new_tab()
ed.setFocus()
ed.insertPlainText("Hello world, a first line")
ed.document().undo()
ed.insertPlainText("Hello world")
cursor = ed.textCursor()
cursor.setPosition(0)
cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
ed.setTextCursor(cursor)
app.processEvents()


def state():
    area = ed._view_splitter.widget(0)
    pane = ed._preview_pane
    return {"text": area.isVisible(), "preview": bool(pane and pane.isVisible()),
            "checked": [a.isChecked() for a in (w.act_view_write, w.act_view_preview,
                                                 w.act_view_split)],
            "split_enabled": w.act_view_split.isEnabled()}


r = {"start": state(), "switch_shown": w.view_switch.isVisible()}
w.act_view_preview.trigger()
app.processEvents()
r["preview"] = state()
r["preview_words"] = "Hello world" in ed._preview_pane.view.toPlainText()
r["bold_in_preview"] = w.act_bold.isEnabled()
r["select_all_in_preview"] = w.act_select_all.isEnabled()
r["names"] = unnamed_controls(ed._preview_pane) + unnamed_controls(w.view_switch)
w.act_view_write.trigger()
app.processEvents()
r["write"] = state()
r["selection_kept"] = ed.textCursor().selectedText()
r["undo_kept"] = ed.document().isUndoAvailable()
w.act_view_split.trigger()
app.processEvents()
r["split"] = state()
ed.moveCursor(QTextCursor.MoveOperation.End)
ed.insertPlainText(" and more")
QTest.qWait(500)
r["redrawn"] = "and more" in ed._preview_pane.view.toPlainText()
ed._preview_pane.view.setFocus()
app.processEvents()
w.resize(900, 800)
app.processEvents()
r["narrow"] = state()
r["narrow_tip"] = w.act_view_split.toolTip()
w.act_view_write.trigger()                  # flips the side; Split stays the choice
app.processEvents()
r["narrow_flipped"] = state()
r["mode_kept"] = settings.load_settings().get("view_mode")
w.resize(1500, 800)
app.processEvents()
r["wide_again"] = state()
txt = os.path.join(os.environ["HOME"], "plain.txt")
with open(txt, "w", encoding="utf-8") as f:
    f.write("plain\n")
plain = w.open_path(txt)
app.processEvents()
r["plain"] = {"switch_shown": w.view_switch.isVisible(),
              "text": plain._view_splitter.widget(0).isVisible()}
for e in (ed, plain):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_write_preview_and_split_in_the_window(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["start"] == {"text": True, "preview": False, "checked": [True, False, False],
                          "split_enabled": True}
    assert r["switch_shown"]
    assert r["preview"]["text"] is False and r["preview"]["preview"] is True
    assert r["preview"]["checked"] == [False, True, False]
    assert r["preview_words"]
    assert r["bold_in_preview"] is False and r["select_all_in_preview"] is True
    assert r["names"] == []
    assert r["write"]["text"] and not r["write"]["preview"]
    assert r["selection_kept"] == "Hello" and r["undo_kept"]
    assert r["split"]["text"] and r["split"]["preview"]
    assert r["redrawn"]
    # Narrow: the side used last (the preview had the focus), Split dimmed.
    assert r["narrow"] == {"text": False, "preview": True, "checked": [False, True, False],
                           "split_enabled": False}
    assert r["narrow_tip"] == "Make the window wider to show both"
    assert r["narrow_flipped"]["text"] and not r["narrow_flipped"]["preview"]
    assert r["mode_kept"] == "split"
    assert r["wide_again"]["text"] and r["wide_again"]["preview"]
    assert r["wide_again"]["checked"] == [False, False, True]
    assert r["plain"] == {"switch_shown": False, "text": True}
