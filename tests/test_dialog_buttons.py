# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the dialogs with a button box answer the keyboard.

What must hold, in every such dialog, with the app's own style (the one
main.py sets, which puts Cancel first and the default action last on
every system, as the HIG does):

  The action (Save, Save draft, Publish, Done, OK) is the default button,
  drawn as such at the trailing end of the row, and Return chooses it whichever control has the focus,
  Cancel included, as in a Mac dialog and in MyEditor's alerts. (Review
  N1: the action was made the default before its button box was in the
  dialog, so Qt chose the first button in the row, Cancel, and Return
  cancelled.)

  Escape cancels. (Publish as Article is given a title first: without
  one, Publish is dimmed and Return does nothing.)
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QPlainTextEdit, QTextEdit,
)
app = QApplication(sys.argv[:1])
import theme
app.setStyle(theme.AppStyle("Fusion"))

from nostr.ui.image_review_dialog import ImageReviewDialog
from nostr.ui.publish_article_dialog import PublishArticleDialog
from nostr.ui.publish_note_dialog import PublishNoteDialog
from nostr.ui.save_destination_dialog import SaveDestinationDialog
from nostr.ui.stash_kind_dialog import StashKindDialog
from page_setup_dialog import PageSetupDialog
from tests.test_imports_image_review import Images
from tests.test_publish_job import _dialog

published = []
for cls in (PublishNoteDialog, PublishArticleDialog):   # what Publish starts is not under test
    cls._on_publish = lambda self: (published.append(True), self.accept())
tmp = pathlib.Path(tempfile.mkdtemp(dir=os.environ["HOME"]))

def titled(dialog):
    # Publish as Article asks for a title before Publish can be chosen.
    dialog._title_edit.setText("My title")
    return dialog


DIALOGS = {
    "save destination": SaveDestinationDialog,
    "save as Nostr draft": StashKindDialog,
    "publish as note": lambda: _dialog("note", tmp, None),
    "publish as article": lambda: titled(_dialog("article", tmp, None)),
    "review images": lambda: ImageReviewDialog(["https://example.com/a.png"],
                                               image_source=Images()),
    "page setup": lambda: PageSetupDialog(is_dark=False),
}
ACTION = (QDialogButtonBox.ButtonRole.AcceptRole,)


def press(make, key, focus_cancel):
    published.clear()
    dialog = make()
    dialog.show()
    app.processEvents()
    box = dialog.findChild(QDialogButtonBox)
    action = next(b for b in box.buttons() if box.buttonRole(b) in ACTION)
    cancel = next(b for b in box.buttons()
                  if box.buttonRole(b) == QDialogButtonBox.ButtonRole.RejectRole)
    if focus_cancel:
        cancel.setFocus(Qt.TabFocusReason)
        app.processEvents()
    target = dialog.focusWidget() or dialog
    if key == Qt.Key_Return and isinstance(target, (QTextEdit, QPlainTextEdit)):
        cancel.setFocus(Qt.TabFocusReason)     # Return in the note's text is a new line
        app.processEvents()
        target = cancel
    default = action.isDefault() and not cancel.isDefault()
    trailing = action.mapTo(dialog, action.rect().topLeft()).x() \
        > cancel.mapTo(dialog, cancel.rect().topLeft()).x()
    QTest.keyClick(target, key)
    app.processEvents()
    accepted = dialog.result() == QDialog.Accepted or bool(published)
    dialog.close()
    return {"default": default, "trailing": trailing, "accepted": accepted}


r = {}
for name, make in DIALOGS.items():
    r[name] = {"return": press(make, Qt.Key_Return, False),
               "return on cancel": press(make, Qt.Key_Return, True),
               "escape": press(make, Qt.Key_Escape, False)}
print("RESULT " + json.dumps(r))
"""


def test_return_chooses_the_action_and_escape_cancels(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    for name, keys in r.items():
        assert keys["return"] == {"default": True, "trailing": True, "accepted": True}, name
        assert keys["return on cancel"]["accepted"], name
        assert not keys["escape"]["accepted"], name
