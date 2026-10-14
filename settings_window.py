# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings: the few choices MyEditor keeps for the whole app.

Apple's Human Interface Guidelines give app-wide settings a window of their
own (Settings…, Command-comma on a Mac; Ctrl+comma elsewhere), so there is
one place to look for how MyEditor behaves instead of hunting through
menus. MyEditor's had no such place: the appearance and the language sat in
the View menu, and a remembered answer about pasted images could not be
changed at all once given.

The window holds only what is chosen once and kept: the appearance, the
language, the writing font, whether the format bar shows over a selection,
how the text cursor looks, and, while a Nostr account is in use, what
happens to a pasted image and where published posts open on the web.
Every command stays in its menu: the window
shows the menu commands' state and chooses through them (or through what
they call), so the two never disagree, and it holds no feature logic of its
own.

As in a Mac settings window, a choice applies at once: there is no OK,
Apply or Cancel. Escape and Command-W (Ctrl+W) close it. It is not modal.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import theme
from constants import DARK_MUTED_FG, LIGHT_MUTED_FG
from i18n import _
from nostr import web_readers

# What happens to a pasted image while a Nostr account is in use, as the
# setting stores it ("ask", "always", "never"), in the order offered.
PASTE_CHOICES = ("ask", "always", "never")


# The text cursors offered, as the setting stores them (editor.CARET_STYLES).
CARET_CHOICES = ("thin", "medium", "thick", "block")

# How wide the explanations under the choices are.
_HELP_WIDTH = 320


def _caret_titles() -> Dict[str, str]:
    return {"thin": _("Thin"), "medium": _("Medium"), "thick": _("Thick"),
            "block": _("Block")}


def _paste_titles() -> Dict[str, str]:
    # The words of the alert that asks: its buttons are Upload and Keep Local.
    return {"ask": _("Ask Every Time"), "always": _("Upload"), "never": _("Keep Local")}


class SettingsWindow(QDialog):
    """App-wide settings, applied as they are chosen (see the module docstring).

    ``appearance`` maps "system", "light" and "dark" to View > Appearance's
    commands; ``languages`` lists View > Language's as ``(code, command)``;
    ``choose_language(code)`` remembers one; ``monospace`` is View > Use
    Monospace Font for Writing; ``format_bar()`` and
    ``choose_format_bar(on)`` read and store whether the format bar shows
    over a selection; ``caret_style()`` and ``choose_caret_style(value)``
    read and store the text cursor (CARET_CHOICES); ``paste_choice()`` and
    ``choose_paste(value)`` read and store the pasted-image setting, and
    ``web_reader()`` and ``choose_web_reader(key)`` the reading app
    published posts open in (web_readers), both offered while
    ``nostr_in_use()``.
    """

    def __init__(self, *, appearance: Dict[str, QAction],
                 languages: List[Tuple[str, QAction]],
                 choose_language: Callable[[str], None],
                 monospace: QAction,
                 paste_choice: Callable[[], str],
                 choose_paste: Callable[[str], None],
                 nostr_in_use: Callable[[], bool],
                 format_bar: Callable[[], bool],
                 choose_format_bar: Callable[[bool], None],
                 caret_style: Callable[[], str] = lambda: "medium",
                 choose_caret_style: Callable[[str], None] = lambda value: None,
                 web_reader: Callable[[], str] = lambda: web_readers.DISCOVER,
                 choose_web_reader: Callable[[str], None] = lambda key: None,
                 is_dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Settings"))
        self._appearance = appearance
        self._languages = languages
        self._choose_language = choose_language
        self._paste_choice = paste_choice
        self._choose_paste = choose_paste
        self._nostr_in_use = nostr_in_use
        self._format_bar_shown = format_bar
        self._web_reader = web_reader

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        # Appearance: the menu's three commands, as radio buttons.
        self.appearance_buttons: Dict[str, QRadioButton] = {}
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        for mode, action in appearance.items():
            button = QRadioButton(action.text())
            button.setChecked(action.isChecked())
            button.toggled.connect(lambda on, a=action: on and not a.isChecked() and a.trigger())
            action.toggled.connect(self._show_appearance)
            self.appearance_buttons[mode] = button
            column.addWidget(button)
        column.addWidget(self._help(_("Automatic follows your computer’s light or dark "
                                      "appearance as it changes.")))
        form.addRow(self._label(_("Appearance:")), box)

        # Language: View > Language's choices, as a pop-up menu.
        self.language = QComboBox()
        for code, action in languages:
            self.language.addItem(action.text(), code)
            action.toggled.connect(self._show_chosen_language)
        self._show_chosen_language()
        self.language.activated.connect(self._language_activated)
        language_box = QWidget()
        language_column = QVBoxLayout(language_box)
        language_column.setContentsMargins(0, 0, 0, 0)
        language_column.setSpacing(4)
        language_column.addWidget(self.language, 0, Qt.AlignLeft)
        language_column.addWidget(self._help(
            _("MyEditor uses a new language the next time it opens.")))
        form.addRow(self._label(_("Language:"), self.language), language_box)

        # Writing: the font, as View > Use Monospace Font for Writing, and
        # the format bar over text selected with the pointer.
        self.monospace = QCheckBox(_("Use a monospace font for writing"))
        self.monospace.setChecked(monospace.isChecked())
        self.monospace.toggled.connect(
            lambda on: on != monospace.isChecked() and monospace.setChecked(on))
        monospace.toggled.connect(self.monospace.setChecked)
        self.format_bar = QCheckBox(_("Show the format bar when selecting text"))
        self.format_bar.setChecked(bool(format_bar()))
        self.format_bar.toggled.connect(choose_format_bar)
        writing_box = QWidget()
        writing_column = QVBoxLayout(writing_box)
        writing_column.setContentsMargins(0, 0, 0, 0)
        writing_column.setSpacing(4)
        writing_column.addWidget(self.monospace)
        writing_column.addWidget(self.format_bar)
        form.addRow(self._label(_("Writing:")), writing_box)

        # The text cursor: how the insertion point looks where you type.
        self.caret = QComboBox()
        caret_titles = _caret_titles()
        for value in CARET_CHOICES:
            self.caret.addItem(caret_titles[value], value)
        self.caret.setCurrentIndex(max(0, self.caret.findData(caret_style())))
        self.caret.activated.connect(
            lambda index: choose_caret_style(self.caret.itemData(index)))
        caret_box = QWidget()
        caret_column = QVBoxLayout(caret_box)
        caret_column.setContentsMargins(0, 0, 0, 0)
        caret_column.setSpacing(4)
        caret_column.addWidget(self.caret, 0, Qt.AlignLeft)
        caret_column.addWidget(self._help(
            _("How the cursor looks where you type, in every document.")))
        form.addRow(self._label(_("Text cursor:"), self.caret), caret_box)

        # Pasted images: only while Nostr is in use (nothing of Nostr before).
        self.paste = QComboBox()
        titles = _paste_titles()
        for value in PASTE_CHOICES:
            self.paste.addItem(titles[value], value)
        self.paste.activated.connect(
            lambda index: self._choose_paste(self.paste.itemData(index)))
        self._paste_box = QWidget()
        paste_column = QVBoxLayout(self._paste_box)
        paste_column.setContentsMargins(0, 0, 0, 0)
        paste_column.setSpacing(4)
        paste_column.addWidget(self.paste, 0, Qt.AlignLeft)
        paste_column.addWidget(self._help(
            _("Upload puts a pasted image on your media server right away. Keep Local "
              "leaves it in the document; publishing asks before it goes up.")))
        self._paste_label = self._label(_("Pasted images:"), self.paste)
        form.addRow(self._paste_label, self._paste_box)

        # Where published posts open on the web: only while Nostr is in use.
        self.reader = QComboBox()
        for app in web_readers.READERS:
            self.reader.addItem(app.name, app.key)
        self.reader.activated.connect(
            lambda index: choose_web_reader(self.reader.itemData(index)))
        self._reader_box = QWidget()
        reader_column = QVBoxLayout(self._reader_box)
        reader_column.setContentsMargins(0, 0, 0, 0)
        reader_column.setSpacing(4)
        reader_column.addWidget(self.reader, 0, Qt.AlignLeft)
        reader_column.addWidget(self._help(
            _("Where your published posts open on the web, and the app a copied link "
              "points to. A post the app cannot show, such as a short note in Standup "
              "Discover, opens in njump.")))
        self._reader_label = self._label(_("Open posts on the web in:"), self.reader)
        form.addRow(self._reader_label, self._reader_box)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addLayout(form)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # Command-W on a Mac, Ctrl+W (and Ctrl+F4 on Windows) elsewhere, as
        # for any window: every key the system binds to Close, not only its
        # first (a QKeySequence of a standard key holds one).
        close = QShortcut(QKeySequence.StandardKey.Close, self)
        close.activated.connect(self.close)
        self.apply_theme(is_dark)
        self.refresh()

    def _fit_help(self) -> None:
        """Give every explanation the height of its words, in the font the
        stylesheet gives it."""
        for label in self.findChildren(QLabel, "help"):
            label.ensurePolished()
            label.setFixedHeight(label.heightForWidth(_HELP_WIDTH))

    # -- building -------------------------------------------------------------

    @staticmethod
    def _label(text: str, buddy: Optional[QComboBox] = None) -> QLabel:
        """A field's label; a pop-up menu (and the list it opens) is named by it."""
        label = QLabel(text)
        if buddy is not None:
            label.setBuddy(buddy)
            name = text.rstrip(":").strip()
            buddy.setAccessibleName(name)
            buddy.view().setAccessibleName(name)
        return label

    @staticmethod
    def _help(text: str) -> QLabel:
        """A line under a choice that says what it does. A fixed width, and
        the height its words take at that width (in any language), so the
        window lays it out whole instead of guessing."""
        label = QLabel(text)
        label.setObjectName("help")
        label.setWordWrap(True)
        label.setFixedWidth(_HELP_WIDTH)
        return label

    # -- state ------------------------------------------------------------------

    def _show_appearance(self, *_args) -> None:
        for mode, action in self._appearance.items():
            if action.isChecked():
                self.appearance_buttons[mode].setChecked(True)

    def _show_chosen_language(self, *_args) -> None:
        for index, (_code, action) in enumerate(self._languages):
            if action.isChecked():
                self.language.setCurrentIndex(index)
                return

    def _language_activated(self, index: int) -> None:
        code = self.language.itemData(index)
        if code is not None:
            self._choose_language(code)

    def refresh(self) -> None:
        """Read again what can change elsewhere: whether Nostr is in use, the
        format bar's setting, and the pasted-image setting (an alert's
        Remember This Choice sets it)."""
        self.format_bar.setChecked(bool(self._format_bar_shown()))
        shown = bool(self._nostr_in_use())
        self._paste_label.setVisible(shown)
        self._paste_box.setVisible(shown)
        self._reader_label.setVisible(shown)
        self._reader_box.setVisible(shown)
        chosen = self.reader.findData(self._web_reader())
        self.reader.setCurrentIndex(chosen if chosen >= 0 else 0)
        index = self.paste.findData(self._paste_choice())
        self.paste.setCurrentIndex(index if index >= 0 else 0)

    def showEvent(self, event) -> None:  # noqa: N802, Qt's name
        self.refresh()
        super().showEvent(event)

    def apply_theme(self, is_dark: bool) -> None:
        muted = DARK_MUTED_FG if is_dark else LIGHT_MUTED_FG
        self.setStyleSheet(theme.dialog_stylesheet(is_dark)
                           + f"QLabel#help {{ color: {muted}; font-size: 11px; }}")
        self._fit_help()
