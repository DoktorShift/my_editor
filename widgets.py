#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later


import math

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QFrame, QMenu, QToolButton,
    QSizePolicy,
)
from constants import DARK_BG
from find_replace import FindOptions
from fonts import monospace_font
from i18n import _, pgettext


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "\u2026" when it has
    less room than the text needs, instead of making the window wider
    (a status bar message in a long language). Screen readers and the
    tooltip get the whole text.

    The text is measured in fractions of a pixel, as Qt shortens it: the
    whole number of pixels can be a little less, and a label given the
    room it asked for then shortened a text that fits."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802 (Qt's name)
        self._full = text or ""
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self._elide()

    def sizeHint(self) -> QSize:  # noqa: N802
        margins = self.contentsMargins()
        width = (math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self._full))
                 + margins.left() + margins.right() + 2)
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance("\u2026") * 4,
                     super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        margins = self.contentsMargins()
        room = max(0, self.width() - margins.left() - margins.right() - 2)
        metrics = QFontMetricsF(self.font())
        if metrics.horizontalAdvance(self._full) <= room:
            super().setText(self._full)
        else:
            super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room))


class LineNumberGutter(QWidget):
    """Each paragraph's number beside it, on the baseline of its first line.

    Where a line is drawn is asked of the editor, never worked out here:
    its document lays the line out (the document's margin, the paragraph's
    own spacing, the font of its text), the scroll bar moves it, and the
    viewport sits below any room kept above the text (a header) and beside
    the paper's margins. The numbers follow all of them."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setFixedWidth(60)
        self.is_dark = True
        self._update_theme()

        self.editor.verticalScrollBar().valueChanged.connect(self.update)
        self.editor.textChanged.connect(self.update)
        self.editor.cursorPositionChanged.connect(self.update)
        # A line moves without an edit as well: a new font or text size, a
        # header's room above the text, the layout of a long file going on.
        self.editor.document().documentLayout().update.connect(self.update)

    def _update_theme(self):
        if hasattr(self.editor, '_theme_colors'):
            is_dark = self.editor._theme_colors['bg'] == DARK_BG
            self.is_dark = is_dark
            if is_dark:
                self.setStyleSheet("""
                    QWidget {
                        background: #252526;
                        color: #858585;
                        border-right: 1px solid #3C3C3C;
                    }
                """)
            else:
                self.setStyleSheet("""
                    QWidget {
                        background: #F8F8F8;
                        color: #666666;
                        border-right: 1px solid #E1E1E1;
                    }
                """)

    def numbers(self):
        """(number, baseline) of each paragraph on screen, the baseline in
        this gutter's pixels: where the editor draws the first line of it."""
        editor = self.editor
        # The document's top, in this gutter's pixels.
        origin = (self.mapFromGlobal(editor.viewport().mapToGlobal(QPoint(0, 0))).y()
                  - editor.verticalScrollBar().value())
        found = []
        block = editor.first_visible_block()
        while block.isValid():
            layout = block.layout()
            if block.isVisible() and layout.lineCount():
                line = layout.lineAt(0)
                top = origin + layout.position().y() + line.y()
                if top > self.height():
                    break
                if top + line.height() >= 0:
                    found.append((block.blockNumber() + 1, round(top + line.ascent())))
            block = block.next()
        return found

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.is_dark:
            painter.fillRect(self.rect(), QColor("#252526"))
            painter.setPen(QColor("#858585"))
        else:
            painter.fillRect(self.rect(), QColor("#F8F8F8"))
            painter.setPen(QColor("#666666"))
        painter.setFont(monospace_font(14))
        metrics = painter.fontMetrics()
        for number, baseline in self.numbers():
            text = str(number)
            painter.drawText(self.width() - metrics.horizontalAdvance(text) - 8, baseline, text)


class FindBar(QFrame):
    """Find (and, in the editor, Replace) above the document.

    The field to find in, previous and next match, how many there are,
    and Done, the way Safari and TextEdit lay out their find bar. With
    ``replace=True`` a second row can be shown (Find and Replace…):
    the replacement, Replace and Replace All. With ``options=True`` a
    menu offers Match Case and Whole Words. The bar only asks: the window
    connects ``options_changed``, ``replace_requested`` and
    ``replace_all_requested`` and does the finding (find_replace.py).

    Keys: Return finds the next match, Shift+Return the previous one,
    Escape closes the bar; Return in the replacement field replaces.
    """

    options_changed = Signal()
    replace_requested = Signal()
    replace_all_requested = Signal()

    def __init__(self, on_find_next, on_find_prev, on_close, parent=None, *,
                 replace: bool = False, options: bool = False):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("FindBar")
        self.is_dark = True

        grid = QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(pgettext("find field", "Find"))
        self.edit.setAccessibleName(pgettext("find field", "Find"))
        self.edit.setClearButtonEnabled(True)
        self.btn_prev = QPushButton("\u276e")
        self.btn_prev.setToolTip(_("Previous Match"))
        self.btn_prev.setAccessibleName(_("Previous Match"))
        self.btn_next = QPushButton("\u276f")
        self.btn_next.setToolTip(_("Next Match"))
        self.btn_next.setAccessibleName(_("Next Match"))
        for button in (self.btn_prev, self.btn_next):
            button.setObjectName("FindArrow")
            button.setAutoDefault(False)
        self.match_info = QLabel("")
        self.match_info.setMinimumWidth(90)
        self.btn_close = QPushButton(_("Done"))
        self.btn_close.setAutoDefault(False)
        self.btn_close.setToolTip(_("Close the find bar (Esc)"))

        self.btn_prev.clicked.connect(on_find_prev)
        self.btn_next.clicked.connect(on_find_next)
        self.btn_close.clicked.connect(on_close)
        self.edit.installEventFilter(self)

        arrows = QHBoxLayout()
        arrows.setSpacing(2)
        arrows.addWidget(self.btn_prev)
        arrows.addWidget(self.btn_next)
        grid.addWidget(self.edit, 0, 0)
        grid.addLayout(arrows, 0, 1)
        grid.addWidget(self.match_info, 0, 2)

        self.match_case = None
        self.whole_words = None
        self.options_button = None
        if options:
            self.options_button = QToolButton()
            self.options_button.setText(_("Options"))
            self.options_button.setAccessibleName(_("Find Options"))
            self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(self.options_button)
            self.match_case = menu.addAction(_("Match Case"))
            self.whole_words = menu.addAction(_("Whole Words"))
            for action in (self.match_case, self.whole_words):
                action.setCheckable(True)
                action.toggled.connect(lambda _on=False: self._options_toggled())
            self.options_button.setMenu(menu)
            grid.addWidget(self.options_button, 0, 3)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(4, 0)
        grid.addWidget(self.btn_close, 0, 5)

        self.replace_edit = None
        self.replace_btn = None
        self.replace_all_btn = None
        if replace:
            self.replace_edit = QLineEdit()
            self.replace_edit.setPlaceholderText(_("Replace With"))
            self.replace_edit.setAccessibleName(_("Replace With"))
            self.replace_edit.installEventFilter(self)
            self.replace_btn = QPushButton(_("Replace"))
            self.replace_all_btn = QPushButton(_("Replace All"))
            for button in (self.replace_btn, self.replace_all_btn):
                button.setAutoDefault(False)
            self.replace_btn.clicked.connect(self.replace_requested)
            self.replace_all_btn.clicked.connect(self.replace_all_requested)
            buttons = QHBoxLayout()
            buttons.setSpacing(6)
            buttons.addWidget(self.replace_btn)
            buttons.addWidget(self.replace_all_btn)
            buttons.addStretch(1)
            grid.addWidget(self.replace_edit, 1, 0)
            grid.addLayout(buttons, 1, 1, 1, 5)
            self.show_replace(False)
            # Tab goes from what to find to what to put instead, as in
            # every find and replace panel.
            QWidget.setTabOrder(self.edit, self.replace_edit)
            QWidget.setTabOrder(self.replace_edit, self.replace_btn)
            QWidget.setTabOrder(self.replace_btn, self.replace_all_btn)

        self._update_theme()

    # -- what the window asks ---------------------------------------------------

    def options(self) -> FindOptions:
        """Match Case and Whole Words, as chosen."""
        return FindOptions(
            match_case=bool(self.match_case and self.match_case.isChecked()),
            whole_words=bool(self.whole_words and self.whole_words.isChecked()))

    def _options_toggled(self) -> None:
        if self.options_button is not None:
            chosen = [a.text() for a in (self.match_case, self.whole_words) if a.isChecked()]
            # The button says that options are on, so a search that finds
            # nothing is not a mystery; which ones, its tooltip and its
            # description say (the names would widen the bar past the
            # window: "Groß-/Kleinschreibung beachten, Nur ganze Wörter").
            self.options_button.setText(
                _("Options ({count})").format(count=len(chosen)) if chosen else _("Options"))
            names = ", ".join(chosen)
            self.options_button.setToolTip(names)
            self.options_button.setAccessibleDescription(names)
        self.options_changed.emit()

    def show_replace(self, shown: bool) -> None:
        """Show or hide the Replace row (Find and Replace…, or Find…)."""
        if self.replace_edit is None:
            return
        for widget in (self.replace_edit, self.replace_btn, self.replace_all_btn):
            widget.setVisible(shown)

    def replace_shown(self) -> bool:
        return self.replace_edit is not None and not self.replace_edit.isHidden()

    def replacement(self) -> str:
        return self.replace_edit.text() if self.replace_edit is not None else ""

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress and obj in (self.edit, self.replace_edit):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if obj is self.replace_edit:
                    self.replace_requested.emit()
                elif event.modifiers() & Qt.ShiftModifier:
                    self.btn_prev.clicked.emit()
                else:
                    self.btn_next.clicked.emit()
                return True
            if event.key() == Qt.Key_Escape:
                self.btn_close.clicked.emit()
                return True
        return super().eventFilter(obj, event)

    def set_match_info(self, text):
        self.match_info.setText(text)

    def text(self):
        return self.edit.text()

    def _update_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
            #FindBar {
                background: #252526;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
            }
            QLineEdit {
                background: #1E1E1E;
                color: #D4D4D4;
                border: 1px solid #3C3C3C;
                padding: 5px 8px;
                border-radius: 4px;
                selection-background-color: #264F78;
            }
            QPushButton, QToolButton {
                background: #2D2D30;
                color: #D4D4D4;
                border: 1px solid #3C3C3C;
                padding: 5px 10px;
                border-radius: 4px;
            }
            QPushButton:hover, QToolButton:hover { background: #3C3C3C; }
            QPushButton:disabled { color: #6A6A6A; }
            #FindArrow { padding: 5px 9px; }
            QToolButton::menu-indicator { image: none; width: 0; }
            QLabel { color: #CCCCCC; }
            """)
        else:
            self.setStyleSheet("""
            #FindBar {
                background: #F8F8F8;
                border: 1px solid #E1E1E1;
                border-radius: 6px;
            }
            QLineEdit {
                background: #FFFFFF;
                color: #333333;
                border: 1px solid #D0D0D0;
                padding: 5px 8px;
                border-radius: 4px;
                selection-background-color: #0078D4;
            }
            QPushButton, QToolButton {
                background: #F3F3F3;
                color: #333333;
                border: 1px solid #D0D0D0;
                padding: 5px 10px;
                border-radius: 4px;
            }
            QPushButton:hover, QToolButton:hover { background: #E1E1E1; }
            QPushButton:disabled { color: #A0A0A0; }
            #FindArrow { padding: 5px 9px; }
            QToolButton::menu-indicator { image: none; width: 0; }
            QLabel { color: #666666; }
            """)

    def focusIn(self):
        self.edit.setFocus()
        self.edit.selectAll()


class UpdateButton(QToolButton):
    """The app's version, said at the end of the toolbar, as a Mac app
    outside the App Store shows a reminder inside its window (Sparkle's
    gentle reminders; Apple: no alert at startup):

    - "Update Available" while a newer version is out: it opens Software
      Update, where Later and Skip This Version are.
    - "What\u2019s New" after an update, for that session: it shows the
      release notes.

    Hidden otherwise. A tinted capsule in the accent, never over the text.
    """

    update_requested = Signal()
    whats_new_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode = "available"
        self.setAccessibleName(_("Update Available"))
        self.clicked.connect(self._on_click)
        self.update_theme(True)
        self.hide()

    def show_update(self, version: str):
        self._mode = "available"
        self.setText(_("Update Available"))
        self.setToolTip(_("MyEditor {version} is available.").format(version=version))
        self.setAccessibleName(self.toolTip())
        self.show()

    def show_updated(self, version: str):
        self._mode = "updated"
        self.setText(_("What\u2019s New"))
        self.setToolTip(_("You\u2019re now using MyEditor {version}.").format(version=version))
        self.setAccessibleName(self.toolTip())
        self.show()

    def _on_click(self):
        if self._mode == "updated":
            self.hide()                    # read once: the reminder is done
            self.whats_new_requested.emit()
        else:
            self.update_requested.emit()

    def update_theme(self, is_dark: bool):
        ink, fill, hover = (("#5EB1FF", "rgba(10, 132, 255, 0.22)", "rgba(10, 132, 255, 0.32)")
                            if is_dark else
                            ("#0A6CDB", "rgba(10, 132, 255, 0.12)", "rgba(10, 132, 255, 0.20)"))
        self.setStyleSheet(f"""
            #UpdateButton {{
                color: {ink}; background: {fill}; border: 1px solid transparent;
                border-radius: 11px; padding: 2px 10px; font-size: 12px; font-weight: 600;
            }}
            #UpdateButton:hover {{ background: {hover}; }}
            #UpdateButton:focus {{ border-color: {ink}; }}
        """)
