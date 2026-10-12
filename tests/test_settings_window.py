# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the Settings window (settings_window.py).

What must hold:

  It shows the same choices as the menus and chooses through the menus'
  commands, so the two never disagree: picking Dark here is View >
  Appearance > Dark, and picking it in the menu shows here at once.

  A choice applies at once: no OK, Apply or Cancel. Escape closes it.

  What happens to a pasted image is offered only while a Nostr account is
  in use (nothing of Nostr before), and reads the setting again when the
  window is shown, since an alert's Remember This Choice can change it.

  Every control has a name a screen reader can announce.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QAction, QActionGroup  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from settings_window import SettingsWindow  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def group_of(names, checked):
    group = QActionGroup(None)
    actions = {}
    for name in names:
        action = QAction(name.title(), group)
        action.setCheckable(True)
        action.setChecked(name == checked)
        actions[name] = action
    return group, actions


class Fixture:
    def __init__(self, *, nostr=False, paste="ask"):
        self.appearance_group, self.appearance = group_of(("system", "light", "dark"), "system")
        self.language_group, languages = group_of(("system", "en", "de"), "en")
        self.languages = list(languages.items())
        self.chosen_languages = []
        self.monospace = QAction("Use Monospace Font for Writing")
        self.monospace.setCheckable(True)
        self.paste = paste
        self.nostr = nostr
        self.triggered = []
        for mode, action in self.appearance.items():
            action.triggered.connect(lambda _c=False, m=mode: self.triggered.append(m))
        self.window = SettingsWindow(
            appearance=self.appearance, languages=self.languages,
            choose_language=self.chosen_languages.append, monospace=self.monospace,
            paste_choice=lambda: self.paste, choose_paste=self._store_paste,
            nostr_in_use=lambda: self.nostr, is_dark=False)

    def _store_paste(self, value):
        self.paste = value


def test_choosing_an_appearance_is_the_menu_command():
    f = Fixture()
    f.window.appearance_buttons["dark"].click()
    assert f.triggered == ["dark"]
    assert f.appearance["dark"].isChecked()


def test_a_choice_in_the_menu_shows_in_the_window():
    f = Fixture()
    f.appearance["light"].trigger()
    assert f.window.appearance_buttons["light"].isChecked()
    assert not f.window.appearance_buttons["system"].isChecked()
    f.monospace.setChecked(True)
    assert f.window.monospace.isChecked()
    f.languages[2][1].setChecked(True)
    assert f.window.language.currentData() == "de"


def test_a_closed_window_leaves_nothing_behind_on_the_menu(monkeypatch):
    # Review M2: the appearance row's connection outlived the window, and
    # every later appearance change raised on its deleted buttons.
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    f = Fixture()
    f.window.setAttribute(Qt.WA_DeleteOnClose, True)
    f.window.show()
    f.window.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    f.appearance["dark"].trigger()
    f.appearance["light"].trigger()
    assert errors == []


def test_the_language_shown_is_the_one_chosen_and_choosing_remembers_it():
    f = Fixture()
    assert f.window.language.currentData() == "en"
    f.window.language.activated.emit(2)
    assert f.chosen_languages == ["de"]


def test_the_writing_font_is_the_view_menu_command():
    f = Fixture()
    f.window.monospace.click()
    assert f.monospace.isChecked()


def test_pasted_images_are_offered_only_while_nostr_is_in_use():
    f = Fixture(nostr=False)
    f.window.show()
    QApplication.processEvents()
    assert not f.window.paste.isVisible()
    f.nostr = True
    f.window.refresh()
    QApplication.processEvents()
    assert f.window.paste.isVisible()
    f.window.close()


def test_the_pasted_image_choice_is_stored_and_read_again_when_shown():
    f = Fixture(nostr=True, paste="never")
    assert f.window.paste.currentData() == "never"
    f.window.paste.activated.emit(f.window.paste.findData("always"))
    assert f.paste == "always"
    f.paste = "ask"                      # an alert remembered something else meanwhile
    f.window.show()
    assert f.window.paste.currentData() == "ask"
    f.window.close()


def test_a_choice_applies_at_once_and_escape_closes():
    f = Fixture(nostr=True)
    assert f.window.findChildren(QPushButton) == []      # no OK, Apply or Cancel
    f.window.show()
    QApplication.processEvents()
    QTest.keyClick(f.window, Qt.Key_Escape)
    assert not f.window.isVisible()


@pytest.mark.parametrize("nostr", [False, True])
def test_every_control_has_a_name(nostr):
    f = Fixture(nostr=nostr)
    assert unnamed_controls(f.window) == []


def test_choosing_from_settings_says_nothing_more_than_the_window_does(monkeypatch):
    """View > Language explains the restart in an alert; Settings says it
    under the choice, so the window's way in asks for no alert."""
    import main_window
    from main_window import MainWindow
    told, saved = [], {}
    monkeypatch.setattr(main_window, "inform", lambda *a, **k: told.append(k))
    monkeypatch.setattr(main_window, "save_setting", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(main_window.i18n, "resolve", lambda code: code)
    monkeypatch.setattr(main_window.i18n, "language", lambda: "en")
    _group, actions = group_of(("system", "en", "de"), "en")
    host = SimpleNamespace(_language_codes=["system", "en", "de"],
                           act_languages=list(actions.values()), is_dark_theme=False)
    MainWindow._choose_language(host, "de", tell=False)
    assert saved == {"language": "de"} and told == []
    assert actions["de"].isChecked()
    MainWindow._choose_language(host, "de")
    assert len(told) == 1
