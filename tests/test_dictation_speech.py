# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Edit > Start Dictation… and Edit > Speech (dictation.py, speech.py).

What must hold, in the order of Apple's Edit menus (Substitutions, Speech,
then Start Dictation…):

  Start Dictation… starts the system's own dictation into the text, on a
  Mac and on Windows; Linux has none, and the command is not there. The
  toolbar has it as a microphone, there too only where it can act. Start
  Speaking reads the selection, or the whole document when nothing is
  selected; Stop Speaking acts only while it reads. Both act on a
  document being written, not on a PDF.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dictation  # noqa: E402
import speech  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


def test_the_systems_dictation_is_there_on_a_mac_and_windows_only():
    assert dictation.system_available("darwin") and dictation.system_available("win32")
    assert not dictation.system_available("linux")
    assert dictation.start_system("linux") is False


def test_what_is_read_leaves_pictures_out_and_reads_paragraphs_as_lines():
    assert speech.spoken_text(" One.\u2029\ufffcTwo.\u2028Three. ") == "One.\nTwo.\nThree."


def test_speech_says_when_it_starts_and_stops():
    voice = speech.Speech(engine=speech.TEST_ENGINE)
    seen = []
    voice.speaking_changed.connect(seen.append)
    voice.speak("\ufffc")                       # nothing to read: nothing starts
    assert seen == [] and not voice.is_speaking()
    voice.speak("A sentence to read.")
    for _ in range(100):
        if seen[-1:] == [False]:
            break
        QTest.qWait(10)
    assert seen == [True, False]


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMenu
app = QApplication(sys.argv[:1])
import dictation, main_window, speech

DICTATION = os.environ["TEST_DICTATION"] == "1"
started = []
dictation.ways = lambda *args, **kwargs: (dictation.SYSTEM,) if DICTATION else ()
dictation.start_system = lambda: started.append(QApplication.focusWidget() is ed) or True
said = []


class Voice(speech.Speech):
    def __init__(self, parent=None):
        super().__init__(speech.TEST_ENGINE, parent)

    def speak(self, text):
        said.append(text)
        super().speak(text)


main_window.Speech = Voice
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setPlainText("First paragraph.\nSecond one.")
ed.setFocus()
app.processEvents()

edit = next(o for o in w.act_undo.associatedObjects() if isinstance(o, QMenu))
r = {"edit": [a.text() or "|" for a in edit.actions()]}
r["speech"] = [a.text() for a in w.m_speech.actions()]
r["mic"] = w.format_toolbar.dictation_button is not None and \
    w.format_toolbar.dictation_button.accessibleName()
r["stop_before"] = w.act_stop_speaking.isEnabled()
w.act_start_speaking.trigger()
r["stop_while"] = w.act_stop_speaking.isEnabled()
w.act_stop_speaking.trigger()
cursor = ed.textCursor()
cursor.setPosition(0)
cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
ed.setTextCursor(cursor)
w.act_start_speaking.trigger()
r["said"] = said
if DICTATION:
    w.act_start_dictation.trigger()
    app.processEvents()
r["started"] = started
from PySide6.QtGui import QTextDocument
from export_pdf import export_pdf
doc = QTextDocument()
doc.setPlainText("A page.")
import tempfile
pdf = os.path.join(tempfile.mkdtemp(), "page.pdf")
export_pdf(doc, pdf, title="Page", page_setup={"page_size": "A4", "orientation": "portrait",
                                              "margins_mm": 20.0})
w.open_path(pdf)
app.processEvents()
r["in_pdf"] = [w.act_start_speaking.isEnabled(),
               DICTATION and w.act_start_dictation.isEnabled()]
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def run(tmp_path, dictation_here):
    os.environ["TEST_DICTATION"] = "1" if dictation_here else "0"
    try:
        return run_window_script(SCRIPT, tmp_path)
    finally:
        del os.environ["TEST_DICTATION"]


def test_edit_menu_offers_speech_then_dictation_as_apples_do(tmp_path):
    r = run(tmp_path, True)
    edit = r["edit"]
    at = edit.index("Speech")
    assert edit[at - 1] == "Substitutions"
    assert edit[at + 1:at + 3] == ["|", "Start Dictation…"]
    assert r["speech"] == ["Start Speaking", "Stop Speaking"]
    assert r["mic"] == "Start Dictation"


def test_start_speaking_reads_the_selection_or_the_whole_document(tmp_path):
    r = run(tmp_path, True)
    assert r["said"] == ["First paragraph.\nSecond one.", "First"]
    assert r["stop_before"] is False and r["stop_while"] is True


def test_start_dictation_starts_the_systems_with_the_text_focused(tmp_path):
    r = run(tmp_path, True)
    assert r["started"] == [True]
    assert r["in_pdf"] == [False, False]


def test_where_the_system_has_no_dictation_it_is_not_offered(tmp_path):
    r = run(tmp_path, False)
    assert "Start Dictation…" not in r["edit"] and r["mic"] is False
    assert "Speech" in r["edit"]
