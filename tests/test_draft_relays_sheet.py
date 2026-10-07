# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Draft Relays sheet (nostr/ui/draft_relays_sheet.py).

What must hold: it reads the account's list before anything can change,
and says so when it can't (with Try Again); an address is added only
when it can be a relay for drafts (wss://, or ws:// on this computer),
once, and at most ten; Use My Usual Relays empties the form and Save
makes it so; Save publishes the encrypted list and moves the drafts'
routing; a save that failed says why and keeps the sheet; a save under
way can't be cancelled halfway; Return in the address field adds it;
every control has a name.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.draft_relays import KIND_PRIVATE_RELAYS, PrivateDraftRelays  # noqa: E402
from nostr.outbox import defaults  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from nostr.outbox.writer import FAILED, UNKNOWN_BASE, WRITTEN, WriteOutcome  # noqa: E402
from nostr.ui.draft_relays_sheet import (  # noqa: E402
    EDITING, READ_FAILED, SAVING, DraftRelaysSheet,
)
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, PK, FakePool, FakeQuery, FakeSessionPool, Profile, settle,
)
from tests.test_draft_relays import SealingClient  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication(sys.argv)


class Lists:
    """Stands in for PrivateDraftRelays: answers reads, keeps or parks saves."""

    def __init__(self, relays=(), *, readable=True, answer=WriteOutcome(WRITTEN)):
        self.relays = list(relays)
        self.readable = readable
        self.answer = answer
        self.saves = []
        self.parked = []

    def read(self, on_done):
        on_done(list(self.relays) if self.readable else None)

    def save(self, relays, on_done):
        self.saves.append(list(relays))
        if self.answer is None:
            self.parked.append(on_done)
        else:
            on_done(self.answer)


def open_sheet(lists):
    sheet = DraftRelaysSheet(lists, is_dark=False)
    sheet.show()
    saved = []
    sheet.saved.connect(saved.append)
    return sheet, saved


def add(sheet, text):
    sheet._field.setText(text)
    sheet._add.click()


def words(sheet):
    return [sheet.buttons[key].text() for key in sheet.buttons]


def test_it_reads_first_and_edits_what_it_read():
    sheet, _saved = open_sheet(Lists(["wss://mine.example"]))
    assert sheet.state == EDITING and sheet.chosen() == ["wss://mine.example"]
    assert sheet._summary.text() == "Drafts are saved only on these relays:"
    assert words(sheet) == ["Use My Usual Relays", "Cancel", "Save"]
    assert not sheet.buttons["save"].isEnabled()          # nothing changed yet
    assert sheet.buttons["save"].isDefault()
    assert unnamed_controls(sheet) == []


def test_an_account_without_a_list_uses_its_usual_relays():
    sheet, _saved = open_sheet(Lists([]))
    assert sheet._summary.text() == "Drafts are kept on your usual relays."
    assert not sheet.buttons["usual"].isEnabled()


def test_a_list_that_could_not_be_read_is_not_edited_and_offers_try_again():
    lists = Lists(["wss://mine.example"], readable=False)
    sheet, _saved = open_sheet(lists)
    assert sheet.state == READ_FAILED and sheet._error.text().startswith(
        "Couldn’t read where your drafts are kept.")
    assert words(sheet) == ["Cancel", "Try Again"] and sheet.buttons["retry"].isDefault()
    assert not sheet._field.isEnabled() and not sheet._list.isEnabled()
    assert not sheet._summary.isVisibleTo(sheet)          # where drafts go is not known
    assert unnamed_controls(sheet) == []
    lists.readable = True
    sheet.buttons["retry"].click()
    assert sheet.state == EDITING and sheet.chosen() == ["wss://mine.example"]


def test_an_address_is_added_only_when_it_can_be_one_and_only_once():
    sheet, _saved = open_sheet(Lists([]))
    add(sheet, "ws://relay.example")
    assert sheet.chosen() == [] and sheet._error.isVisibleTo(sheet)
    assert sheet._error.text() == "Enter a relay’s address. It starts with wss://."
    assert sheet._field.accessibleDescription() == sheet._error.text()
    add(sheet, "wss://Relay.Example/")
    assert sheet.chosen() == ["wss://relay.example"] and not sheet._error.isVisibleTo(sheet)
    add(sheet, "wss://relay.example")
    assert sheet._error.text() == "That relay is already in the list."
    add(sheet, "ws://localhost:4869")                     # a relay on this computer
    assert sheet.chosen() == ["wss://relay.example", "ws://localhost:4869"]
    assert sheet.buttons["save"].isEnabled()


def test_return_in_the_address_field_adds_it_and_does_not_save():
    lists = Lists([])
    sheet, saved = open_sheet(lists)
    sheet._field.setFocus()
    sheet._field.setText("wss://typed.example")
    QTest.keyClick(sheet._field, Qt.Key.Key_Return)
    assert sheet.chosen() == ["wss://typed.example"] and lists.saves == [] and saved == []
    assert sheet._field.text() == ""


def test_drafts_go_to_at_most_ten_relays():
    sheet, _saved = open_sheet(Lists([f"wss://r{n}.example" for n in range(defaults.PRIVATE_CAP)]))
    add(sheet, "wss://one-more.example")
    assert len(sheet.chosen()) == defaults.PRIVATE_CAP
    assert sheet._error.text() == "Drafts can be kept on at most 10 relays."


def test_removing_a_relay():
    sheet, _saved = open_sheet(Lists(["wss://a.example", "wss://b.example", "wss://c.example"]))
    assert not sheet._remove.isEnabled()                  # nothing selected
    sheet._list.setCurrentRow(1)
    sheet._remove.click()
    assert sheet.chosen() == ["wss://a.example", "wss://c.example"]
    assert sheet._list.currentRow() == 1                  # the next one, for the keyboard
    QTest.keyClick(sheet._list, Qt.Key.Key_Delete)
    assert sheet.chosen() == ["wss://a.example"]


def test_use_my_usual_relays_empties_the_form_and_save_makes_it_so():
    lists = Lists(["wss://mine.example"])
    sheet, saved = open_sheet(lists)
    sheet.buttons["usual"].click()
    assert sheet.chosen() == [] and sheet._summary.text() == (
        "Drafts are kept on your usual relays.")
    assert lists.saves == []                              # nothing changes before Save
    sheet.buttons["save"].click()
    assert lists.saves == [[]] and saved == [[]] and not sheet.isVisible()


def test_a_save_that_failed_says_why_and_keeps_the_sheet():
    signed_but_refused = {"kind": KIND_PRIVATE_RELAYS}
    for answer, start in (
            (WriteOutcome(UNKNOWN_BASE), "Couldn’t read the current setting"),
            (WriteOutcome(FAILED, event=signed_but_refused), "Too few relays took the change."),
            (WriteOutcome(FAILED, reason="user rejected"), "Your signer didn’t approve")):
        sheet, saved = open_sheet(Lists([], answer=answer))
        add(sheet, "wss://mine.example")
        sheet.buttons["save"].click()
        assert sheet.isVisible() and saved == [] and sheet.state == EDITING
        assert sheet._error.text().startswith(start)
        assert sheet.buttons["save"].isEnabled()          # try again


def test_a_save_under_way_cannot_be_cancelled_halfway():
    lists = Lists([], answer=None)
    sheet, saved = open_sheet(lists)
    add(sheet, "wss://mine.example")
    sheet.buttons["save"].click()
    assert sheet.state == SAVING and sheet._busy.isVisibleTo(sheet)
    assert not sheet.buttons["cancel"].isEnabled() and not sheet._field.isEnabled()
    sheet.reject()                                        # Escape, the close box
    assert sheet.isVisible()
    lists.parked.pop()(WriteOutcome(WRITTEN))
    assert saved == [["wss://mine.example"]] and not sheet.isVisible()


def test_an_answer_after_the_sheet_closed_is_dropped():
    lists = Lists([], answer=None)
    sheet, saved = open_sheet(lists)
    add(sheet, "wss://mine.example")
    sheet.buttons["save"].click()
    sheet.done(0)
    lists.parked.pop()(WriteOutcome(WRITTEN))
    assert saved == []


def test_save_publishes_the_encrypted_list_and_drafts_follow_it():
    client = SealingClient()
    pool = FakePool()
    query = FakeQuery({KIND_PRIVATE_RELAYS: ABSENT})
    directory = RelayDirectory(pool, query=query, store_path=None)
    lists = PrivateDraftRelays(profile=Profile(), pool=pool,
                               session_pool=FakeSessionPool(client), directory=directory,
                               query=query, clock=lambda: NOW)
    sheet, saved = open_sheet(lists)
    settle()
    assert sheet.state == EDITING and sheet.chosen() == []
    add(sheet, "wss://mine.example")
    sheet.buttons["save"].click()
    settle(10)
    assert saved == [["wss://mine.example"]]
    (_relays, event), = pool.published
    assert event["kind"] == KIND_PRIVATE_RELAYS and event["tags"] == []
    assert event["content"] == 'sealed:[["relay","wss://mine.example"]]'
    assert directory.draft_relays_of(PK) == ["wss://mine.example"]
