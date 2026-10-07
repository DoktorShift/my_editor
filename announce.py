# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling a screen reader about something that never takes the focus.

A list that opens under the caret while the text keeps the focus (the
slash menu) changes nothing a screen reader follows by itself. Qt's
events for a changed name or description do not help: macOS and Windows
drop them, and on Linux they are a property change, not speech. An
announcement is spoken everywhere: VoiceOver gets it as an announcement
request, Narrator and NVDA as a UI Automation notification, Orca as an
AT-SPI announcement.

A new module because nothing in the app announced before; everything
that needs to speak goes through :func:`announce`, which tests replace.
"""

from __future__ import annotations

from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent


def announce(source, text: str) -> None:
    """Have screen readers say ``text`` on behalf of the widget ``source``,
    politely (after what they are saying)."""
    if not text:
        return
    QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(source, text))
