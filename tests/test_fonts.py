# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The monospace font is monospace on every system, installed or not,
and the tests draw with the system's fonts on every system."""

import os

import pytest
from PySide6.QtGui import QFontDatabase, QFontInfo

import fonts
from tests.app_process import offscreen_fonts


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


def test_the_editor_style_sheet_uses_it():
    from editor import HtmlEditor
    assert f'font-family: "{fonts.monospace_family()}"' in HtmlEditor().styleSheet()


def test_the_offscreen_platform_gets_the_system_fonts_on_windows(monkeypatch):
    # The test run and its child processes draw offscreen; on Windows that
    # platform reads fonts from a folder and would otherwise find none.
    monkeypatch.setenv("SystemRoot", r"D:\Windows")
    env = {"QT_QPA_PLATFORM": "offscreen"}
    offscreen_fonts(env, platform="win32")
    assert env["QT_QPA_FONTDIR"] == os.path.join(r"D:\Windows", "Fonts")
    chosen = {"QT_QPA_PLATFORM": "offscreen", "QT_QPA_FONTDIR": r"C:\Fonts"}
    offscreen_fonts(chosen, platform="win32")
    assert chosen["QT_QPA_FONTDIR"] == r"C:\Fonts"            # a folder chosen stays
    # Windows' own platform, and offscreen elsewhere, ask the system.
    for platform, qt_platform in (("win32", "windows"), ("darwin", "offscreen"),
                                  ("linux", "offscreen")):
        env = {"QT_QPA_PLATFORM": qt_platform}
        offscreen_fonts(env, platform=platform)
        assert "QT_QPA_FONTDIR" not in env
