# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draft Relays: where the account keeps its drafts.

A sheet on the window (window-modal: on macOS it slides down from the
title bar), opened from the Drafts panel's action menu:

    Draft Relays
    Drafts are encrypted wherever they are kept. With relays chosen
    here, drafts are saved only there. Apps that don't read this
    setting won't see the drafts saved there.

    Drafts are saved only on these relays:
    ┌──────────────────────────────────────┐
    │ wss://relay.example                  │
    └──────────────────────────────────────┘
                                   [Remove]
    Add a relay: [ wss://            ] [Add]

    [Use My Usual Relays]     [Cancel] [Save]

It reads the account's list first (nostr/draft_relays.py), and edits
only what it has just read. Adding checks the address (``wss://``, or
``ws://`` on this computer). Use My Usual Relays empties the list, the
way Restore Defaults resets a settings sheet; nothing changes before
Save. Save publishes the encrypted list (or an empty one) and then
moves the drafts' routing (PrivateDraftRelays.save); while that waits
for the signer the sheet says so, and it can't be cancelled halfway.

Keys: Return in the address field adds it, elsewhere it saves; Delete or
Backspace in the list removes the selected relay; Escape cancels.

Signals:
  saved(list)   the relays drafts now go to ([] for the usual relays)
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QWidget,
)

from i18n import _, ngettext

from ..draft_relays import PrivateDraftRelays, draft_relay_address
from ..outbox import defaults
from ..outbox.writer import FAILED, UNKNOWN_BASE, WriteOutcome
from .assistant import DEFAULT, LEADING, NORMAL, AssistantWindow, busy_bar, page, text_label

MAIN = "main"

# What the sheet is doing.
READING = "reading"
READ_FAILED = "read_failed"
EDITING = "editing"
SAVING = "saving"


class DraftRelaysSheet(AssistantWindow):
    """Choose the relays drafts are kept on (see the module's text)."""

    saved = Signal(list)

    def __init__(self, relays: PrivateDraftRelays, *, is_dark: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(_("Draft Relays"), is_dark=is_dark, min_width=480, parent=parent)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._relays = relays
        self._read: List[str] = []        # the account's list, as last read
        self._state = READING
        self._generation = 0
        self._shown_buttons: list = []
        self._build()
        self._read_list()

    # -- what is shown -------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def chosen(self) -> List[str]:
        """The relays in the list now."""
        return [self._list.item(row).text() for row in range(self._list.count())]

    def _build(self) -> None:
        widget, column = page(10)
        column.addWidget(text_label(_("Draft Relays"), "title"))
        column.addWidget(text_label(
            _("Drafts are encrypted wherever they are kept. With relays chosen here, drafts "
              "are saved only there. Apps that don’t read this setting won’t see the drafts "
              "saved there."), "muted"))

        self._summary = text_label("", "item_title")
        column.addWidget(self._summary)
        self._list = QListWidget()
        self._list.setAccessibleName(_("Draft relays"))
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(self._list.fontMetrics().height() * 5 + 12)
        self._list.itemSelectionChanged.connect(self._update)
        self._list.installEventFilter(self)
        column.addWidget(self._list)

        under_list = QHBoxLayout()
        under_list.addStretch(1)
        self._remove = QPushButton(_("Remove"))
        self._remove.setAutoDefault(False)
        self._remove.clicked.connect(self._remove_selected)
        under_list.addWidget(self._remove)
        column.addLayout(under_list)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        label = QLabel(_("Add a relay:"))
        self._field = QLineEdit()
        self._field.setPlaceholderText("wss://")
        label.setBuddy(self._field)
        self._field.textChanged.connect(self._on_typing)
        self._field.installEventFilter(self)
        self._add = QPushButton(_("Add"))
        self._add.setAutoDefault(False)
        self._add.clicked.connect(self._add_typed)
        add_row.addWidget(label)
        add_row.addWidget(self._field, 1)
        add_row.addWidget(self._add)
        column.addLayout(add_row)

        self._error = text_label("", "error")
        self._error.hide()
        column.addWidget(self._error)

        self._busy = QWidget()
        busy_row = QHBoxLayout(self._busy)
        busy_row.setContentsMargins(0, 0, 0, 0)
        busy_row.setSpacing(10)
        busy_row.addWidget(busy_bar(120))
        self._busy_label = text_label("", "muted")
        busy_row.addWidget(self._busy_label, 1)
        self._busy.hide()
        column.addWidget(self._busy)
        column.addStretch(1)
        self.add_page(MAIN, widget)
        self.show_page(MAIN, [])

    # -- reading ---------------------------------------------------------------

    def _read_list(self) -> None:
        self._generation += 1
        generation = self._generation
        self._set_state(READING, busy=_("Reading where your drafts are kept…"))
        self._relays.read(lambda relays: self._on_read(generation, relays))

    def _on_read(self, generation: int, relays: Optional[List[str]]) -> None:
        if generation != self._generation:
            return
        if relays is None:
            self._set_state(READ_FAILED, error=_(
                "Couldn’t read where your drafts are kept. Check your internet connection, "
                "then try again."))
            return
        self._read = list(relays)
        self._list.clear()
        self._list.addItems(self._read)
        self._set_state(EDITING)

    # -- editing ---------------------------------------------------------------

    def _on_typing(self, _text: str) -> None:
        if self._state == EDITING:
            self._error.hide()
        self._update()

    def _add_typed(self) -> None:
        if self._state != EDITING or not self._field.text().strip():
            return
        address = draft_relay_address(self._field.text())
        if address is None:
            self._say(_("Enter a relay’s address. It starts with wss://."))
            return
        if address in self.chosen():
            self._say(_("That relay is already in the list."))
            return
        if self._list.count() >= defaults.PRIVATE_CAP:
            self._say(ngettext("Drafts can be kept on at most {count} relay.",
                               "Drafts can be kept on at most {count} relays.",
                               defaults.PRIVATE_CAP).format(count=defaults.PRIVATE_CAP))
            return
        self._list.addItem(address)
        self._list.setCurrentRow(self._list.count() - 1)
        self._field.clear()
        self._update()

    def _remove_selected(self) -> None:
        row = self._list.currentRow()
        if self._state != EDITING or row < 0 or not self._list.item(row).isSelected():
            return
        self._list.takeItem(row)
        if self._list.count():
            self._list.setCurrentRow(min(row, self._list.count() - 1))
        self._update()

    def _use_usual_relays(self) -> None:
        """Like Restore Defaults: the form changes, Save makes it so."""
        self._list.clear()
        self._update()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if (watched is self._field and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and self._field.text().strip()):
                self._add_typed()           # Return adds what was typed, not Save
                return True
            if watched is self._list and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._remove_selected()
                return True
        return super().eventFilter(watched, event)

    # -- saving ----------------------------------------------------------------

    def _save(self) -> None:
        if self._state != EDITING or not self._changed():
            return
        chosen = self.chosen()
        self._generation += 1
        generation = self._generation
        self._set_state(SAVING, busy=_("Saving. Approve the request on your signer if it "
                                       "asks…"))
        self._relays.save(chosen, lambda outcome: self._on_saved(generation, chosen, outcome))

    def _on_saved(self, generation: int, chosen: List[str], outcome: WriteOutcome) -> None:
        if generation != self._generation:
            return
        if outcome.ok:
            self._state = EDITING
            self.saved.emit(list(chosen))
            self.accept()
            return
        if outcome.status == UNKNOWN_BASE:
            message = _("Couldn’t read the current setting, so nothing was changed. Check "
                        "your internet connection, then try again.")
        elif outcome.status == FAILED and outcome.event is not None:
            message = _("Too few relays took the change. Check your internet connection, "
                        "then try again.")
        else:
            message = _("Your signer didn’t approve the change. Try again, and approve it "
                        "on your signer.")
        self._set_state(EDITING, error=message)

    def _changed(self) -> bool:
        return self.chosen() != self._read

    # -- state and buttons -------------------------------------------------------

    def _set_state(self, state: str, *, busy: str = "", error: str = "") -> None:
        self._state = state
        self._busy_label.setText(busy)
        self._busy.setVisible(bool(busy))
        if error:
            self._say(error)
        else:
            self._error.hide()
        self._update()

    def _say(self, text: str) -> None:
        self._error.setText(text)
        self._error.show()
        # Read with the field it is about, by a screen reader too.
        self._field.setAccessibleDescription(text)

    def _update(self) -> None:
        editing = self._state == EDITING
        # Where drafts go is said once it is known, not while reading.
        known = self._state in (EDITING, SAVING)
        self._summary.setText(_("Drafts are saved only on these relays:") if self._list.count()
                              else _("Drafts are kept on your usual relays."))
        self._summary.setVisible(known)
        self._list.setEnabled(editing)
        self._field.setEnabled(editing)
        self._add.setEnabled(editing and bool(self._field.text().strip()))
        selected = self._list.currentItem()
        self._remove.setEnabled(editing and selected is not None and selected.isSelected())
        if not self._error.isVisible():
            self._field.setAccessibleDescription("")
        self._update_buttons()

    def _buttons(self) -> list:
        """``(key, label, placement, handler, enabled)`` for the state."""
        if self._state == READ_FAILED:
            return [("cancel", _("Cancel"), NORMAL, self.reject, True),
                    ("retry", _("Try Again"), DEFAULT, self._read_list, True)]
        editing = self._state == EDITING
        return [("usual", _("Use My Usual Relays"), LEADING, self._use_usual_relays,
                 editing and self._list.count() > 0),
                ("cancel", _("Cancel"), NORMAL, self.reject, self._state != SAVING),
                ("save", _("Save"), DEFAULT, self._save, editing and self._changed())]

    def _update_buttons(self) -> None:
        """Lay the buttons out again only when they change (a new layout
        would take the focus from the one in use), and keep their enabled
        state current."""
        specs = self._buttons()
        shape = [spec[:3] for spec in specs]
        if shape != self._shown_buttons:
            self._shown_buttons = shape
            self.set_buttons([spec[:4] for spec in specs])
        for key, _label, _placement, _handler, enabled in specs:
            self.buttons[key].setEnabled(enabled)

    # -- closing -----------------------------------------------------------------

    def reject(self) -> None:
        # A change on its way to the signer and the relays can't be called
        # back: the sheet stays until it is answered, and says so.
        if self._state == SAVING:
            return
        super().reject()

    def done(self, result: int) -> None:
        self._generation += 1           # an answer still on its way is dropped
        super().done(result)
