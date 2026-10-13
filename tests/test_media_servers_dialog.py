# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Media Servers sheet: the one place the person's servers are managed.

What must hold:

  The servers are listed in the order uploads use them, the members'
  server after them, named as the association and not removable here.

  A server is added by its address; an address that is no media server
  address says so and adds nothing. Remove asks first, and the last
  server cannot be removed. Make Primary moves a server to the top.

  The servers the person's Nostr account lists and this app does not
  use are offered, and added only on the click.

  Share with Other Apps… asks the window that owns publishing.

Every change is saved at once and announced, so the library reads the
new servers.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import alerts  # noqa: E402
from nostr.blossom.settings import BlossomSettings  # noqa: E402
from nostr.ui.media_servers_dialog import MediaServersDialog  # noqa: E402
from tests.media_fakes import fake_alert  # noqa: E402

A, B, C = "https://a.example", "https://b.example", "https://c.example"
E21 = "https://blossom.einundzwanzig.space"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def sheet(tmp_path, *, servers=(A, B), entitled=(), suggestions=(), label=None):
    settings = BlossomSettings(tmp_path / "blossom_servers.json")
    settings.set_custom_servers(list(servers))
    dialog = MediaServersDialog(
        settings=settings, entitled=list(entitled), suggestions=list(suggestions),
        server_label=label or (lambda origin: None), is_dark=False)
    changes = []
    dialog.changed.connect(lambda: changes.append(list(settings.configured_servers)))
    return dialog, settings, changes


def rows(dialog):
    lst = dialog.server_list
    return [lst.item(i).text() for i in range(lst.count())]


def select(dialog, row):
    dialog.server_list.setCurrentRow(row)


def test_servers_are_listed_in_upload_order_with_the_members_server_last(tmp_path):
    dialog, _settings, _ = sheet(
        tmp_path, entitled=[E21],
        label=lambda origin: "EINUNDZWANZIG" if origin == E21 else None)
    assert rows(dialog) == ["a.example", "b.example", "EINUNDZWANZIG"]
    members = dialog.server_list.item(2)
    assert not members.flags() & Qt.ItemFlag.ItemIsSelectable
    assert members.toolTip() == "Included with your membership"


def test_a_server_is_added_by_its_address(tmp_path):
    dialog, settings, changes = sheet(tmp_path)
    dialog.address.setText("c.example")
    dialog.add_button.click()
    assert settings.configured_servers == [A, B, C]
    assert rows(dialog)[-1] == "c.example"
    assert changes == [[A, B, C]]
    assert dialog.address.text() == ""


def test_an_address_that_is_no_media_server_adds_nothing_and_says_so(tmp_path):
    dialog, settings, changes = sheet(tmp_path)
    dialog.address.setText("http://insecure.example")
    dialog.add_button.click()
    assert settings.configured_servers == [A, B]
    assert changes == []
    assert not dialog.address_error.isHidden()
    assert dialog.address_error.text() == (
        "Enter a server address that starts with https://, such as "
        "https://media.example.com.")
    dialog.address.setText("c.example")
    assert dialog.address_error.isHidden()


def test_remove_asks_first(tmp_path, monkeypatch):
    dialog, settings, changes = sheet(tmp_path)
    alert, shown = fake_alert(click="Cancel")
    monkeypatch.setattr(alerts, "Alert", alert)
    select(dialog, 1)
    dialog.remove_button.click()
    assert settings.configured_servers == [A, B]
    assert shown[0].title == "Remove “b.example”?"
    assert shown[0].message == (
        "Files already there stay there. The Media Library shows them only "
        "while another of your servers has them too.")

    alert, shown = fake_alert(click="Remove")
    monkeypatch.setattr(alerts, "Alert", alert)
    dialog.remove_button.click()
    assert settings.configured_servers == [A]
    assert changes == [[A]]


def test_the_last_server_cannot_be_removed(tmp_path):
    dialog, _settings, _ = sheet(tmp_path, servers=(A,))
    select(dialog, 0)
    assert not dialog.remove_button.isEnabled()


def test_make_primary_moves_a_server_to_the_top(tmp_path):
    dialog, settings, changes = sheet(tmp_path)
    select(dialog, 0)
    assert not dialog.primary_button.isEnabled()        # already first
    select(dialog, 1)
    assert dialog.primary_button.isEnabled()
    dialog.primary_button.click()
    assert settings.configured_servers == [B, A]
    assert rows(dialog) == ["b.example", "a.example"]
    assert changes == [[B, A]]


def test_the_accounts_own_servers_are_offered_and_added_on_the_click(tmp_path):
    dialog, settings, changes = sheet(tmp_path, suggestions=[C])
    assert not dialog.suggestions.isHidden()
    assert dialog.suggestion_text.text() == "Your Nostr account also lists c.example."
    assert settings.configured_servers == [A, B]
    dialog.suggestion_button.click()
    assert settings.configured_servers == [A, B, C]
    assert dialog.suggestions.isHidden()


def test_nothing_to_offer_shows_nothing(tmp_path):
    dialog, _settings, _ = sheet(tmp_path)
    assert dialog.suggestions.isHidden()


def test_sharing_is_asked_of_the_owner(tmp_path):
    dialog, _settings, _ = sheet(tmp_path)
    asked = []
    dialog.share_requested.connect(lambda: asked.append(True))
    dialog.share_button.click()
    assert asked == [True]


def test_every_control_has_a_name_for_a_screen_reader(tmp_path):
    dialog, _settings, _ = sheet(tmp_path)
    assert dialog.server_list.accessibleName() == "Media servers"
    assert dialog.address.accessibleName() == "Server address"
    for button in (dialog.add_button, dialog.remove_button, dialog.primary_button,
                   dialog.share_button):
        assert button.text()


@pytest.mark.parametrize("text", ["http://example.com", "ftp://x.example", "https://",
                                  "media example com", "https://ex ample.com/a b",
                                  "nodot", "https://nodot"])
def test_a_malformed_address_is_refused_by_return_and_the_sheet_stays(tmp_path, text):
    from PySide6.QtTest import QTest
    dialog, settings, changes = sheet(tmp_path)
    dialog.show()
    dialog.address.setFocus()
    dialog.address.setText(text)
    QTest.keyClick(dialog.address, Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert not dialog.address_error.isHidden()
    assert settings.configured_servers == [A, B]
    assert changes == []
    dialog.add_button.click()
    assert settings.configured_servers == [A, B]


@pytest.mark.parametrize("text, origin", [("c.example", C), ("https://c.example/path", C),
                                          ("http://localhost:3000", "http://localhost:3000"),
                                          ("https://192.0.2.7", "https://192.0.2.7")])
def test_return_adds_a_good_address_and_the_sheet_stays(tmp_path, text, origin):
    from PySide6.QtTest import QTest
    dialog, settings, _changes = sheet(tmp_path)
    dialog.show()
    dialog.address.setFocus()
    dialog.address.setText(text)
    QTest.keyClick(dialog.address, Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert settings.configured_servers == [A, B, origin]


def test_done_answers_return_when_the_address_field_is_empty(tmp_path):
    from PySide6.QtTest import QTest
    dialog, _settings, _changes = sheet(tmp_path)
    dialog.show()
    dialog.address.setFocus()
    QTest.keyClick(dialog.address, Qt.Key.Key_Return)
    assert not dialog.isVisible()


def test_it_is_a_sheet_on_the_window_that_opened_it(tmp_path):
    """Window-modal, as the app's other sheets are (Draft Relays): it
    blocks the library it belongs to, not the whole app."""
    dialog, _settings, _changes = sheet(tmp_path)
    assert dialog.windowModality() == Qt.WindowModality.WindowModal


def test_the_main_window_opens_it_as_a_sheet_on_the_library():
    import inspect
    import textwrap
    from main_window import MainWindow
    source = textwrap.dedent(inspect.getsource(MainWindow._open_media_servers))
    assert "sheet.open()" in source and "exec()" not in source
    assert "self._visible_media_library()" in source
