# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins dictating on this computer in the window (dictation/, main_window).

What must hold:

  Where the way to dictate is on this computer (Linux, or Windows by
  choice), Start Dictation… offers the speech model when it is not there,
  and does nothing more when the person says Not Now. With the model, the
  indicator shows at the insertion point, and each phrase is written there
  as it is spoken, a space apart from the word before, one step of Undo
  each, in the language chosen. The menu says Stop Dictation meanwhile;
  Done, the menu, another tab, or quitting stops it, the last phrase
  still written. A failure says why.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import dictation
from dictation import models, on_device, setup
dictation.ways = lambda *args, **kwargs: (dictation.ON_DEVICE,)
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

r = {"offered": [], "loaded": [], "told": []}
answers = [setup.NOT_NOW, setup.INSTALLED]
installed = [False]
models.installed = lambda model: installed[0]


class Offer:
    def __init__(self, parent, model):
        r["offered"].append(model.key)

    def run(self):
        answer = answers.pop(0)
        installed[0] = answer == setup.INSTALLED
        return answer


setup.SetupAlert = Offer
on_device.prepare = lambda: None
on_device.load_whisper = lambda model, language: r["loaded"].append((model.key, language))
sessions = []


class Session(QObject):
    ready = Signal(); phrase = Signal(str); level = Signal(float)
    listening_changed = Signal(bool); failed = Signal(str)

    def __init__(self, make_engine, parent=None):
        super().__init__(parent)
        self.make_engine = make_engine
        sessions.append(self)

    def start(self):
        self.make_engine()
        self.listening_changed.emit(True)
        self.ready.emit()

    def stop(self):
        self.phrase.emit("Letzter Satz.")          # the phrase being spoken
        self.listening_changed.emit(False)

    def shutdown(self):
        r["shut_down"] = True
        self.listening_changed.emit(False)


on_device.OnDeviceDictation = Session
main_window.inform = lambda parent, **said: r["told"].append(said["title"])
w = main_window.MainWindow()
w.show()
w._set_dictation_prefs(w.dictation_prefs.with_(language="de", model=models.ACCURATE))
ed = w.new_tab()
ed.setPlainText("Ich schreibe")
cursor = ed.textCursor(); cursor.movePosition(cursor.MoveOperation.End); ed.setTextCursor(cursor)
app.processEvents()

w.act_start_dictation.trigger()                      # Not Now
r["after_not_now"] = [w._dictating is None, w.act_start_dictation.text()]
w.act_start_dictation.trigger()                      # Download, then dictate
session, indicator = w._dictating
r["dictating"] = [w.act_start_dictation.text(), indicator.isVisible(), indicator.state_text()]
session.phrase.emit("heute.")
session.phrase.emit("Morgen mehr.")
r["text"] = ed.toPlainText()
ed.undo()
r["undone"] = ed.toPlainText()
indicator.done_button.click()                        # Done
r["stopped"] = [ed.toPlainText(), w._dictating is None, w.act_start_dictation.text(),
                indicator.isVisible()]

w.act_start_dictation.trigger()                      # installed now: no offer
other = w.new_tab()
app.processEvents()
r["tab_stops"] = w._dictating is None
r["offers"] = list(r["offered"])

w.act_start_dictation.trigger()
sessions[-1].failed.emit("No microphone was found.")
r["failed_said"] = list(r["told"])
for e in w._all_editors():
    e.document().setModified(False)
w.close()
r["quit_shut_down"] = r.get("shut_down", False)
print("RESULT " + json.dumps(r))
"""


def test_dictating_on_this_computer_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["after_not_now"] == [True, "Start Dictation…"]
    assert r["dictating"] == ["Stop Dictation", True, "Listening"]
    assert r["loaded"][0] == ["accurate", "de"]             # the model and language chosen
    assert r["text"] == "Ich schreibe heute. Morgen mehr."
    assert r["undone"] == "Ich schreibe heute."             # one phrase, one step of Undo
    assert r["stopped"] == ["Ich schreibe heute. Letzter Satz.", True, "Start Dictation…",
                            False]
    assert r["tab_stops"] and r["offers"] == ["accurate", "accurate"]
    assert r["failed_said"] == ["Dictation stopped"]
    assert r["quit_shut_down"]
