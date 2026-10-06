# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a screen reader would announce, for tests.

VoiceOver, NVDA and Orca learn what a control is called from Qt's
accessibility interfaces. ``unnamed_controls`` asks those same interfaces,
so a control a screen reader would announce only as "button" or "text
field" (an icon without a name, a field with only a placeholder, a list
without a label) fails the test that calls it. Every page of a window is
checked, shown or not: a name does not depend on which page is up.
"""

from __future__ import annotations

from typing import List

from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QWidget

_Role = QAccessible.Role

# Roles a person operates; each needs a name of its own. Text a person only
# reads (labels, static text) is announced as it is.
INTERACTIVE = frozenset({
    _Role.Button, _Role.PushButton, _Role.CheckBox, _Role.RadioButton,
    _Role.ComboBox, _Role.EditableText, _Role.SpinBox, _Role.Slider,
    _Role.ButtonMenu, _Role.ButtonDropDown, _Role.List, _Role.Tree, _Role.Table,
    _Role.PageTabList,
})


# Qt's own parts of a control, named (or not) by Qt itself: the clear
# button inside a search field.
QT_INTERNALS = frozenset({"QLineEditIconButton"})


def unnamed_controls(root: QWidget) -> List[str]:
    """The interactive controls under ``root`` without an accessible name."""
    found = []
    for widget in [root, *root.findChildren(QWidget)]:
        if widget.metaObject().className() in QT_INTERNALS:
            continue
        interface = QAccessible.queryAccessibleInterface(widget)
        if interface is None or interface.role() not in INTERACTIVE:
            continue
        if not interface.text(QAccessible.Text.Name).strip():
            found.append(_describe(widget))
    return found


def _describe(widget: QWidget) -> str:
    parts = [type(widget).__name__]
    if widget.objectName():
        parts.append(f"#{widget.objectName()}")
    if widget.toolTip():
        parts.append(f"(tooltip {widget.toolTip()!r})")
    parent = widget.parentWidget()
    if parent is not None:
        parts.append(f"in {type(parent).__name__}"
                     + (f"#{parent.objectName()}" if parent.objectName() else ""))
    return " ".join(parts)
