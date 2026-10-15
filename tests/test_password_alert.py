# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the alerts that ask for a password (password_alert.py).

What must hold, as Pages asks for a document's password:

  Locking a document asks for the password twice and says plainly that a
  forgotten one opens nothing; Set Password acts only once both are typed
  alike. Opening one asks for its password; a wrong one is said under the
  field, which is emptied, and the alert stays. Changing it asks for the
  old one, then the new one twice; Remove Password needs only the old one.
  Every field is secure and named for a screen reader, and the first has
  the focus.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

import password_alert  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


def type_in(alert, **texts):
    for name, text in texts.items():
        alert.fields[name].setText(text)


def test_setting_a_password_asks_twice_and_warns_it_cannot_be_recovered():
    alert = password_alert.set_password_alert(None, name="diary.md")
    assert "can’t be opened" in alert.message_label.text()
    set_button = alert.button_for(password_alert.SET)
    assert not set_button.isEnabled()
    type_in(alert, password="horse", verify="hors")
    assert not set_button.isEnabled() and alert.problem.text() == "The passwords don’t match."
    type_in(alert, verify="horse")
    assert set_button.isEnabled() and alert.problem.text() == ""
    set_button.click()
    assert alert.value == password_alert.SET and alert.text("password") == "horse"
    for field in alert.fields.values():
        assert field.echoMode() == QLineEdit.EchoMode.Password
    assert unnamed_controls(alert) == []


def test_a_wrong_password_is_said_and_the_alert_stays():
    tried = []

    def check(password):
        tried.append(password)
        return "opened" if password == "horse" else None

    alert = password_alert.open_alert(None, name="diary.md", check=check)
    alert.show()
    assert alert.focusWidget() is alert.fields["password"]      # the field it opens on
    type_in(alert, password="pony")
    alert.button_for(password_alert.OPEN).click()
    assert alert.isVisible() and alert.problem.text() == "The password is incorrect."
    assert alert.text("password") == ""
    type_in(alert, password="horse")
    alert.button_for(password_alert.OPEN).click()
    assert not alert.isVisible() and alert.result == "opened"
    assert tried == ["pony", "horse"]


def test_changing_asks_for_the_old_password_and_removing_needs_only_it():
    alert = password_alert.change_alert(None, name="diary.md", check=lambda p: p == "horse")
    change = alert.button_for(password_alert.CHANGE)
    remove = alert.button_for(password_alert.REMOVE)
    assert not change.isEnabled() and not remove.isEnabled()
    type_in(alert, old="horse")
    assert remove.isEnabled() and not change.isEnabled()
    type_in(alert, new="battery", verify="battery")
    assert change.isEnabled()
    change.click()
    assert alert.value == password_alert.CHANGE and alert.text("new") == "battery"
    alert = password_alert.change_alert(None, name="diary.md", check=lambda p: p == "horse")
    alert.show()
    type_in(alert, old="pony")
    alert.button_for(password_alert.REMOVE).click()
    assert alert.isVisible() and alert.problem.text() == "The password is incorrect."
