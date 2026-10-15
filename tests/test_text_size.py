# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how big text reads, and in which font (text_size.py, fonts.py).

What must hold (Apple's guidelines on typography: "macOS doesn't support
Dynamic Type", so the app offers its own text size, for the content and
not the window's chrome; nothing smaller than 10 pt on a Mac):

  The text size is the writing font's size in points, as a Mac's fonts
  are measured ("Helvetica 12"): 8 to 32 pt, none drawn smaller than
  10 px (10 pt on a Mac, where a pixel is a point), up to twice the
  writing font's own size at least. Zoom In and Zoom Out step through
  them and stop at both ends; Actual Size is the writing font's own size
  (16 px: 16 pt on a Mac, 12 pt at 96 dpi). Code keeps its size beside
  the writing. The writing font is the system's, a reading serif
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
from text_size import SIZES, TextSize, text_size  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def actual_size():
    yield
    text_size().reset()


def test_the_size_is_in_points_and_steps_through_the_sizes_offered():
    mac = TextSize(dpi=72)                        # a pixel is a point
    assert mac.actual() == 16 and mac.points() == 16 and mac.pixels(16) == 16
    assert mac.sizes() == (10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32)
    mac.grow()
    assert mac.points() == 18 and mac.pixels(16) == 18 and mac.pixels(14) == 16
    for _ in range(20):
        mac.grow()
    assert mac.points() == 32 and not mac.can_grow()
    for _ in range(20):
        mac.shrink()
    assert mac.points() == 10 and not mac.can_shrink()
    mac.reset()
    assert mac.points() == 16
    mac.set_points(15)                            # between sizes: the nearest
    assert mac.points() == 14


def test_other_screens_offer_the_small_sizes_that_still_read():
    """At 96 dpi a point is 1.33 px: 8 pt is drawn at 10.7 px, so it is
    offered there; the writing font's own size is 12 pt."""
    windows = TextSize(dpi=96)
    assert windows.actual() == 12 and windows.sizes()[:3] == (8, 9, 10)
    assert windows.sizes()[-1] >= 2 * windows.actual()        # 200 % at least
    odd = TextSize(dpi=120)                       # its own size is no usual one
    assert odd.actual() in odd.sizes() and odd.pixels(16) == 16


def test_at_the_smallest_size_text_stays_readable():
    for dpi in (72, 96):
        size = TextSize(dpi=dpi)
        size.set_points(SIZES[0])
        for pixels in (fonts.WRITING_PIXEL_SIZE, fonts.CODE_PIXEL_SIZE):
            assert size.pixels(pixels) >= 10, (dpi, pixels)


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
    text_size().set_points(text_size().sizes()[text_size().sizes().index(text_size().actual()) + 3])
    after = sizes()
    assert after == pytest.approx([size * text_size().scale() for size in before])
    assert text_size().scale() > 1
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
r["stored"] = load_settings().get("writing_size")
r["actual_points"] = text_size().actual()
r["points"] = text_size().points()
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
    assert r["before"] == 16 and r["bigger"] > 16         # two sizes up
    assert r["points"] > r["actual_points"]
    assert r["stored"] == r["points"] and r["said"] == f"Text Size: {r['points']} pt"
    assert r["actual_on"] is True and r["actual"] == 16
    assert r["serif_menu"] is False
    assert r["mono"] == ["monospace", True]
    assert r["back"] == "serif"                            # the font before Monospace
    # Off in a PDF tab: the reader's own zoom keys are the same ones.
    assert r["in_pdf"] == [False, False] and r["in_text"] == [True, True]


def test_a_text_size_kept_in_percent_is_read_as_the_nearest_size():
    """Before sizes were points they were kept in percent ("text_size")."""
    from text_size import points_from_percent
    mac = TextSize(dpi=72)
    assert points_from_percent(125, mac) == 20 and points_from_percent(80, mac) == 13
    assert points_from_percent("junk", mac) == 16
