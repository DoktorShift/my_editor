# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how big text reads, and in which font (text_size.py, fonts.py).

What must hold (Apple's guidelines on typography: "macOS doesn't support
Dynamic Type", so the app offers its own text size, for the content and
not the window's chrome; nothing smaller than 10 pt on a Mac):

  The text size steps from 80 % to 200 % and stops at both ends; Actual
  Size goes back to 100 %. Writing is 16 px at 100 %, code 14 px, both
  above 10 pt at 80 %. The writing font is the system's, a reading serif
  the system has, the monospace font, or an installed family. A preview
  takes every size of its text by the text size, a heading staying larger
  than the body. In the window, View > Zoom In makes the documents' text
  bigger and is remembered; the commands are off in a PDF tab, whose own
  zoom keys they share; Monospace as the writing font is View > Use
  Monospace Font for Writing, and turning that off goes back to the font
  before it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QFontInfo  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import fonts  # noqa: E402
from text_size import ACTUAL, STEPS, TextSize, text_size  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def actual_size():
    yield
    text_size().set_percent(ACTUAL)


def test_the_size_steps_and_stops_at_both_ends():
    size = TextSize()
    assert size.percent() == 100 and size.pixels(16) == 16
    size.grow()
    assert size.percent() == 110
    for _ in range(20):
        size.grow()
    assert size.percent() == STEPS[-1] and not size.can_grow()
    for _ in range(20):
        size.shrink()
    assert size.percent() == STEPS[0] and not size.can_shrink()
    size.reset()
    assert size.percent() == ACTUAL
    size.set_percent(130)                         # between steps: the nearest
    assert size.percent() == 125


def test_at_the_smallest_size_text_stays_readable():
    smallest = TextSize(STEPS[0])
    for pixels in (fonts.WRITING_PIXEL_SIZE, fonts.CODE_PIXEL_SIZE):
        # On a Mac a pixel is a point: the guidelines' least is 10 pt.
        assert smallest.pixels(pixels) >= 11


def test_the_writing_fonts_are_the_systems_a_serif_the_monospace_or_one_installed():
    system = fonts.writing_font(16, fonts.SYSTEM)
    assert system.pixelSize() == 16
    mono = fonts.writing_font(16, fonts.MONOSPACE)
    assert QFontInfo(mono).fixedPitch()
    serif = fonts.writing_font(16, fonts.SERIF)
    if fonts.serif_family():
        assert serif.family() == fonts.serif_family()
    installed = fonts.monospace_family()
    assert fonts.writing_font(16, installed).family() == installed
    assert fonts.writing_font(16, "No Such Font Anywhere").family() == system.family()


def test_a_preview_takes_every_size_by_the_text_size():
    from nostr.preview import NostrPreview
    preview = NostrPreview()
    preview.show_markdown("# Heading\n\nBody text.")

    def sizes():
        doc, found = preview.document(), []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                size = it.fragment().charFormat().fontPointSize()
                found.append(size or doc.defaultFont().pointSizeF())
                it += 1
            block = block.next()
        return found
    before = sizes()
    text_size().set_percent(150)
    after = sizes()
    assert after == pytest.approx([size * 1.5 for size in before])
    assert after[0] > after[-1]                   # the heading stays larger
    preview.show_markdown("# Heading\n\nBody text.")   # shown again: at the size
    assert sizes() == pytest.approx(after)


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, fonts
from text_size import text_size
from settings import load_settings
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
app.processEvents()
r = {"before": ed.font().pixelSize()}
w.act_zoom_in.trigger()
w.act_zoom_in.trigger()
app.processEvents()
r["bigger"] = ed.font().pixelSize()
r["stored"] = load_settings().get("text_size")
r["said"] = w.status.currentMessage()
r["actual_on"] = w.act_actual_size.isEnabled()
w.act_actual_size.trigger()
r["actual"] = ed.font().pixelSize()
w.set_writing_family(fonts.SERIF)
r["serif_menu"] = w.act_monospace_writing.isChecked()
w.act_monospace_writing.trigger()
r["mono"] = (w.writing_family, w.act_monospace_writing.isChecked())
w.act_monospace_writing.trigger()
r["back"] = w.writing_family
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
r["in_pdf"] = [a.isEnabled() for a in (w.act_zoom_in, w.act_zoom_out)]
w.tabs.setCurrentIndex(w._editor_widget_index(ed))
app.processEvents()
r["in_text"] = [a.isEnabled() for a in (w.act_zoom_in, w.act_zoom_out)]
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_zoom_in_makes_the_documents_text_bigger_and_is_remembered(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["before"] == 16 and r["bigger"] == 20       # 100 %, then 110 % and 125 %
    assert r["stored"] == 125 and r["said"] == "Text Size: 125 %"
    assert r["actual_on"] is True and r["actual"] == 16
    assert r["serif_menu"] is False
    assert r["mono"] == ["monospace", True]
    assert r["back"] == "serif"                            # the font before Monospace
    # Off in a PDF tab: the reader's own zoom keys are the same ones.
    assert r["in_pdf"] == [False, False] and r["in_text"] == [True, True]
