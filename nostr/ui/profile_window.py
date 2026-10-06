# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edit Profile: the name, picture and addresses people see for an account.

Three pages, in the order they happen:

    reading   the profile is read fresh from the network
    form      the fields, with any problem shown under its field
    saving    the change is signed and published

A profile is one replaceable event: what is published last is the whole
profile, in every app. So the window never edits from a remembered copy.
It reads the profile first, and only a profile that was found (or one the
network clearly does not have) can be edited; when it cannot be read, the
window says so and offers to try again rather than risk replacing it.
Saving sends only the fields that were changed, and the writer
(nostr/outbox/writer.py, update_profile) reads the profile once more and
keeps every field this window does not show (banner colors, bots' flags,
anything another app added).

The window does no network work itself: ``read`` and ``save`` are handed
in (nostr/profile_editing.py has the real ones), which is also what lets
the tests drive it.
"""

from __future__ import annotations

import json
import re
import weakref
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from i18n import N_, _
from nostr.outbox.policy import LookupState
from nostr.outbox.writer import UNCHANGED, UNKNOWN_BASE, WRITTEN
from nostr.ui.assistant import (
    DEFAULT, NORMAL, AssistantWindow, busy_bar, page, text_label,
)

READING = "reading"
FORM = "form"
SAVING = "saving"
PROBLEM = "problem"

# read(on_done(Lookup)) and save(changes, on_done(WriteOutcome)).
ReadFn = Callable[[Callable[[object], None]], None]
SaveFn = Callable[[Dict[str, str], Callable[[object], None]], None]


@dataclass(frozen=True)
class Field:
    key: str                # the profile's own name for it (NIP-01, NIP-24, NIP-57)
    label: str
    placeholder: str = ""
    kind: str = "text"      # text, about, link, image, lightning, address


# In the order people think of them: who they are, how they look, where
# else to find them. The labels are marked here and translated where shown.
FIELDS = (
    Field("display_name", N_("Name"), N_("The name people see")),
    Field("name", N_("Username"), N_("A short name, without spaces")),
    Field("about", N_("About"), kind="about"),
    Field("picture", N_("Picture"), N_("Address of an image, https://…"), kind="image"),
    Field("banner", N_("Banner"), N_("Address of a wide image, https://…"), kind="image"),
    Field("website", N_("Website"), "https://…", kind="link"),
    Field("lud16", N_("Lightning Address"), N_("you@wallet.example"), kind="lightning"),
    Field("nip05", N_("Nostr Address"), N_("you@example.com"), kind="address"),
)

# Older apps wrote the display name under this key; it is shown when the
# newer one is missing.
_LEGACY_DISPLAY_NAME = "displayName"

_ADDRESS = re.compile(r"\A(?:[A-Za-z0-9._+-]+@)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")
_LIGHTNING = re.compile(r"\A[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")
_WEB = re.compile(r"\Ahttps?://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?\Z", re.IGNORECASE)
_SECURE_WEB = re.compile(r"\Ahttps://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?\Z", re.IGNORECASE)


def field_problem(field: Field, value: str) -> Optional[str]:
    """What is wrong with ``value`` for ``field``, in plain words, or None.
    An empty field is never a problem: it removes that part of the profile."""
    value = value.strip()
    if not value:
        return None
    if field.kind == "image" and not _SECURE_WEB.match(value):
        return _("Enter an image address beginning with https, like "
                 "https://example.com/picture.jpg.")
    if field.kind == "link" and not _WEB.match(value):
        return _("Enter a web address beginning with http or https, like "
                 "https://example.com.")
    if field.kind == "lightning" and not _LIGHTNING.match(value):
        return _("Enter a Lightning address like you@wallet.example.")
    if field.kind == "address" and not _ADDRESS.match(value):
        return _("Enter a Nostr address like you@example.com.")
    if field.key == "name" and any(c.isspace() for c in value):
        return _("A username can’t contain spaces.")
    return None


def profile_fields(event: Optional[dict]) -> Dict[str, str]:
    """The window's fields from a profile event; raises ValueError for a
    profile that is not a JSON object (it is never guessed at)."""
    if event is None:
        return {f.key: "" for f in FIELDS}
    data = json.loads(event.get("content") or "{}")
    if not isinstance(data, dict):
        raise ValueError("the profile is not a JSON object")
    values = {}
    for field in FIELDS:
        value = data.get(field.key)
        if field.key == "display_name" and not value:
            value = data.get(_LEGACY_DISPLAY_NAME)
        values[field.key] = value.strip() if isinstance(value, str) else ""
    return values


class ProfileWindow(AssistantWindow):
    """Edit Profile, for the active account."""

    saved = Signal()        # the changed profile reached the relays

    def __init__(self, *, read: ReadFn, save: SaveFn, signs_locally: bool = False,
                 is_dark: bool = True, parent=None) -> None:
        super().__init__(_("Edit Profile"), is_dark=is_dark, parent=parent)
        self._read = read
        self._save = save
        self._signs_locally = signs_locally
        self._loaded: Dict[str, str] = {}
        self._new_profile = False
        self._closed = False
        self._edits: Dict[str, QWidget] = {}
        self._errors: Dict[str, QLabel] = {}
        self._build_reading()
        self._build_form()
        self._build_saving()
        self._build_problem()
        self.start_reading()

    # -- reading -------------------------------------------------------------

    def _build_reading(self) -> None:
        body, col = page()
        col.addStretch(1)
        self._reading_label = text_label(_("Reading your profile…"), "item_title")
        col.addWidget(self._reading_label)
        col.addWidget(busy_bar())
        col.addStretch(2)
        self.add_page(READING, body)

    def start_reading(self) -> None:
        self.show_page(READING, [("cancel", _("Cancel"), DEFAULT, self.reject)])
        self._read(self._on_read)

    def _on_read(self, result) -> None:
        if self._closed:
            return
        if result.state is LookupState.UNKNOWN:
            self._show_problem(
                _("MyEditor Couldn’t Read Your Profile"),
                _("Your profile couldn’t be read from the network just now. Editing "
                  "it without reading it first could replace it, so try again in a "
                  "moment."), retry=True)
            return
        try:
            self._loaded = profile_fields(result.event)
        except ValueError:
            self._show_problem(
                _("This Profile Can’t Be Edited Here"),
                _("Your profile is stored in a form MyEditor doesn’t understand, so "
                  "it is left as it is. Edit it in the app that created it."),
                retry=False)
            return
        self._new_profile = result.state is LookupState.ABSENT
        self._show_form()

    # -- the form ------------------------------------------------------------

    def _build_form(self) -> None:
        body, col = page()
        col.addWidget(text_label(_("Your Profile"), "title"))
        self._form_note = text_label("", "muted")
        col.addWidget(self._form_note)
        form = QFormLayout()
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # A text box still reports a change while it is being destroyed;
        # through a weak reference that report never reaches a window that
        # is already going.
        window = weakref.ref(self)

        def edited(*_args) -> None:
            alive = window()
            if alive is not None and not alive._closed:
                alive._on_edited()

        for field in FIELDS:
            if field.kind == "about":
                edit = QPlainTextEdit()
                edit.setFixedHeight(84)
            else:
                edit = QLineEdit()
                edit.setPlaceholderText(_(field.placeholder) if field.placeholder else "")
            edit.textChanged.connect(edited)
            edit.setAccessibleName(_(field.label))
            error = text_label("", "error")
            error.hide()
            holder = QWidget()
            stack = QVBoxLayout(holder)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(2)
            stack.addWidget(edit)
            stack.addWidget(error)
            form.addRow(_(field.label), holder)
            self._edits[field.key] = edit
            self._errors[field.key] = error
        col.addLayout(form)
        col.addStretch(1)
        self.add_page(FORM, body)

    def _value(self, key: str) -> str:
        edit = self._edits[key]
        text = edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()
        return text.strip()

    def _show_form(self) -> None:
        for field in FIELDS:
            edit = self._edits[field.key]
            edit.blockSignals(True)
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(self._loaded.get(field.key, ""))
            else:
                edit.setText(self._loaded.get(field.key, ""))
            edit.blockSignals(False)
            self._errors[field.key].hide()
        self._set_form_note(
            _("You don’t have a profile yet. What you enter here becomes your profile.")
            if self._new_profile else
            _("This is what people see in every Nostr app. An empty field is removed "
              "from your profile."))
        self._show_form_page()

    def _show_form_page(self) -> None:
        self.show_page(FORM, [("cancel", _("Cancel"), NORMAL, self.reject),
                              ("save", _("Save"), DEFAULT, self._save_changes)])
        self._on_edited()

    def _set_form_note(self, text: str, *, problem: bool = False) -> None:
        self._form_note.setObjectName("error" if problem else "muted")
        self._form_note.style().unpolish(self._form_note)
        self._form_note.style().polish(self._form_note)
        self._form_note.setText(text)

    def changes(self) -> Dict[str, str]:
        """The fields that differ from the profile as it was read."""
        return {field.key: self._value(field.key) for field in FIELDS
                if self._value(field.key) != self._loaded.get(field.key, "")}

    def _on_edited(self) -> None:
        problems = False
        for field in FIELDS:
            problem = field_problem(field, self._value(field.key))
            label = self._errors[field.key]
            label.setText(problem or "")
            label.setVisible(bool(problem))
            problems = problems or bool(problem)
        button = self.buttons.get("save")
        if button is not None:
            button.setEnabled(bool(self.changes()) and not problems)

    # -- saving --------------------------------------------------------------

    def _build_saving(self) -> None:
        body, col = page()
        col.addStretch(1)
        self._saving_label = text_label("", "item_title")
        col.addWidget(self._saving_label)
        col.addWidget(busy_bar())
        col.addStretch(2)
        self.add_page(SAVING, body)

    def _save_changes(self) -> None:
        changes = self.changes()
        if not changes:
            return
        text = _("Saving your profile…")
        if not self._signs_locally:
            text += " " + _("Your signer app may ask you to approve.")
        self._saving_label.setText(text)
        self.show_page(SAVING, [])
        self._save(changes, self._on_saved)

    def _on_saved(self, outcome) -> None:
        if self._closed:
            return
        if outcome.status in (WRITTEN, UNCHANGED):
            self.saved.emit()
            self.accept()
            return
        # Back to the form, with what was typed, and what went wrong above it.
        if outcome.status == UNKNOWN_BASE:
            note = _("Your profile wasn’t saved: it couldn’t be read again just before "
                     "saving, so nothing was changed. Try again in a moment.")
        else:
            note = _("Your profile wasn’t saved: the signer didn’t sign it, or too few "
                     "servers took it. Nothing was changed. Try again in a moment.")
        self._set_form_note(note, problem=True)
        self._show_form_page()

    # -- problems ------------------------------------------------------------

    def _build_problem(self) -> None:
        body, col = page()
        self._problem_title = text_label("", "title")
        self._problem_text = text_label("")
        col.addWidget(self._problem_title)
        col.addWidget(self._problem_text)
        col.addStretch(1)
        self.add_page(PROBLEM, body)

    def _show_problem(self, title: str, text: str, *, retry: bool) -> None:
        self._problem_title.setText(title)
        self._problem_text.setText(text)
        if retry:
            buttons = [("close", _("Close"), NORMAL, self.reject),
                       ("retry", _("Try Again"), DEFAULT, self.start_reading)]
        else:
            buttons = [("close", _("Close"), DEFAULT, self.reject)]
        self.show_page(PROBLEM, buttons)

    def done(self, result: int) -> None:
        # An answer that arrives after the window closed is dropped.
        self._closed = True
        super().done(result)
