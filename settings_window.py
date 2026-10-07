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
language, the writing font, and, while a Nostr account is in use, what
happens to a pasted image. Every command stays in its menu: the window
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

# What happens to a pasted image while a Nostr account is in use, as the
# setting stores it ("ask", "always", "never"), in the order offered.
PASTE_CHOICES = ("ask", "always", "never")


# How wide the explanations under the choices are.
_HELP_WIDTH = 320


def _paste_titles() -> Dict[str, str]:
    # The words of the alert that asks: its buttons are Upload and Keep Local.
    return {"ask": _("Ask Every Time"), "always": _("Upload"), "never": _("Keep Local")}


class SettingsWindow(QDialog):
    """App-wide settings, applied as they are chosen (see the module docstring).

    ``appearance`` maps "system", "light" and "dark" to View > Appearance's
    commands; ``languages`` lists View > Language's as ``(code, command)``;
    ``choose_language(code)`` remembers one; ``monospace`` is View > Use
    Monospace Font for Writing; ``paste_choice()`` and ``choose_paste(value)``
    read and store the pasted-image setting, offered while
    ``nostr_in_use()``.
    """

    def __init__(self, *, appearance: Dict[str, QAction],
                 languages: List[Tuple[str, QAction]],
                 choose_language: Callable[[str], None],
                 monospace: QAction,
                 paste_choice: Callable[[], str],
                 choose_paste: Callable[[str], None],
                 nostr_in_use: Callable[[], bool],
                 is_dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Settings"))
        self._languages = languages
        self._choose_language = choose_language
        self._paste_choice = paste_choice
        self._choose_paste = choose_paste
        self._nostr_in_use = nostr_in_use

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
            action.toggled.connect(lambda on, b=button: on and b.setChecked(True))
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

        # Writing: the font, as View > Use Monospace Font for Writing.
        self.monospace = QCheckBox(_("Use a monospace font for writing"))
        self.monospace.setChecked(monospace.isChecked())
        self.monospace.toggled.connect(
            lambda on: on != monospace.isChecked() and monospace.setChecked(on))
        monospace.toggled.connect(self.monospace.setChecked)
        form.addRow(self._label(_("Writing:")), self.monospace)

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addLayout(form)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # Command-W on a Mac, Ctrl+W elsewhere, as for any window.
        close = QShortcut(QKeySequence(QKeySequence.StandardKey.Close), self)
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
        """Read again what can change elsewhere: whether Nostr is in use, and
        the pasted-image setting (an alert's Remember This Choice sets it)."""
        shown = bool(self._nostr_in_use())
        self._paste_label.setVisible(shown)
        self._paste_box.setVisible(shown)
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
