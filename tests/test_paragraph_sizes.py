# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Format > Bigger and Smaller: the size of selected text, in the styles
Markdown can keep.

Asked for: "the option to change the font size" over selected text. A
document here and a Nostr article are Markdown, which keeps no font size
for a part of the text, so a size set on words would be lost on saving and
never published. The size a reader sees is the paragraph's style, as
Notes' Title, Heading and Subheading, and Medium's and Ghost's large and
small T: Bigger steps Body to Heading 3, 2 and 1; Smaller steps back. A
heading the menus do not offer (4 to 6, from a file) goes to Heading 3 or
to Body. They step from the selection's first paragraph and set every
paragraph selected, and are off where no larger (or smaller) style is,
and wherever the styles are (a code block, a table, a plain-text file).
"""

import pytest

import rich_text
from tests.app_process import run_window_script


@pytest.mark.parametrize("level, bigger, smaller", [
    (0, 3, None), (3, 2, 0), (2, 1, 3), (1, None, 2),
    (4, 3, 0), (5, 3, 0), (6, 3, 0),
])
def test_bigger_and_smaller_step_through_the_styles_by_size(level, bigger, smaller):
    assert rich_text.step_style(level, larger=True) == bigger
    assert rich_text.step_style(level, larger=False) == smaller


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setPlainText("first words\nsecond words\nthird")
app.processEvents()


def select(start_block, end_block):
    doc = ed.document()
    cursor = QTextCursor(doc.findBlockByNumber(start_block))
    end = doc.findBlockByNumber(end_block)
    cursor.setPosition(end.position() + end.length() - 1, QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    app.processEvents()


def levels():
    doc = ed.document()
    return [doc.findBlockByNumber(n).blockFormat().headingLevel() for n in range(3)]


def state():
    return [w.act_bigger.isEnabled(), w.act_smaller.isEnabled()]


r = {"titles": [w.act_bigger.text(), w.act_smaller.text()]}
from PySide6.QtWidgets import QMenu
format_menu = next(o for o in w.m_style.menuAction().associatedObjects() if isinstance(o, QMenu))
items = [a.text() for a in format_menu.actions()]
r["after_style"] = items[items.index(w.m_style.menuAction().text()) + 1:][:2]
select(0, 1)
r["body"] = state()
w.act_bigger.trigger()
r["one"] = levels()
w.act_bigger.trigger(); w.act_bigger.trigger()
r["top"] = levels() + state()
w.act_smaller.trigger()
r["down"] = levels()
for _ in range(3):
    w.act_smaller.trigger()
r["bottom"] = levels() + state()
r["sizes"] = [w.act_styles[n].font().pointSizeF() for n in range(4)]
r["bold"] = [w.act_styles[n].font().bold() for n in range(4)]
ed.document().setModified(False)
code = os.path.join(os.environ["HOME"], "plain.txt")
open(code, "w").write("words\n")
w.open_path(code)
app.processEvents()
r["plain"] = state()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_bigger_and_smaller_set_the_selected_paragraphs_style(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["titles"] == ["Bigger", "Smaller"]
    assert r["after_style"] == ["Bigger", "Smaller"]        # Format > Style, then these
    assert r["body"] == [True, False]
    assert r["one"] == [3, 3, 0]                          # every paragraph selected
    assert r["top"] == [1, 1, 0, False, True]
    assert r["down"] == [2, 2, 0]
    assert r["bottom"] == [0, 0, 0, True, False]
    # Format > Style shows each style at its size, as Notes' does.
    body, h1, h2, h3 = r["sizes"]
    assert h1 > h2 > h3 > body and r["bold"] == [False, True, True, True]
    assert r["plain"] == [False, False]
