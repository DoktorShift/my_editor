"""Settings > Text cursor: thin, medium, thick or a block, as the person
likes it, in every document; chosen once and remembered."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import editor  # noqa: E402
import settings  # noqa: E402


# Every window a test makes, closed and freed when it ends: a shown window
# left behind would be painted, and freed, during a later test.
_made: list = []


@pytest.fixture(autouse=True)
def _close_windows():
    yield
    while _made:
        widget = _made.pop()
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _app():
    return QApplication.instance() or QApplication([])


def test_each_cursor_style_draws_as_named():
    _app()
    ed = editor.HtmlEditor()
    _made.append(ed)
    ed.show()
    ed.setFocus()
    QApplication.processEvents()
    for style, width in (("thin", 1), ("medium", 2), ("thick", 4)):
        ed.set_caret_style(style)
        assert ed.cursorWidth() == width and not ed.block_caret_shown()
    ed.set_caret_style("block")
    assert ed.cursorWidth() == 0
    ed._cursor_visible = True
    assert ed.block_caret_shown() == ed.hasFocus()


def test_the_font_never_changes_the_chosen_cursor():
    _app()
    ed = editor.HtmlEditor()
    _made.append(ed)
    ed.set_caret_style("thick")
    ed.set_writing_font(True)
    assert ed.cursorWidth() == 4
    ed.set_writing_font(False)
    assert ed.cursorWidth() == 4
    ed.set_caret_style("block")
    ed.set_writing_font(True)
    assert ed.cursorWidth() == 0


def test_an_unknown_style_is_medium():
    _app()
    ed = editor.HtmlEditor()
    _made.append(ed)
    ed.set_caret_style("enormous")
    assert ed.caret_style() == "medium" and ed.cursorWidth() == 2


def test_the_choice_reaches_every_open_document_and_is_remembered(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    import main_window
    w = main_window.MainWindow()
    try:
        first = w.new_tab()
        second = w.new_tab()
        w._set_caret_style("thick")
        assert first.cursorWidth() == 4 and second.cursorWidth() == 4
        assert settings.load_settings().get("caret_style") == "thick"
        third = w.new_tab()
        assert third.caret_style() == "thick"
    finally:
        for ed in w._all_editors():
            ed.document().setModified(False)
        w.close()


def test_settings_offers_the_four_cursors_and_chooses_through_the_window():
    _app()
    from PySide6.QtGui import QAction
    from settings_window import CARET_CHOICES, SettingsWindow
    chosen = []
    window = SettingsWindow(
        appearance={"system": QAction("Automatic")}, languages=[],
        choose_language=lambda code: None, monospace=QAction("Mono"),
        paste_choice=lambda: "ask", choose_paste=lambda value: None,
        nostr_in_use=lambda: False, format_bar=lambda: True,
        choose_format_bar=lambda on: None,
        caret_style=lambda: "medium", choose_caret_style=chosen.append)
    assert CARET_CHOICES == ("thin", "medium", "thick", "block")
    box = window.caret
    assert [box.itemData(i) for i in range(box.count())] == list(CARET_CHOICES)
    assert box.currentData() == "medium"
    box.activated.emit(box.findData("block"))
    assert chosen == ["block"]
    window.close()
