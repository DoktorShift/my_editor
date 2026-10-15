# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The alerts that ask for a document's password (locked_files.py).

The app's alert (alerts.py) with secure fields above its buttons, as Pages
asks for a document's password:

- Set Password: the password twice, and plainly that a forgotten one can't
  be recovered; the button acts once both are typed alike.
- Open: the locked document's password. A wrong one is said under the
  field, which is emptied, and the alert stays, as a Mac's password
  prompts do.
- Change Password: the old password, then the new one twice; Remove
  Password needs only the old one.

The password is checked as a button is chosen (``check``), so the work it
takes (scrypt, a moment on purpose) is done once: what the check gives back
(the opened document) is the alert's ``result``.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QLineEdit, QWidget

import theme
from alerts import CANCEL, DEFAULT, NORMAL, Alert, Button
from i18n import _

# What each alert's buttons answer.
OPEN, SET, CHANGE, REMOVE, CANCELLED = "open", "set", "change", "remove", "cancel"

_CURRENT = ("password", "old")      # the fields that hold the password a document has


class PasswordAlert(Alert):
    """An alert with password fields. ``fields`` are their labels by name,
    in order; ``needs`` the fields each button's answer needs typed in (a
    new password and "verify" alike as well); ``check(password)`` opens
    with the password the document has, None when it is wrong."""

    def __init__(self, parent, *, title: str, message: str, buttons: Sequence[Button],
                 fields: Dict[str, str], needs: Dict[str, Tuple[str, ...]],
                 check: Optional[Callable[[str], object]] = None, caution: bool = False):
        super().__init__(parent, title=title, message=message, buttons=buttons,
                         caution=caution)
        self._needs = needs
        self._check = check
        self._by_value = {spec.value: self.buttons[spec.label] for spec in buttons}
        self.result = None

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.fields: Dict[str, QLineEdit] = {}
        for name, label in fields.items():
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setAccessibleName(label.rstrip(":"))
            field.textChanged.connect(self._update)
            form.addRow(label, field)
            self.fields[name] = field
        box = QWidget(self)
        box.setLayout(form)
        self.problem = QLabel(self)
        self.problem.setObjectName("alert_problem")
        self.problem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.problem.setWordWrap(True)
        dark = theme.is_dark_active()
        self.setStyleSheet(self.styleSheet()
                           + f"#alert_problem {{ color: {theme.error_text_color(dark)}; }}")
        # Over the buttons, below the message (before the room above them).
        layout = self.layout()
        below = layout.indexOf(self.button_row) - 1
        layout.insertWidget(below, self.problem)
        layout.insertWidget(below, box)
        self._follow_in_tab_order(list(self.fields.values()) + self.ordered_buttons)
        first = next(iter(self.fields.values()))
        first.setFocus()
        self._update()

    def text(self, name: str) -> str:
        return self.fields[name].text()

    def button_for(self, value: str):
        return self._by_value[value]

    def _update(self) -> None:
        """Each button acts once what it needs is typed in; two new
        passwords that differ are said."""
        new = self.fields.get("new", self.fields["password"] if "password" in self.fields
                              else None)
        verify = self.fields.get("verify")
        differ = bool(new and verify and verify.text() and new.text() != verify.text())
        self.problem.setText(_("The passwords don’t match.") if differ else "")
        for value, names in self._needs.items():
            ready = all(self.text(name) for name in names)
            if "verify" in names:
                ready = ready and not differ
            self._by_value[value].setEnabled(ready)

    def _choose(self, value) -> None:
        current = next((name for name in _CURRENT if name in self._needs.get(value, ())), None)
        if current is not None and self._check is not None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.result = self._check(self.text(current))
            finally:
                QApplication.restoreOverrideCursor()
            if not self.result:
                field = self.fields[current]
                field.blockSignals(True)          # the message stays until typing again
                field.clear()
                field.blockSignals(False)
                self._update()
                self.problem.setText(_("The password is incorrect."))
                field.setFocus()
                return
        super()._choose(value)


def _cancel() -> Button:
    return Button(_("Cancel"), CANCELLED, CANCEL)


def open_alert(parent, *, name: str, check: Callable[[str], object],
               message: str = "") -> PasswordAlert:
    """Asks for a locked document's password; ``result`` is what ``check``
    opened. ``message`` says what for, when not to open it."""
    return PasswordAlert(
        parent, title=_("“{name}” is locked").format(name=name),
        message=message or _("Enter its password to open it."),
        buttons=(_cancel(), Button(_("Open"), OPEN, DEFAULT)),
        fields={"password": _("Password:")}, needs={OPEN: ("password",)}, check=check)


def set_password_alert(parent, *, name: str) -> PasswordAlert:
    """Asks for a new password, twice, for a document that has none."""
    return PasswordAlert(
        parent, title=_("Set a password for “{name}”").format(name=name),
        message=_("If you forget the password, the document can’t be opened, by MyEditor or "
                  "anyone else. There is no way to recover it."),
        buttons=(_cancel(), Button(_("Set Password"), SET, DEFAULT)),
        fields={"password": _("Password:"), "verify": _("Verify:")},
        needs={SET: ("password", "verify")}, caution=True)


def change_alert(parent, *, name: str, check: Callable[[str], object]) -> PasswordAlert:
    """Asks for the old password, and a new one twice; Remove Password
    takes the password off."""
    return PasswordAlert(
        parent, title=_("Change the password for “{name}”").format(name=name),
        message=_("If you forget the new password, the document can’t be opened."),
        buttons=(Button(_("Remove Password"), REMOVE, NORMAL), _cancel(),
                 Button(_("Change Password"), CHANGE, DEFAULT)),
        fields={"old": _("Old password:"), "new": _("New password:"), "verify": _("Verify:")},
        needs={CHANGE: ("old", "new", "verify"), REMOVE: ("old",)}, check=check)
