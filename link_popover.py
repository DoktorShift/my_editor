# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Add Link / Edit Link popover.

A small panel that opens right below the words being linked, the way
Pages and Mail ask for a link: the text the link shows, and its
address. The address is checked as it is typed (link_url.py), and why
it cannot be a link is said right under it, never in an alert; the
button that adds the link only works once the address can be one.
Return adds or updates the link, Escape closes the panel, and the
editor gets the focus back either way.

The popover only asks. What becomes of the answer is the editor's
business: it connects ``applied(text, address)`` and ``removed()``. The
text is "" unless the person changed it: the linked words stay exactly
as they are (their spaces, their paragraphs) unless they were retyped.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

import theme
from constants import (
    DARK_BG, DARK_BORDER, DARK_FG, DARK_MENU_BG, LIGHT_BG, LIGHT_BORDER, LIGHT_FG,
)
from i18n import _
from link_url import is_bare_http_url, normalize_link_input


class LinkPopover(QFrame):
    """Text and Address of a link; ``applied(text, href)`` on Add or
    Update, ``removed()`` on Remove Link (only while editing a link).
    ``text_editable`` False (words of several paragraphs) shows the words
    without letting them be retyped."""

    applied = Signal(str, str)
    removed = Signal()

    WIDTH = 380

    def __init__(self, *, text: str = "", href: str = "", editing: bool = False,
                 nostr: bool = False, text_editable: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("LinkPopover")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName(_("Edit Link") if editing else _("Add Link"))
        self._editing = editing
        self._nostr = nostr

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)
        self.text_edit = QLineEdit(text)
        self.text_edit.setPlaceholderText(_("Same as the address"))
        if not text_editable:
            self.text_edit.setEnabled(False)
            self.text_edit.setToolTip(_("Words from several paragraphs are linked as "
                                        "they are."))
        self.address_edit = QLineEdit(href)
        self.address_edit.setPlaceholderText("https://example.com")
        self.address_edit.setClearButtonEnabled(True)
        form.addRow(_("Text:"), self.text_edit)
        form.addRow(_("Address:"), self.address_edit)
        outer.addLayout(form)

        # Why the address cannot be a link, right under it.
        self.problem = QLabel("")
        self.problem.setObjectName("LinkProblem")
        self.problem.setWordWrap(True)
        self.problem.setVisible(False)
        outer.addWidget(self.problem)

        # Remove Link at the leading edge, apart; Cancel, then the default
        # button at the trailing edge, where Return lands.
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.remove_button: Optional[QPushButton] = None
        if editing:
            self.remove_button = QPushButton(_("Remove Link"))
            self.remove_button.setObjectName("destructive")
            self.remove_button.setAutoDefault(False)
            self.remove_button.clicked.connect(self._remove)
            buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.close)
        buttons.addWidget(self.cancel_button)
        self.add_button = QPushButton(_("Update Link") if editing else _("Add Link"))
        self.add_button.setAutoDefault(False)
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self._apply)
        buttons.addWidget(self.add_button)
        outer.addLayout(buttons)

        self.address_edit.textChanged.connect(self._check)
        self.address_edit.returnPressed.connect(self._apply)
        self.text_edit.returnPressed.connect(self._apply)
        # At least this wide; wider when longer words need it (German).
        self.setMinimumWidth(self.WIDTH)
        self._check()
        self._apply_colors()

    # -- the colors follow the app's light or dark palette ------------------------

    def _apply_colors(self) -> None:
        dark = theme.is_dark_active()
        background, border, field, text = (
            (DARK_BG, DARK_BORDER, DARK_MENU_BG, DARK_FG) if dark
            else (LIGHT_BG, LIGHT_BORDER, LIGHT_BG, LIGHT_FG))
        self.setStyleSheet(
            theme.dialog_stylesheet(dark)
            + f"#LinkPopover {{ background: {background}; border: 1px solid {border}; }}"
            + f"QLineEdit {{ background: {field}; color: {text}; border: 1px solid {border};"
              f" border-radius: 4px; padding: 3px 6px; }}"
            + f"#LinkProblem {{ color: {theme.error_text_color(dark)}; }}")

    # -- checking ---------------------------------------------------------------

    def address(self) -> Optional[str]:
        return normalize_link_input(self.address_edit.text(), nostr=self._nostr)[0]

    def _check(self) -> None:
        raw = self.address_edit.text()
        href, reason = normalize_link_input(raw, nostr=self._nostr)
        self.add_button.setEnabled(href is not None)
        # No complaint about an empty field: there is nothing wrong yet.
        show = bool(raw.strip()) and href is None
        if self.problem.text() != reason or self.problem.isVisible() != show:
            self.problem.setText(reason if show else "")
            self.problem.setVisible(show)
            self.adjustSize()
            # Screen readers hear the reason as it appears.
            QAccessible.updateAccessibility(
                QAccessibleEvent(self.problem, QAccessible.Event.NameChanged))

    # -- answering --------------------------------------------------------------

    def _apply(self) -> None:
        href = self.address()
        if href is None:
            self.address_edit.setFocus()
            return
        # Only words the person typed replace the linked ones.
        text = self.text_edit.text().strip() if self.text_edit.isModified() else ""
        self.applied.emit(text, href)
        self.close()

    def _remove(self) -> None:
        self.removed.emit()
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Cancel) or event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # -- placing ----------------------------------------------------------------

    def show_below(self, anchor: QRect, parent: QWidget) -> None:
        """Open under ``anchor`` (the linked words, in ``parent``'s
        coordinates), or above them when there is no room below."""
        self.adjustSize()
        below = parent.mapToGlobal(anchor.bottomLeft()) + QPoint(0, 6)
        above = parent.mapToGlobal(anchor.topLeft()) - QPoint(0, self.height() + 6)
        screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(below, self.size())
        point = below if below.y() + self.height() <= area.bottom() else above
        point.setX(max(area.left(), min(point.x(), area.right() - self.width())))
        self.move(point)
        self.show()
        self.address_edit.setFocus()
        self.address_edit.selectAll()


def clipboard_address() -> str:
    """The clipboard's text when it is a web address (Pages offers it as
    the link's address), else ""."""
    text = (QGuiApplication.clipboard().text() or "").strip()
    return text if is_bare_http_url(text) else ""
