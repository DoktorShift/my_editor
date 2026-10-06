# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Edit Profile.

What must hold:

  The profile is read fresh before anything can be edited. A profile that
  could not be read is never edited (it could be replaced); one the
  network clearly does not have can be created.

  Only the fields that were changed are saved; an emptied field is
  removed. Every other field of the profile is kept by the writer, which
  test_profile_editing pins against a real writer.

  A field with a problem says so under itself, and Save waits until
  something changed and nothing is wrong.

  A failed save keeps what was typed and says what happened.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.outbox.lookup import Lookup  # noqa: E402
from nostr.outbox.policy import LookupState  # noqa: E402
from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.ui import profile_window as pw  # noqa: E402
from nostr.ui.profile_window import FIELDS, FORM, PROBLEM, READING, SAVING  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def found(**fields):
    return Lookup(LookupState.FOUND, event={"content": json.dumps(fields)})


class Network:
    def __init__(self):
        self.reads = []
        self.saves = []

    def read(self, on_done):
        self.reads.append(on_done)

    def save(self, changes, on_done):
        self.saves.append((changes, on_done))


def window(**kw):
    net = Network()
    win = pw.ProfileWindow(read=net.read, save=net.save, is_dark=False, **kw)
    return win, net


def edit(win, key, text):
    widget = win._edits[key]
    if hasattr(widget, "setPlainText"):
        widget.setPlainText(text)
    else:
        widget.setText(text)


def test_the_profile_is_read_before_anything_can_be_edited():
    win, net = window()
    assert win.page == READING and len(net.reads) == 1
    net.reads[0](found(name="alice", display_name="Alice", about="Hi",
                       lud16="alice@wallet.example"))
    assert win.page == FORM
    assert win._edits["display_name"].text() == "Alice"
    assert win._edits["about"].toPlainText() == "Hi"
    assert not win.buttons["save"].isEnabled()             # nothing changed yet


def test_a_profile_that_could_not_be_read_is_not_edited():
    win, net = window()
    net.reads[0](Lookup(LookupState.UNKNOWN))
    assert win.page == PROBLEM
    assert "Try Again" in [b.text() for b in win.buttons.values()]
    win.buttons["retry"].click()
    assert win.page == READING and len(net.reads) == 2


def test_a_profile_that_is_not_an_object_is_left_alone():
    win, net = window()
    net.reads[0](Lookup(LookupState.FOUND, event={"content": "[1, 2]"}))
    assert win.page == PROBLEM and "retry" not in win.buttons


def test_no_profile_yet_starts_an_empty_one():
    win, net = window()
    net.reads[0](Lookup(LookupState.ABSENT))
    assert win.page == FORM
    assert "don’t have a profile yet" in win._form_note.text()


def test_only_changed_fields_are_saved_and_an_emptied_one_is_removed():
    win, net = window()
    net.reads[0](found(name="alice", display_name="Alice", website="https://a.example"))
    edit(win, "display_name", "Alice B")
    edit(win, "website", "")
    assert win.changes() == {"display_name": "Alice B", "website": ""}
    win.buttons["save"].click()
    assert win.page == SAVING
    assert net.saves[0][0] == {"display_name": "Alice B", "website": ""}


def test_the_older_display_name_key_is_shown():
    win, net = window()
    net.reads[0](found(displayName="Old Style"))
    assert win._edits["display_name"].text() == "Old Style"


@pytest.mark.parametrize("key, value, problem", [
    ("picture", "http://cdn.example/a.png", "beginning with https"),
    ("banner", "not a link", "beginning with https"),
    ("website", "example.com", "beginning with http or https"),
    ("lud16", "alice", "Lightning address"),
    ("nip05", "alice@", "Nostr address"),
    ("name", "two words", "spaces"),
])
def test_a_field_with_a_problem_says_so_and_blocks_saving(key, value, problem):
    win, net = window()
    net.reads[0](found(name="alice"))
    edit(win, key, value)
    error = win._errors[key]
    assert not error.isHidden() and problem in error.text()
    assert not win.buttons["save"].isEnabled()


@pytest.mark.parametrize("key, value", [
    ("picture", "https://cdn.example/a.png"),
    ("website", "http://old.example"),
    ("lud16", "alice@wallet.example"),
    ("nip05", "example.com"),
    ("nip05", "alice@example.com"),
])
def test_good_values_pass(key, value):
    field = next(f for f in FIELDS if f.key == key)
    assert pw.field_problem(field, value) is None


def test_a_saved_profile_closes_the_window_and_says_so():
    win, net = window()
    saved = []
    win.saved.connect(lambda: saved.append(True))
    net.reads[0](found(name="alice"))
    edit(win, "about", "New about")
    win.buttons["save"].click()
    net.saves[0][1](outbox_writer.WriteOutcome(outbox_writer.WRITTEN))
    assert saved == [True] and win.result() == win.DialogCode.Accepted


def test_a_failed_save_keeps_what_was_typed():
    win, net = window()
    net.reads[0](found(name="alice"))
    edit(win, "about", "New about")
    win.buttons["save"].click()
    net.saves[0][1](outbox_writer.WriteOutcome(outbox_writer.FAILED, reason="no"))
    assert win.page == FORM
    assert win._edits["about"].toPlainText() == "New about"
    assert "wasn’t saved" in win._form_note.text()
    assert win.buttons["save"].isEnabled()


def test_a_signer_account_is_told_to_look_at_the_signer():
    win, net = window()
    net.reads[0](found(name="alice"))
    edit(win, "about", "x")
    win.buttons["save"].click()
    assert "signer app" in win._saving_label.text()
    local, net2 = window(signs_locally=True)
    net2.reads[0](found(name="alice"))
    edit(local, "about", "x")
    local.buttons["save"].click()
    assert "signer" not in local._saving_label.text()


def test_an_answer_after_closing_is_dropped():
    win, net = window()
    win.reject()
    net.reads[0](found(name="alice"))         # nothing happens, nothing raises
    assert win.page == READING
