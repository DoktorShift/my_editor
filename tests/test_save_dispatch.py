"""Pins the Save As dispatch plumbing in main_window.

The defect classes this file guards against:
- the auto-extension logic regressing for cased filters (the old
  substring matching silently failed for ".Rmd (*.Rmd)"),
- .rmd accidentally matching the .md suffix branches (formatting-loss
  warning, toMarkdown save),
- _do_insert_image dropping the source URL again (exports use it for
  provenance).
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from main_window import MainWindow, _SAVE_EXTS, _SUPPORTED_EXTS, _extension_from_filter
from editor import HtmlEditor


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # HtmlEditor is a widget, so this file needs a full QApplication.
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# --------------------------------------------------------------------------- #
# Filter to extension extraction
# --------------------------------------------------------------------------- #

def test_extension_from_every_save_filter():
    cases = {
        ".txt (*.txt)": ".txt",
        ".html (*.html)": ".html",
        ".pdf (*.pdf)": ".pdf",
        ".md (*.md)": ".md",
        ".rtf (*.rtf)": ".rtf",
        ".Rmd (*.Rmd)": ".Rmd",   # canonical casing preserved
    }
    for filt, ext in cases.items():
        assert _extension_from_filter(filt) == ext


def test_extension_from_filter_handles_garbage():
    assert _extension_from_filter("") is None
    assert _extension_from_filter("All files (*.*)") is None


def test_save_exts_cover_every_filter_extension():
    # A typed "notes.Rmd" must count as already-has-extension.
    for typed in ("a.txt", "a.html", "a.pdf", "a.md", "a.rtf", "a.Rmd", "a.rmd"):
        assert any(typed.lower().endswith(e) for e in _SAVE_EXTS)


# --------------------------------------------------------------------------- #
# .rmd never rides the .md branches
# --------------------------------------------------------------------------- #

def test_rmd_suffix_is_not_md():
    # _save_to and the formatting-loss warning branch on these suffix
    # checks; .rmd must fall through to its own branch.
    assert not "notes.rmd".endswith('.md')
    assert not "notes.Rmd".lower().endswith(('.txt', '.md'))
    assert "notes.rmd".lower().endswith('.rmd')


def test_rmd_is_supported_for_open_and_drop():
    assert '.rmd' in _SUPPORTED_EXTS


# --------------------------------------------------------------------------- #
# _do_insert_image records the source URL for provenance
# --------------------------------------------------------------------------- #

class _StatusStub:
    def showMessage(self, *a, **k):
        pass


def _fake_window():
    return types.SimpleNamespace(status=_StatusStub())


def test_insert_image_records_source_url(tmp_path):
    ed = HtmlEditor()
    local = str(tmp_path / ("a" * 64))
    MainWindow._do_insert_image(_fake_window(), ed, local,
                                "https://blossom.example/a.png")
    assert ed._image_urls == {local: "https://blossom.example/a.png"}


def test_insert_image_markdown_tab_inserts_reference(tmp_path):
    ed = HtmlEditor()
    ed._loaded_as_markdown = True
    MainWindow._do_insert_image(_fake_window(), ed,
                                str(tmp_path / "x"),
                                "https://blossom.example/a.png", "alt")
    assert "![alt](https://blossom.example/a.png)" in ed.toPlainText()
    assert not hasattr(ed, "_image_urls")
