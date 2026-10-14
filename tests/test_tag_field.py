# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the hashtags field (nostr/ui/tag_field.py), the one Article Details
and Publish as Article share.

What must hold, as Mail's recipients and Finder's tags behave:

  Typed words become a tag at Return or a comma; Return with nothing typed
  is the window's (it publishes). A leading "#" is dropped and a tag the
  field has, in any case, is not added twice. Pasted "a, b c" is read as
  a file's front matter string is (article_details.hashtags_written):
  three tags. Words typed and not yet made a tag count, and leaving the
  field makes them one.

  × removes its tag, and Backspace with nothing typed removes the last
  one, said to a screen reader. Each tag is named by itself, each × as
  "Remove <tag>"; Tab reaches the × buttons left to right, then the field.
  A field kept read-only shows its tags without a way to change them.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QDialogButtonBox, QToolButton, QVBoxLayout,
)

from nostr.ui.tag_field import TagField  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def field_in_dialog():
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    field = TagField()
    field.setAccessibleName("Hashtags")
    layout.addWidget(field)
    box = QDialogButtonBox()
    publish = box.addButton("Publish", QDialogButtonBox.ButtonRole.AcceptRole)
    publish.clicked.connect(dialog.accept)
    publish.setDefault(True)
    layout.addWidget(box)
    dialog.show()
    field.entry.setFocus()
    QApplication.processEvents()
    return dialog, field


def removers(field):
    return [button for button in field.findChildren(QToolButton) if button.isVisible()]


def test_return_and_a_comma_make_a_tag_without_the_hash_each_once():
    dialog, field = field_in_dialog()
    changed = []
    field.changed.connect(lambda: changed.append(field.tags()))
    QTest.keyClicks(field.entry, "#Bitcoin")
    QTest.keyClick(field.entry, Qt.Key.Key_Return)
    QTest.keyClicks(field.entry, "nostr,")
    QTest.keyClicks(field.entry, "bitcoin")
    QTest.keyClick(field.entry, Qt.Key.Key_Return)
    assert field.tags() == ("Bitcoin", "nostr")
    assert field.entry.text() == "" and changed[-1] == ("Bitcoin", "nostr")
    assert dialog.result() == 0                    # Return made tags, it did not publish
    QTest.keyClick(field.entry, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted   # nothing typed: the window's
    dialog.close()


def test_pasted_words_are_read_as_front_matter_reads_them():
    dialog, field = field_in_dialog()
    QApplication.clipboard().setText("a, b c")
    field.entry.paste()
    QTest.keyClick(field.entry, Qt.Key.Key_Return)
    assert field.tags() == ("a", "b", "c")
    QApplication.clipboard().setText("#ruby #jekyll")
    field.entry.paste()
    assert field.tags() == ("a", "b", "c", "ruby", "jekyll")      # typed words count
    field.entry.clearFocus()
    QApplication.processEvents()
    assert field.entry.text() == "" and field.tags() == ("a", "b", "c", "ruby", "jekyll")
    dialog.close()


def test_x_and_backspace_remove_a_tag_and_a_screen_reader_hears_it(monkeypatch):
    import announce
    heard = []
    monkeypatch.setattr(announce, "announce", lambda source, text: heard.append(text))
    dialog, field = field_in_dialog()
    field.set_tags(("bitcoin", "nostr", "essay"))
    assert [b.accessibleName() for b in removers(field)] == [
        "Remove bitcoin", "Remove nostr", "Remove essay"]
    removers(field)[1].click()
    assert field.tags() == ("bitcoin", "essay")
    field.entry.setFocus()
    QTest.keyClick(field.entry, Qt.Key.Key_Backspace)
    assert field.tags() == ("bitcoin",) and heard[-1] == "Removed essay"
    QTest.keyClicks(field.entry, "ab")
    QTest.keyClick(field.entry, Qt.Key.Key_Backspace)          # a letter, not a tag
    assert field.tags() == ("bitcoin", "a")
    dialog.close()


def test_every_tag_and_control_is_named_and_tab_goes_left_to_right():
    dialog, field = field_in_dialog()
    field.set_tags(("bitcoin", "nostr"))
    field.set_tags(("essay", "bitcoin", "nostr"))             # made again, in order
    assert unnamed_controls(dialog) == []
    names = [chip.accessibleName() for chip in field.chips()]
    assert names == ["essay", "bitcoin", "nostr"]
    chain, widget = [], dialog
    for _step in range(6):
        widget = widget.nextInFocusChain()
        while not (widget.focusPolicy() & Qt.FocusPolicy.TabFocus and widget.isVisible()):
            widget = widget.nextInFocusChain()
        chain.append(widget.accessibleName())
    assert chain[:4] == ["Remove essay", "Remove bitcoin", "Remove nostr", "Hashtags"]
    dialog.close()


def test_a_read_only_field_shows_its_tags_and_offers_no_change():
    dialog, field = field_in_dialog()
    field.set_tags(("bitcoin",))
    field.setReadOnly(True)
    assert removers(field) == [] and field.entry.isHidden()
    assert field.tags() == ("bitcoin",)
    field.setReadOnly(False)
    assert len(removers(field)) == 1 and not field.entry.isHidden()
    dialog.close()
