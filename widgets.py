#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later


from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QFrame, QMenu, QToolButton,
)
from constants import DARK_BG
from find_replace import FindOptions
from fonts import monospace_font
from i18n import _, pgettext


class LineNumberGutter(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setFixedWidth(60)
        self.is_dark = True
        self._update_theme()

        self.editor.verticalScrollBar().valueChanged.connect(self.update)
        self.editor.textChanged.connect(self.update)
        self.editor.cursorPositionChanged.connect(self.update)

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

    def paintEvent(self, event):
        painter = QPainter(self)

        if self.is_dark:
            painter.fillRect(self.rect(), QColor("#252526"))
            text_color = QColor("#858585")
        else:
            painter.fillRect(self.rect(), QColor("#F8F8F8"))
            text_color = QColor("#666666")

        doc = self.editor.document()
        layout = doc.documentLayout()

        font = monospace_font(14)
        painter.setFont(font)
        painter.setPen(text_color)

        font_metrics = painter.fontMetrics()
        editor_padding = 8

        first_visible_pos = self.editor.cursorForPosition(self.editor.viewport().rect().topLeft())
        first_visible_block = first_visible_pos.block()
        first_block_rect = layout.blockBoundingRect(first_visible_block)

        block = first_visible_block
        block_number = block.blockNumber() + 1

        while block.isValid():
            block_rect = layout.blockBoundingRect(block)
            y_pos = (block_rect.top() - first_block_rect.top()) + editor_padding + font_metrics.ascent()

            if y_pos - font_metrics.ascent() > self.height():
                break
            if y_pos - font_metrics.ascent() < -editor_padding:
                block = block.next()
                block_number += 1
                continue

            line_text = str(block_number)
            text_width = font_metrics.horizontalAdvance(line_text)
            x_pos = self.width() - text_width - 8
            painter.drawText(x_pos, y_pos, line_text)

            block = block.next()
            block_number += 1


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
            # The button says which options are on, so a search that finds
            # nothing is not a mystery.
            self.options_button.setText(", ".join(chosen) if chosen else _("Options"))
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


class FileChangedBar(QWidget):
    """Notification bar shown at the top of a tab when the file was changed externally."""

    reload_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FileChangedBar")
        self.is_dark = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(10)

        self._icon = QLabel("⚠")
        self._icon.setFixedWidth(18)
        layout.addWidget(self._icon)

        self._text = QLabel()
        self._reload_btn = QPushButton(_("Reload"))
        self._reload_btn.setFixedHeight(26)
        self._reload_btn.clicked.connect(self.reload_requested)

        self._dismiss_btn = QPushButton("×")
        self._dismiss_btn.setFixedSize(26, 26)
        self._dismiss_btn.setToolTip(_("Dismiss"))
        self._dismiss_btn.clicked.connect(self._on_dismiss)

        layout.addWidget(self._text, 1)
        layout.addWidget(self._reload_btn)
        layout.addWidget(self._dismiss_btn)

        self._update_theme()
        self.hide()

    def show_changed(self, has_unsaved: bool):
        self._text.setText(_("File was changed externally."))
        self._reload_btn.setText(_("Discard my changes and reload") if has_unsaved
                                 else _("Reload"))
        self._reload_btn.show()
        self.show()

    def show_deleted(self):
        self._text.setText(_("File was deleted - save to recreate it."))
        self._reload_btn.hide()
        self.show()

    def show_already_open(self):
        self._text.setText(_("This file is already open in this tab."))
        self._reload_btn.hide()
        self.show()

    def show_unsupported(self, filename: str = ""):
        msg = (_("File type not supported: {names}").format(names=filename) if filename
               else _("File type not supported."))
        self._text.setText(msg)
        self._reload_btn.hide()
        self.show()

    def show_notice(self, message: str):
        """Show an arbitrary one-line notice with no reload offer."""
        self._text.setText(message)
        self._reload_btn.hide()
        self.show()

    def _on_dismiss(self):
        self.hide()
        self.dismissed.emit()

    def update_theme(self, is_dark: bool):
        self.is_dark = is_dark
        self._update_theme()

    def _update_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
                #FileChangedBar {
                    background: #3C3000;
                    border-bottom: 1px solid #5C4A00;
                }
                QLabel { color: #FFD080; background: transparent; font-size: 12px; font-weight: bold; }
                QPushButton {
                    background: #5C4A00;
                    color: #FFD080;
                    border: 1px solid #7A6200;
                    padding: 3px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #7A6200; }
            """)
        else:
            self.setStyleSheet("""
                #FileChangedBar {
                    background: #FFF8DC;
                    border-bottom: 1px solid #D4A800;
                }
                QLabel { color: #7A5000; background: transparent; font-size: 12px; font-weight: bold; }
                QPushButton {
                    background: #F0D060;
                    color: #4A3000;
                    border: 1px solid #C0A000;
                    padding: 3px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #D4B840; }
            """)


class UpdateBar(QWidget):
    """Notification bar at the top of the window about the app's version.

    Two messages, one place:

    - A newer version is available. It only announces the update.
      "Update\u2026" opens Software Update, where the person chooses; closing
      the bar means Later (skipping a version is an explicit button in that
      dialog, never a side effect of closing this).
    - MyEditor was just updated. "What\u2019s New" shows the release notes.
    """

    update_requested = Signal()
    whats_new_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateBar")
        self.is_dark = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(10)

        self._icon = QLabel("⬆")
        self._icon.setFixedWidth(18)
        layout.addWidget(self._icon)

        self._text = QLabel()
        self._update_btn = QPushButton(_("Update\u2026"))
        self._update_btn.setFixedHeight(26)
        self._update_btn.clicked.connect(self._on_button)
        self._mode = "available"

        self._dismiss_btn = QPushButton("×")
        self._dismiss_btn.setFixedSize(26, 26)
        self._dismiss_btn.setToolTip(_("Later"))
        self._dismiss_btn.setAccessibleName(_("Close"))
        self._dismiss_btn.clicked.connect(self.hide)

        layout.addWidget(self._text, 1)
        layout.addWidget(self._update_btn)
        layout.addWidget(self._dismiss_btn)

        self._update_theme()
        self.hide()

    def show_update(self, version: str):
        self._mode = "available"
        self._icon.setText("\u2b06")
        self._text.setText(_("MyEditor {version} is available.").format(version=version))
        self._update_btn.setText(_("Update\u2026"))
        self._dismiss_btn.setToolTip(_("Later"))
        self.show()

    def show_updated(self, version: str):
        self._mode = "updated"
        self._icon.setText("\u2713")
        self._text.setText(_("You\u2019re now using MyEditor {version}.").format(version=version))
        self._update_btn.setText(_("What\u2019s New"))
        self._dismiss_btn.setToolTip(_("Close"))
        self.show()

    def _on_button(self):
        if self._mode == "updated":
            self.whats_new_requested.emit()
        else:
            self.update_requested.emit()

    def update_theme(self, is_dark: bool):
        self.is_dark = is_dark
        self._update_theme()

    def _update_theme(self):
        if self.is_dark:
            self.setStyleSheet("""
                #UpdateBar {
                    background: #16324A;
                    border-bottom: 1px solid #1F4C6E;
                }
                QLabel { color: #8FD0FF; background: transparent; font-size: 12px; font-weight: bold; }
                QPushButton {
                    background: #1F4C6E;
                    color: #8FD0FF;
                    border: 1px solid #2C6690;
                    padding: 3px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #2C6690; }
            """)
        else:
            self.setStyleSheet("""
                #UpdateBar {
                    background: #E3F2FD;
                    border-bottom: 1px solid #90CAF9;
                }
                QLabel { color: #0D47A1; background: transparent; font-size: 12px; font-weight: bold; }
                QPushButton {
                    background: #BBDEFB;
                    color: #0D47A1;
                    border: 1px solid #64B5F6;
                    padding: 3px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #90CAF9; }
            """)
