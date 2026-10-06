# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The editor's fonts, as this system really has them.

``constants.MONO_FONT`` names the monospace font each system usually
ships (Menlo, Consolas, Noto Sans Mono). Not every Linux system has
Noto Sans Mono, and asking Qt for a family it lacks gives the system's
default font, which is proportional: code and plain text then lost their
columns. Here a missing family falls back to the system's own monospace
font. Style sheets cannot carry that fallback, so they use
``monospace_family()``, the family Qt really draws with.

Writing (Markdown files and drafts) is set in ``writing_font()``: the
system's own text font (San Francisco on a Mac, Segoe UI on Windows,
the desktop's font on Linux), asked for as the system font rather than
by a family name that may not be installed, at a size made for reading
on screen.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

from constants import MONO_FONT

_family: Optional[str] = None


def monospace_font(point_size: Optional[float] = None) -> QFont:
    """The app's monospace font, at ``point_size`` when given. Needs a
    QGuiApplication."""
    if QFontDatabase.hasFamily(MONO_FONT):
        font = QFont(MONO_FONT)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    if point_size is not None:
        font.setPointSizeF(point_size)
    return font


def monospace_family() -> str:
    """The family name of ``monospace_font()`` as installed, for style
    sheets. Looked up once."""
    global _family
    if _family is None:
        _family = QFontInfo(monospace_font()).family()
    return _family


# The size writing is set in, in pixels: what long-form readers use on
# screen (16 to 18), where the monospace font for code stays at 14.
WRITING_PIXEL_SIZE = 16
CODE_PIXEL_SIZE = 14


def writing_font(pixel_size: int = WRITING_PIXEL_SIZE) -> QFont:
    """The system's proportional text font, for writing. Needs a
    QGuiApplication."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(pixel_size)
    return font


def code_font(pixel_size: int = CODE_PIXEL_SIZE) -> QFont:
    """The monospace font at the size code and plain text are set in."""
    font = monospace_font()
    font.setPixelSize(pixel_size)
    return font
