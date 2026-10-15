# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The editor's fonts, as this system really has them.

``constants.MONO_FONT`` names the monospace font each system usually
ships (Menlo, Consolas, Noto Sans Mono). Not every Linux system has
Noto Sans Mono, and asking Qt for a family it lacks gives the system's
default font, which is proportional: code and plain text then lost their
columns. Here a missing family falls back to the system's own monospace
font, and when that has no fixed columns either (Qt's offscreen platform
on Windows names a generic family it cannot find), to the first
installed family that has them. Style sheets cannot carry that fallback,
so they use ``monospace_family()``, the family Qt really draws with.

Writing (Markdown files and drafts) is set in ``writing_font()``, in the
font chosen in Settings: the system's own text font by default (San
Francisco on a Mac, Segoe UI on Windows, the desktop's font on Linux),
asked for as the system font rather than by a family name that may not
be installed; a serif made for reading on screen, the first each system
ships (Iowan Old Style or Charter on a Mac, Georgia on Windows, Noto
Serif or DejaVu Serif on Linux); the monospace font; or any installed
family. The size is one made for reading on screen.
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
        if not QFontInfo(font).fixedPitch():
            fixed = _first_fixed_pitch_family()
            if fixed:
                font.setFamily(fixed)
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


# The writing fonts offered: the system's, a serif, the monospace font;
# any other value is an installed family's name.
SYSTEM, SERIF, MONOSPACE = "system", "serif", "monospace"

# Serifs made for reading on screen, in the order looked for: what a Mac,
# Windows and the common Linux desktops ship.
SERIF_FAMILIES = ("Iowan Old Style", "Charter", "Georgia", "Cambria", "Noto Serif",
                  "DejaVu Serif", "Liberation Serif")


def serif_family() -> str:
    """The first reading serif this system has; "" when it has none."""
    return next((family for family in SERIF_FAMILIES if QFontDatabase.hasFamily(family)), "")


def writing_font(pixel_size: int = WRITING_PIXEL_SIZE, family: str = SYSTEM) -> QFont:
    """The font for writing at ``pixel_size``: ``family`` is SYSTEM, SERIF,
    MONOSPACE or an installed family's name (the system's font when it is
    not installed). Needs a QGuiApplication."""
    if family == MONOSPACE:
        font = monospace_font()
    elif family == SERIF and serif_family():
        font = QFont(serif_family())
        font.setStyleHint(QFont.StyleHint.Serif)
    elif family not in (SYSTEM, SERIF) and QFontDatabase.hasFamily(family):
        font = QFont(family)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(pixel_size)
    return font


def code_font(pixel_size: int = CODE_PIXEL_SIZE) -> QFont:
    """The monospace font at the size code and plain text are set in."""
    font = monospace_font()
    font.setPixelSize(pixel_size)
    return font


def names_monospace(families) -> bool:
    """Whether a page's font families (CSS ``font-family``, as Qt read
    them) are a monospace font: a code font stack names the generic family,
    and Qt gives a code element (code, kbd, samp, tt) the system's own
    fixed font (Menlo on a Mac, Courier New on Windows), or the generic
    family where there is none."""
    names = {family.strip().strip("'\"").lower() for family in (families or [])}
    return bool(names & {"monospace", "ui-monospace", system_fixed_family().lower()})


def system_fixed_family() -> str:
    """The system's own fixed-width font."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def _first_fixed_pitch_family() -> Optional[str]:
    return next((family for family in QFontDatabase.families()
                 if QFontDatabase.isFixedPitch(family)
                 and not QFontDatabase.isPrivateFamily(family)), None)
