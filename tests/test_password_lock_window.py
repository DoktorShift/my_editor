# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins File > Set Password… in the window (locked_files.py, password_alert.py).

What must hold, as Pages locks a document:

  Setting a password locks the file at once, under its own name, with
  nothing of its text left in it. Opening it asks for the password, and a
  wrong one is asked again; cancelling opens nothing. Saving keeps it
  locked, without the password typed again. Change Password needs the old
  one and locks with the new one; Remove Password writes it in the clear.
  A locked document is not written as a PDF or RTF, which cannot be
  locked. After a crash, its sealed backup is restored with its password,
  and left on the disk when none is given.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, password_alert, recovery
from locked_files import Lock, is_locked_file
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

home = os.environ["HOME"]
diary = os.path.join(home, "diary.md")
asked = []
told = []


class Answer:
    # An alert answered as the person would: each password in turn is
    # tried (the alert's check), until one opens or none is left.
    def __init__(self, choice, check=None, **texts):
        self.choice, self.check, self.texts, self.result = choice, check, texts, None

    def run(self):
        if self.check is None:
            return self.choice
        # Opening tries passwords; changing checks the old one.
        for password in self.texts.get("tries", [self.texts.get("old")]):
            self.result = self.check(password)
            if self.result:
                return self.choice
        return password_alert.CANCELLED

    def text(self, name):
        return self.texts[name]


answers = []


def answering(kind):
    def make(parent, *, name, check=None, **_):
        asked.append((kind, name))
        choice, texts = answers.pop(0)
        return Answer(choice, check, **texts)
    return make


for kind in ("open_alert", "set_password_alert", "change_alert"):
    setattr(password_alert, kind, answering(kind))
main_window.inform = lambda parent, **said: told.append(said["title"])

r = {}
w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setPlainText("Dear diary, a secret.")
main_window.QFileDialog = SimpleNamespace(getSaveFileName=lambda *a, **k: (diary, ".md (*.md)"))
assert w.save_as()
r["title_before"] = w.act_password.text()
answers.append((password_alert.SET, {"password": "horse"}))
w.act_password.trigger()
r["title_after"] = w.act_password.text()
data = open(diary, "rb").read()
r["locked"] = is_locked_file(diary) and b"secret" not in data
r["name"] = os.listdir(home).count("diary.md") == 1            # its own name, no copy

# Saving again keeps it locked, without asking.
ed.textCursor().insertText("More. ")
asked.clear()
assert w.save()
r["saved_without_asking"] = asked == []
r["still_locked"] = Lock.open(open(diary, "rb").read(), "horse")[1].decode().startswith("More.")

# A locked document is not written as a PDF.
main_window.QFileDialog = SimpleNamespace(
    getSaveFileName=lambda *a, **k: (os.path.join(home, "diary.pdf"), ".pdf (*.pdf)"))
told.clear()
r["pdf_saved"] = w.save_as()
r["pdf_told"] = told[:]
r["pdf_exists"] = os.path.exists(os.path.join(home, "diary.pdf"))
told.clear()
w._on_knit("html")
r["knit_told"] = told[:]

ed.document().setModified(False)
w.act_close_tab.trigger()
app.processEvents()

# Opening asks for the password; a wrong one is tried, then the right one.
answers.append((password_alert.OPEN, {"tries": ["pony", "horse"]}))
opened = w.open_path(diary)
r["opened_text"] = opened.toPlainText()
r["opened_locked"] = getattr(opened, "_lock", None) is not None
opened.document().setModified(False)
w.act_close_tab.trigger()
app.processEvents()
answers.append((password_alert.OPEN, {"tries": ["pony"]}))     # then Cancel
tabs_before = w.tabs.count()
r["cancelled"] = w.open_path(diary) is None and w.tabs.count() == tabs_before

# Change, then remove.
answers.append((password_alert.OPEN, {"tries": ["horse"]}))
ed = w.open_path(diary)
answers.append((password_alert.CHANGE, {"old": "horse", "new": "battery", "verify": "battery"}))
w.act_password.trigger()
r["changed"] = Lock.open(open(diary, "rb").read(), "battery")[1].decode().startswith("More.")
answers.append((password_alert.REMOVE, {"old": "battery"}))
w.act_password.trigger()
r["removed"] = open(diary, "rb").read().decode().startswith("More.")
r["title_removed"] = w.act_password.text()

# A crash: the backup of a locked document, restored with its password.
answers.append((password_alert.SET, {"password": "horse"}))
w.act_password.trigger()
ed.textCursor().insertText("Unsaved. ")
assert ed._backup.write_now()
backup_file = ed._backup.path
record = json.load(open(backup_file))
r["backup_sealed"] = record["format"] == recovery.SEALED
answers.append((password_alert.OPEN, {"tries": []}))            # Cancel
r["skipped"] = w._restore_one_backup(dict(record, _backup_file=backup_file)) is None
r["kept_on_disk"] = os.path.exists(backup_file)
answers.append((password_alert.OPEN, {"tries": ["horse"]}))
restored = w._restore_one_backup(dict(record, _backup_file=backup_file))
r["restored"] = restored is not None and "Unsaved." in restored.toPlainText()
r["restored_locked"] = restored is not None and getattr(restored, "_lock", None) is not None
for e in w._all_editors():
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_document_locked_with_a_password(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["title_before"] == "Set Password…" and r["title_after"] == "Change Password…"
    assert r["locked"] and r["name"]
    assert r["saved_without_asking"] and r["still_locked"]
    assert r["pdf_saved"] is False and r["pdf_exists"] is False
    assert r["pdf_told"] == ["A locked document can’t be saved as PDF or RTF"]
    assert r["knit_told"] == ["A locked document can’t be knit"]
    assert r["opened_text"].startswith("More. Dear diary") and r["opened_locked"]
    assert r["cancelled"]
    assert r["changed"] and r["removed"] and r["title_removed"] == "Set Password…"
    assert r["backup_sealed"] and r["skipped"] and r["kept_on_disk"]
    assert r["restored"] and r["restored_locked"]
