# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the editor's rich-text model (rich_text.py).

What must hold:

  Every structure the editor makes is one the Markdown writer writes,
  and Markdown read back gives the same document again: the round trip
  is the test of every structure.

  What Qt's Markdown reader leaves behind is tidied: no checklist mark
  outside a list, so a paragraph after a checklist never turns into a
  checked item.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import (  # noqa: E402
    QTextBlockFormat, QTextCursor, QTextDocument, QTextFormat, QTextListFormat,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from markdown_writer import READ_FEATURES, document_to_markdown  # noqa: E402
from tests.rich_text_helpers import assert_round_trip, block_named, from_markdown  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


# -- reading Markdown ----------------------------------------------------------------

def test_no_checklist_mark_is_left_after_a_checklist():
    doc = QTextDocument()
    doc.setMarkdown("- [x] done\n- [ ] open\n\nAfter\n\n> quote\n", READ_FEATURES)
    rich_text.normalize_after_markdown_load(doc)
    marker = QTextFormat.Property.BlockMarker
    assert not block_named(doc, "After").blockFormat().hasProperty(marker)
    assert not block_named(doc, "quote").blockFormat().hasProperty(marker)
    assert block_named(doc, "done").blockFormat().marker() == QTextBlockFormat.MarkerType.Checked
    assert block_named(doc, "open").blockFormat().marker() == QTextBlockFormat.MarkerType.Unchecked


def test_a_list_started_after_a_checklist_is_a_plain_list():
    doc = from_markdown("- [x] done\n\nAfter\n")
    QTextCursor(block_named(doc, "After")).createList(QTextListFormat.Style.ListDisc)
    assert document_to_markdown(doc) == "- [x] done\n\n- After\n"


def test_a_checklist_comes_back_the_same():
    assert_round_trip(from_markdown("- [x] done\n- [ ] open\n\nAfter\n"))

