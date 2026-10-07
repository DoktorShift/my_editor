# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The monospace font, as this system really has it.

``constants.MONO_FONT`` names the monospace font each system usually
ships (Menlo, Consolas, Noto Sans Mono). Not every Linux system has
Noto Sans Mono, and asking Qt for a family it lacks gives the system's
default font, which is proportional: code and plain text then lost their
columns. Here a missing family falls back to the system's own monospace
font, and when that has no fixed columns either (Qt's offscreen platform
on Windows names a generic family it cannot find), to the first
installed family that has them. Style sheets cannot carry that fallback,
so they use ``monospace_family()``, the family Qt really draws with.
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


def _first_fixed_pitch_family() -> Optional[str]:
    return next((family for family in QFontDatabase.families()
                 if QFontDatabase.isFixedPitch(family)
                 and not QFontDatabase.isPrivateFamily(family)), None)
