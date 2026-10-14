# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a closed PDF tab goes: reading many PDFs in one session does
not keep growing.

What must hold:

  A PDF tab closed while it renders, while it scrolls, while Find scans
  it, with its outline open or a reload of its file pending, is deleted
  once the event loop has run: the reader, its document, its view and its
  search are gone, and nothing put off for it (a reload, the reading
  position) comes to it afterwards. Closing a long PDF while it renders
  takes no time anyone could notice.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.app_process import CHILD_EPILOGUE, CHILD_PROLOGUE, REPO, child_env  # noqa: E402

SCRIPT = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QPageSize, QPainter, QPdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6
app = QApplication(sys.argv[:1])
import main_window
main_window.inform = lambda parent, **kw: None
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
home = os.environ["HOME"]

def make_pdf(path, pages):
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    painter = QPainter(writer)
    for page in range(pages):
        if page:
            writer.newPage()
        for line in range(40):
            painter.drawText(QPointF(200, 300 + line * 220),
                             "Page %d, a line of words to find and read %d" % (page + 1, line))
    painter.end()

long_pdf = os.path.join(home, "long.pdf")
make_pdf(long_pdf, 500)
w = main_window.MainWindow()
w.resize(1000, 700)
w.show()
w.activateWindow()

def settle():
    for _ in range(3):
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

def parts(viewer):
    return [viewer, viewer.document, viewer.view, viewer._search, viewer._bookmarks]

r = {"cases": {}}
for case in ("render", "scroll", "search", "outline", "reload"):
    path = os.path.join(home, case + ".pdf")
    make_pdf(path, 60)
    viewer = w.open_path(path) or w.current_pdf_viewer()
    app.processEvents()
    if case == "scroll":
        bar = viewer.view.verticalScrollBar()
        for step in range(5):
            bar.setValue(bar.maximum() * step // 5)
    elif case == "search":
        viewer.toggle_findbar()
        viewer.findbar.edit.setText("words")
    elif case == "outline":
        viewer.toggle_outline()
    elif case == "reload":
        viewer.schedule_reload()
    held = parts(viewer)
    w.close_tab(w.tabs.indexOf(viewer))
    settle()
    r["cases"][case] = [shiboken6.isValid(part) for part in held]

# A 500-page PDF closed while it renders, at a zoom where every page is
# rendered anew.
viewer = w.open_path(long_pdf) or w.current_pdf_viewer()
viewer.zoom_in()
viewer.view.verticalScrollBar().setValue(viewer.view.verticalScrollBar().maximum() // 2)
app.processEvents()
held = parts(viewer)
started = time.perf_counter()
w.close_tab(w.tabs.indexOf(viewer))
settle()
r["close_ms"] = round((time.perf_counter() - started) * 1000)
r["long"] = [shiboken6.isValid(part) for part in held]
QTest.qWait(600)                    # a reload or a reading position put off
app.processEvents()
r["tabs"] = w.tabs.count()
w.close()
print("RESULT " + json.dumps(r), flush=True)
"""


def test_a_closed_pdf_tab_goes_with_its_document_and_search(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", CHILD_PROLOGUE + SCRIPT + CHILD_EPILOGUE,
         REPO], env=child_env(tmp_path), capture_output=True, text=True, timeout=300)
    line = next((out for out in proc.stdout.splitlines() if out.startswith("RESULT ")), "")
    assert line, proc.stdout + proc.stderr
    r = json.loads(line[len("RESULT "):])
    for case, alive in r["cases"].items():
        assert alive == [False] * 5, (case, alive)
    assert r["long"] == [False] * 5
    assert r["close_ms"] < 150, r["close_ms"]
    assert "Traceback" not in proc.stderr and "RuntimeError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


QUIET_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent, QPointF, qInstallMessageHandler
from PySide6.QtGui import QPageSize, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
path = os.path.join(os.environ["HOME"], "quiet.pdf")
writer = QPdfWriter(path)
writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
painter = QPainter(writer)
painter.drawText(QPointF(200, 300), "words to find")
painter.end()
w = main_window.MainWindow()
w.show()
viewer = w.open_path(path) or w.current_pdf_viewer()
viewer.toggle_findbar()
viewer.findbar.edit.setText("words")
app.processEvents()
said = []
qInstallMessageHandler(lambda kind, context, message: said.append(message))
w.close_tab(w.tabs.indexOf(viewer))
for _ in range(3):
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
qInstallMessageHandler(None)
w.close()
print("RESULT " + json.dumps(said))
"""


def test_closing_a_pdf_says_nothing_in_the_log(tmp_path):
    """Review 2, L3: each closed PDF wrote three "QObject::connect: invalid
    nullptr parameter" warnings, which the app's log keeps."""
    proc = subprocess.run(
        [sys.executable, "-c", CHILD_PROLOGUE + QUIET_SCRIPT + CHILD_EPILOGUE, REPO],
        env=child_env(tmp_path), capture_output=True, text=True, timeout=120)
    line = next((out for out in proc.stdout.splitlines() if out.startswith("RESULT ")), "")
    assert line, proc.stdout + proc.stderr
    said = json.loads(line[len("RESULT "):])
    assert [m for m in said if "QObject" in m or "nullptr" in m] == [], said
