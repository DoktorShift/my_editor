# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a tab's article is one model every surface follows at once.

What must hold (Apple's guidelines for panels: an inspector shows the
details of the current item, "automatically updating its contents when
the item changes"):

  A detail set in Article Details (or the publish window, or a cover that
  finished uploading) shows in the preview without touching the text:
  its title, summary and hashtags. A tab with no file of its own is named
  by its article's title as it is typed, in the tab, the window title and
  for a screen reader; a file keeps its file name.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from main_window import SPLIT
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

def settle(seconds=0.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

w = main_window.MainWindow()
w.resize(1900, 800)        # Split needs room beside the inspector
w.show()
w.nostr_state._active = True
w._on_nostr_in_use_changed(True)
ed = w.new_tab()
ed.setPlainText("Why I write in plain text\n\nEvery tool I loved is gone.")
w._choose_view(SPLIT)
w.act_article_details.setChecked(True)
settle()
panel = w._article_details_panel
pane = ed._preview_pane
r = {"before": pane.view.toPlainText()}
panel.title_edit.setText("Plain text, forever")
panel.summary_edit.setPlainText("Why the files outlive the apps.")
panel.hashtags.entry.setText("writing")
panel.hashtags.commit()
settle()
preview = pane.view.toPlainText()
r["preview_title"] = "Plain text, forever" in preview
r["preview_summary"] = "Why the files outlive the apps." in preview
r["preview_tag"] = "writing" in preview
r["visible"] = pane.isVisible()
r["tab"] = w.tabs.tabText(w.tabs.currentIndex())
r["window"] = w.windowTitle()
r["spoken"] = ed.accessibleName()
ed._file_path = "/tmp/notes.md"
w._update_tab_title()
r["file_tab"] = w.tabs.tabText(w.tabs.currentIndex())
ed._file_path = None
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_preview_and_the_tab_follow_the_details_as_they_are_typed(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["visible"] and "Plain text, forever" not in r["before"]
    assert r["preview_title"] and r["preview_summary"] and r["preview_tag"]
    assert r["tab"] == "Plain text, forever*"
    assert r["window"].startswith("Plain text, forever")
    assert r["spoken"] == "Plain text, forever"
    assert r["file_tab"] == "notes.md*"


PLACEHOLDERS = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

def settle(seconds=0.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

w = main_window.MainWindow()
w.show()
w.nostr_state._active = True
w._on_nostr_in_use_changed(True)
ed = w.new_tab()
ed.setPlainText("Why I write in plain text\n\nEvery tool I loved is gone.")
w.act_article_details.setChecked(True)
settle()
panel = w._article_details_panel
r = {"title": panel.title_edit.placeholderText(),
     "link": panel.identifier_edit.placeholderText()}
cursor = ed.textCursor()
cursor.setPosition(0)
cursor.insertText("Still ")
settle()
r["title_after_edit"] = panel.title_edit.placeholderText()
r["link_after_edit"] = panel.identifier_edit.placeholderText()
r["typed_untouched"] = panel.title_edit.text()
panel.title_edit.setText("Plain text, forever")
settle()
r["link_follows_typed"] = panel.identifier_edit.placeholderText()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_an_empty_title_shows_the_title_it_will_publish_with_as_the_text_changes(tmp_path):
    r = run_window_script(PLACEHOLDERS, tmp_path)
    assert r["title"] == "Why I write in plain text"
    assert r["link"] == "why-i-write-in-plain-text"
    assert r["title_after_edit"] == "Still Why I write in plain text"
    assert r["link_after_edit"] == "still-why-i-write-in-plain-text"
    assert r["typed_untouched"] == ""
    assert r["link_follows_typed"] == "plain-text-forever"


HEADER = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from main_window import SPLIT, DraftBinding
from nostr.drafts import INNER_KIND_SHORT_NOTE
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

def settle(seconds=0.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

w = main_window.MainWindow()
w.resize(1900, 800)
w.show()
w.nostr_state._active = True
w._on_nostr_in_use_changed(True)
ed = w.new_tab()
ed.setPlainText("Why I write in plain text\n\nEvery tool I loved is gone.")
settle()
headers = w._article_headers
r = {"before_panel": headers.header_of(ed) is not None}
w._choose_view(SPLIT)
w.act_article_details.setChecked(True)
settle()
head = headers.header_of(ed)
panel = w._article_details_panel
r["after_panel"] = head is not None
r["placeholder"] = head.title_edit.placeholderText()
r["inset"] = ed.top_inset() > 0
head.title_edit.setText("Plain text, forever")
head.summary_edit.setPlainText("Why the files outlive the apps.")
settle()
r["panel_title"] = panel.title_edit.text()
r["panel_summary"] = panel.summary_edit.toPlainText()
r["tab"] = w.tabs.tabText(w.tabs.currentIndex())
r["preview"] = "Plain text, forever" in ed._preview_pane.view.toPlainText()
panel.title_edit.setText("Typed in the inspector")
settle()
r["head_title"] = head.title_edit.text()
r["text_untouched"] = ed.toPlainText().startswith("Why I write in plain text")
r["undo_untouched"] = not ed.document().isUndoAvailable() or ed.document().availableUndoSteps() == 1
w.act_article_details.setChecked(False)
settle()
r["stays_an_article"] = headers.header_of(ed) is not None
note = w.new_tab()
note._draft_binding = DraftBinding(identifier="n", inner_kind=INNER_KIND_SHORT_NOTE)
w.act_article_details.setChecked(True)
w._on_tab_changed(w.tabs.currentIndex())
settle()
r["note"] = headers.header_of(note) is not None
w.nostr_state._active = False
w._on_nostr_in_use_changed(False)
settle()
r["without_nostr"] = headers.header_of(ed) is not None or ed.top_inset() != 0
for tab in (ed, note):
    tab.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_article_head_on_the_page_is_the_same_article_as_the_inspector(tmp_path):
    r = run_window_script(HEADER, tmp_path)
    # A new tab is an article once Article Details is opened on it, and stays one.
    assert r["before_panel"] is False and r["after_panel"] is True and r["inset"]
    assert r["placeholder"] == "Why I write in plain text"
    assert r["panel_title"] == "Plain text, forever"
    assert r["panel_summary"] == "Why the files outlive the apps."
    assert r["tab"] == "Plain text, forever*" and r["preview"]
    assert r["head_title"] == "Typed in the inspector"
    assert r["text_untouched"] and r["undo_untouched"]
    assert r["stays_an_article"] is True
    assert r["note"] is False                     # a note has no head
    assert r["without_nostr"] is False            # nothing of Nostr without an account
