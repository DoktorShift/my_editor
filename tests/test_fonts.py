# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The monospace font is monospace on every system, installed or not."""

import pytest
from PySide6.QtGui import QFontDatabase, QFontInfo

import fonts


@pytest.fixture(autouse=True)
def fresh_lookup(monkeypatch):
    monkeypatch.setattr(fonts, "_family", None)


def test_the_monospace_font_has_fixed_columns():
    font = fonts.monospace_font(13)
    assert QFontInfo(font).fixedPitch()
    assert font.pointSizeF() == 13


def test_a_missing_preferred_font_falls_back_to_a_monospace_one(monkeypatch):
    # Not every Linux system has Noto Sans Mono; asking for a missing
    # family must not end in the system's proportional default.
    monkeypatch.setattr(fonts, "MONO_FONT", "No Such Font Family 2026")
    assert QFontInfo(fonts.monospace_font()).fixedPitch()
    assert QFontInfo(fonts.monospace_font()).family() != "No Such Font Family 2026"


def test_style_sheets_get_a_family_this_system_really_has():
    family = fonts.monospace_family()
    assert family and QFontDatabase.hasFamily(family)


def test_the_editor_uses_it_for_code_and_the_system_font_for_writing():
    from editor import HtmlEditor
    ed = HtmlEditor()
    assert QFontInfo(ed.font()).fixedPitch()
    assert ed.font().pixelSize() == fonts.CODE_PIXEL_SIZE
    ed.set_writing_font(True)
    assert not QFontInfo(ed.font()).fixedPitch()
    assert ed.font().pixelSize() == fonts.WRITING_PIXEL_SIZE
    ed.set_writing_font(False)
    assert QFontInfo(ed.font()).fixedPitch()


def test_the_writing_font_is_one_the_system_has():
    family = QFontInfo(fonts.writing_font()).family()
    assert family and not QFontInfo(fonts.writing_font()).fixedPitch()
