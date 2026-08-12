"""Pins the built-in PDF viewer (pdf_viewer.PdfViewerTab).

The defect classes this file guards against:
- the reading-position store corrupting, growing unbounded, or
  restoring an out-of-range page after the document shrank,
- load errors (missing file, not a PDF) leaking through as load_ok,
- page navigation drifting (clamping, page-entry field, page display),
- zoom escaping its clamps or the fit-mode buttons losing sync,
- search not starting from the page being read, or not wrapping,
- reload after an external change losing the reading position.

PdfViewerTab is a widget, so a full QApplication plus the offscreen
platform is required. The saved-position restore is deferred to first
show, so tests that rely on it must show() and process events.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication

import pdf_viewer
from export_pdf import export_pdf
from pdf_viewer import PdfViewerTab, load_view_state, save_view_state


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def isolated_positions(tmp_path, monkeypatch):
    """Point the reading-position store at a per-test file."""
    monkeypatch.setattr(pdf_viewer, "_POSITIONS_PATH", str(tmp_path / "positions.json"))


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    """A six-page searchable PDF built with the project's own exporter."""
    doc = QTextDocument()
    doc.setPlainText("needle in the haystack\n" + "plain body line\n" * 300)
    path = str(tmp_path_factory.mktemp("pdfs") / "sample.pdf")
    export_pdf(doc, path, title="Sample Title",
               page_setup={"page_size": "A4", "orientation": "portrait",
                           "margins_mm": 20.0})
    return path


def _shown(tab, qt_app):
    tab.resize(900, 700)
    tab.show()
    qt_app.processEvents()
    return tab


def _force_full_search_scan(tab):
    # QPdfSearchModel fills its result list lazily in the background;
    # poke every page so tests see the final count deterministically.
    for page in range(tab.document.pageCount()):
        tab._search.resultsOnPage(page)


# --------------------------------------------------------------------------- #
# Reading-position store
# --------------------------------------------------------------------------- #

def test_view_state_round_trip(sample_pdf):
    save_view_state(sample_pdf, {"page": 3, "zoom_mode": "fit-width"})
    state = load_view_state(sample_pdf)
    assert state["page"] == 3
    assert state["zoom_mode"] == "fit-width"


def test_view_state_missing_and_corrupt(tmp_path, sample_pdf):
    assert load_view_state(sample_pdf) is None
    with open(pdf_viewer._POSITIONS_PATH, "w") as f:
        f.write("{ not json")
    assert load_view_state(sample_pdf) is None
    # A corrupt store must not break saving either.
    save_view_state(sample_pdf, {"page": 1})
    assert load_view_state(sample_pdf)["page"] == 1


def test_view_state_lru_eviction(monkeypatch):
    monkeypatch.setattr(pdf_viewer, "_MAX_POSITIONS", 3)
    ts = iter(range(1000, 2000))
    monkeypatch.setattr(pdf_viewer.time, "time", lambda: next(ts))
    for i in range(5):
        save_view_state(f"/books/{i}.pdf", {"page": i})
    with open(pdf_viewer._POSITIONS_PATH) as f:
        stored = json.load(f)
    assert len(stored) == 3
    # The oldest two entries were evicted, the newest survive.
    assert "/books/0.pdf" not in stored and "/books/1.pdf" not in stored
    assert load_view_state("/books/4.pdf")["page"] == 4


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def test_load_ok_and_metadata(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    assert tab.load_ok and not tab.load_error
    assert tab.document.pageCount() > 1
    assert tab.document_title() == "Sample Title"
    assert tab.page_display() == f"Page 1 / {tab.document.pageCount()}"


def test_load_missing_file(tmp_path):
    tab = PdfViewerTab(str(tmp_path / "gone.pdf"))
    assert not tab.load_ok
    assert tab.load_error


def test_load_not_a_pdf(tmp_path):
    path = str(tmp_path / "fake.pdf")
    with open(path, "w") as f:
        f.write("just text")
    tab = PdfViewerTab(path)
    assert not tab.load_ok
    assert "not a valid PDF" in tab.load_error


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #

def test_jump_clamps_and_syncs_page_edit(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    last = tab.document.pageCount() - 1
    tab.jump_to_page(999)
    assert tab.current_page() == last
    tab.jump_to_page(-5)
    assert tab.current_page() == 0
    tab.jump_to_page(2)
    qt_app.processEvents()
    assert tab._page_edit.text() == "3"


def test_page_entry_jumps(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab._page_edit.setText("4")
    tab._on_page_entered()
    assert tab.current_page() == 3
    # Garbage input restores the real page instead of raising.
    tab._page_edit.setText("")
    tab._on_page_entered()
    assert tab.current_page() == 3


# --------------------------------------------------------------------------- #
# Zoom
# --------------------------------------------------------------------------- #

def test_zoom_steps_and_clamps(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    assert tab.view.zoomMode() == QPdfView.ZoomMode.FitToWidth
    tab.zoom_in()
    assert tab.view.zoomMode() == QPdfView.ZoomMode.Custom
    assert tab._zoom_label.text() == "120%"
    for _ in range(40):
        tab.zoom_in()
    assert tab.view.zoomFactor() <= pdf_viewer._MAX_ZOOM
    for _ in range(80):
        tab.zoom_out()
    assert tab.view.zoomFactor() >= pdf_viewer._MIN_ZOOM


def test_fit_buttons_track_mode(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    assert tab._fit_width_btn.isChecked() and not tab._fit_page_btn.isChecked()
    tab._set_zoom_mode(QPdfView.ZoomMode.FitInView)
    assert tab._fit_page_btn.isChecked() and not tab._fit_width_btn.isChecked()
    assert tab._zoom_label.text() == "Fit"
    tab.zoom_in()
    assert not tab._fit_width_btn.isChecked() and not tab._fit_page_btn.isChecked()


def test_next_prev_page_step_and_clamp(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.next_page()
    assert tab.current_page() == 1
    tab.prev_page()
    assert tab.current_page() == 0
    tab.prev_page()  # already on the first page: stays put
    assert tab.current_page() == 0


def test_go_to_page_focuses_page_box(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab._focus_page_box()
    assert tab._page_edit.hasFocus()
    assert tab._page_edit.selectedText() == tab._page_edit.text()


def test_actual_size_zoom(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.zoom_in()
    tab.actual_size()
    assert tab.view.zoomMode() == QPdfView.ZoomMode.Custom
    assert tab.view.zoomFactor() == 1.0
    assert tab._zoom_label.text() == "100%"


def test_outline_disabled_without_bookmarks(sample_pdf, qt_app):
    # The exporter-produced sample carries no outline, so the Contents
    # toggle must stay greyed out and the panel must refuse to open.
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    assert not tab._outline_btn.isEnabled()
    tab.toggle_outline()
    assert not tab.outline.isVisible()


def _build_outlined_pdf(path):
    """A minimal 3-page PDF with a two-entry outline, written by hand
    (the app's own exporter never emits outlines)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Outlines 6 0 R /PageMode /UseOutlines >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        b"<< /Type /Outlines /First 7 0 R /Last 8 0 R /Count 2 >>",
        b"<< /Title (Chapter One) /Parent 6 0 R /Next 8 0 R /Dest [4 0 R /XYZ 0 842 0] >>",
        b"<< /Title (Chapter Two) /Parent 6 0 R /Prev 7 0 R /Dest [5 0 R /XYZ 0 842 0] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    with open(path, "wb") as f:
        f.write(bytes(out))


def test_outline_panel_and_jump(tmp_path, qt_app):
    path = str(tmp_path / "outlined.pdf")
    _build_outlined_pdf(path)
    tab = _shown(PdfViewerTab(path), qt_app)
    assert tab.load_ok
    assert tab._outline_btn.isEnabled()
    assert tab._bookmarks.rowCount() == 2

    tab.toggle_outline()
    assert tab.outline.isVisible() and tab._outline_btn.isChecked()

    idx = tab._bookmarks.index(1, 0)
    assert idx.data() == "Chapter Two"
    tab._on_outline_activated(idx)
    qt_app.processEvents()
    assert tab.current_page() == 2

    tab.toggle_outline()
    assert not tab.outline.isVisible()


# --------------------------------------------------------------------------- #
# Find
# --------------------------------------------------------------------------- #

def test_find_starts_at_current_page_and_wraps(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.toggle_findbar()
    tab.findbar.edit.setText("plain body")
    qt_app.processEvents()
    _force_full_search_scan(tab)
    count = tab._search.count()
    assert count > 2

    # Reading page 3: the first hit must not yank the reader to page 1.
    tab.jump_to_page(2)
    tab._current_result = -1
    tab.find_next()
    assert tab._search.resultAtIndex(tab._current_result).page() >= 2

    # Wrap-around: stepping past the last result lands on the first.
    tab._jump_to_result(count - 1)
    tab.find_next()
    assert tab._current_result == 0
    tab.find_prev()
    assert tab._current_result == count - 1
    assert tab.findbar.match_info.text() == f"{count} / {count}"


def test_find_no_matches_and_close_clears(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.toggle_findbar()
    tab.findbar.edit.setText("zzz-not-in-doc")
    qt_app.processEvents()
    _force_full_search_scan(tab)
    tab._update_match_label()
    assert tab.findbar.match_info.text() == "No matches"
    tab.find_next()  # must not raise on zero results
    tab.toggle_findbar()
    assert not tab.findbar.isVisible()
    assert tab._search.searchString() == ""


# --------------------------------------------------------------------------- #
# Persistence + reload
# --------------------------------------------------------------------------- #

def test_save_and_restore_view_state(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.jump_to_page(4)
    tab.zoom_in()
    qt_app.processEvents()
    tab.save_view_state()
    state = load_view_state(sample_pdf)
    assert state["page"] == 4
    assert state["zoom_mode"] == "custom"
    assert abs(state["zoom"] - 1.2) < 1e-6

    tab2 = _shown(PdfViewerTab(sample_pdf), qt_app)
    qt_app.processEvents()  # restore is deferred to first show
    assert tab2.current_page() == 4
    assert tab2.view.zoomMode() == QPdfView.ZoomMode.Custom
    assert abs(tab2.view.zoomFactor() - 1.2) < 1e-6


def test_restore_ignores_out_of_range_page(sample_pdf, qt_app):
    save_view_state(sample_pdf, {"page": 9999, "zoom_mode": "fit-width"})
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    qt_app.processEvents()
    assert tab.current_page() == 0


def test_reload_keeps_position(sample_pdf, qt_app):
    tab = _shown(PdfViewerTab(sample_pdf), qt_app)
    tab.jump_to_page(3)
    qt_app.processEvents()
    tab.reload()
    qt_app.processEvents()
    assert tab.load_ok
    assert tab.current_page() == 3


# --------------------------------------------------------------------------- #
# Dispatch wiring
# --------------------------------------------------------------------------- #

def test_pdf_is_supported_for_open_and_drop():
    from main_window import _SUPPORTED_EXTS
    assert '.pdf' in _SUPPORTED_EXTS
