# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the Settings window (settings_window.py).

What must hold:

  It shows the same choices as the menus and chooses through the menus'
  commands, so the two never disagree: picking Dark here is View >
  Appearance > Dark, and picking it in the menu shows here at once.

  A choice applies at once: no OK, Apply or Cancel. Escape closes it.

  Whether the format bar shows over a selection made with the pointer is a
  writing choice, on unless turned off, and kept.

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
    def __init__(self, *, nostr=False, upload="publishing", format_bar=True):
        self.appearance_group, self.appearance = group_of(("system", "light", "dark"), "system")
        self.language_group, languages = group_of(("system", "en", "de"), "en")
        self.languages = list(languages.items())
        self.chosen_languages = []
        self.family = "system"
        self.upload = upload
        self.nostr = nostr
        self.format_bar = format_bar
        self.triggered = []
        for mode, action in self.appearance.items():
            action.triggered.connect(lambda _c=False, m=mode: self.triggered.append(m))
        self.window = SettingsWindow(
            appearance=self.appearance, languages=self.languages,
            choose_language=self.chosen_languages.append,
            writing_family=lambda: self.family, choose_writing_family=self._store_family,
            upload_choice=lambda: self.upload, choose_upload=self._store_upload,
            nostr_in_use=lambda: self.nostr,
            format_bar=lambda: self.format_bar, choose_format_bar=self._store_format_bar,
            is_dark=False)

    def _store_format_bar(self, on):
        self.format_bar = on

    def _store_family(self, family):
        self.family = family

    def _store_upload(self, value):
        self.upload = value


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
    f.family = "monospace"                               # View > Use Monospace Font…
    f.window.refresh()
    assert f.window.font.currentData() == "monospace"
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


@pytest.fixture(autouse=True)
def actual_text_size():
    # The text size is the app's: each test leaves it as it found it.
    yield
    from text_size import text_size
    text_size().reset()


def test_the_writing_font_is_chosen_from_fonts_named_in_themselves():
    f = Fixture()
    font = f.window.font
    titles = [font.itemText(i) for i in range(font.count()) if font.itemText(i)]
    assert titles == ["System", "Serif", "Monospace", "Other…"]
    assert font.itemData(1, Qt.ItemDataRole.FontRole) is not None   # shown in itself
    font.activated.emit(1)
    assert f.family == "serif"


def test_other_opens_the_systems_font_panel_and_shows_the_font_picked(monkeypatch):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QFontDialog
    import fonts
    family = fonts.monospace_family()                   # one installed everywhere
    from text_size import text_size
    bigger = text_size().sizes()[-1]
    monkeypatch.setattr(QFontDialog, "getFont", staticmethod(
        lambda *args, **kwargs: (QFont(family, bigger), True)))
    f = Fixture()
    f.window.font.activated.emit(f.window.font.findData("other"))
    assert f.family == family
    assert text_size().points() == bigger                # the panel sets the size too
    f.window.refresh()
    assert f.window.font.currentText() == family
    assert f.window.font_size.currentData() == bigger


def test_the_font_size_is_chosen_in_points_beside_the_font_and_the_view_menu_moves_it():
    """Asked for: "an option to change the size of the font, like 8, 10, 12".
    The size sits beside the font, as a Mac's settings name a font
    ("Helvetica 12")."""
    from text_size import text_size
    f = Fixture()
    sizes = f.window.font_size
    offered = [sizes.itemData(i) for i in range(sizes.count())]
    assert offered == list(text_size().sizes()) and 12 in offered and 24 in offered
    assert sizes.currentData() == text_size().actual()
    assert sizes.itemText(sizes.findData(12)) == "12 pt"
    sizes.activated.emit(sizes.findData(24))
    assert text_size().points() == 24
    text_size().shrink()                                 # View > Zoom Out
    assert sizes.currentData() == offered[offered.index(24) - 1]
    font_row = f.window.font.parentWidget()
    assert sizes.parentWidget() is font_row              # on the font's row


def test_the_format_bar_over_a_selection_is_a_writing_choice():
    f = Fixture()
    box = f.window.format_bar
    assert box.text() == "Show the format bar when selecting text"
    assert box.isChecked()
    box.click()
    assert f.format_bar is False
    f.format_bar = True                                  # chosen elsewhere
    f.window.refresh()
    assert box.isChecked()
    assert Fixture(format_bar=False).window.format_bar.isChecked() is False


def test_when_files_upload_is_offered_only_while_nostr_is_in_use():
    f = Fixture(nostr=False)
    f.window.show()
    QApplication.processEvents()
    assert not f.window.upload.isVisible()
    f.nostr = True
    f.window.refresh()
    QApplication.processEvents()
    assert f.window.upload.isVisible()
    f.window.close()


def test_the_upload_choice_is_stored_and_read_again_when_shown():
    f = Fixture(nostr=True, upload="now")
    assert [f.window.upload.itemData(i) for i in range(f.window.upload.count())] == [
        "publishing", "now"]
    assert f.window.upload.currentData() == "now"
    f.window.upload.activated.emit(f.window.upload.findData("publishing"))
    assert f.upload == "publishing"
    f.upload = "now"
    f.window.show()
    assert f.window.upload.currentData() == "now"
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



def test_command_w_closes_it_as_any_window():
    # Review, decision 1: only Escape closed it. Every key the system binds
    # to Close does (Command-W, Ctrl+W, and Ctrl+F4 on Windows).
    f = Fixture()
    f.window.show()
    QApplication.processEvents()
    QTest.keyClick(f.window, Qt.Key_W, Qt.ControlModifier)
    assert not f.window.isVisible()


def test_where_published_posts_open_is_offered_with_nostr_and_kept():
    """Settings > Open posts on the web in: the reading apps, Standup
    Discover first and preselected, only while Nostr is in use."""
    chosen = []
    actions_group, appearance = group_of(("system", "light", "dark"), "system")
    language_group, languages = group_of(("en",), "en")
    nostr = {"on": False}
    window = SettingsWindow(
        appearance=appearance, languages=list(languages.items()),
        choose_language=lambda code: None, writing_family=lambda: "system",
        choose_writing_family=lambda family: None,
        upload_choice=lambda: "publishing", choose_upload=lambda value: None,
        nostr_in_use=lambda: nostr["on"], format_bar=lambda: True,
        choose_format_bar=lambda on: None, web_reader=lambda: "discover",
        choose_web_reader=chosen.append)
    assert not window._reader_box.isVisibleTo(window)
    nostr["on"] = True
    window.refresh()
    assert window._reader_box.isVisibleTo(window)
    names = [window.reader.itemText(i) for i in range(window.reader.count())]
    assert names[0] == "Standup Discover" and window.reader.currentText() == "Standup Discover"
    window.reader.activated.emit(window.reader.findData("primal"))
    assert chosen == ["primal"]
    assert window.reader.accessibleName() == "Open posts on the web in"
    assert unnamed_controls(window) == []


# -- Dictation (dictation/) -------------------------------------------------------------

def dictation_window(ways, prefs=None, **kwargs):
    from dictation.preferences import Preferences
    chosen = [prefs or Preferences(way=ways[0] if ways else "", language="de")]
    group, appearance = group_of(("system",), "system")
    window = SettingsWindow(
        appearance=appearance, languages=[],
        choose_language=lambda code: None,
        writing_family=lambda: "system", choose_writing_family=lambda family: None,
        upload_choice=lambda: "publishing", choose_upload=lambda value: None,
        nostr_in_use=lambda: False, format_bar=lambda: True,
        choose_format_bar=lambda on: None, dictation_ways=ways,
        dictation=lambda: chosen[-1], choose_dictation=chosen.append, is_dark=False, **kwargs)
    window._test_group = group                    # the appearance actions live with it
    return window, chosen


def test_dictation_settings_show_only_where_it_can_run_on_this_computer():
    import dictation
    for ways in ((), (dictation.SYSTEM,)):                  # a Mac: the system's alone
        window, _chosen = dictation_window(ways)
        assert window.dictation_model is None and window.dictation_way is None
    window, _chosen = dictation_window((dictation.ON_DEVICE,))   # Linux: no choice of way
    assert window.dictation_way is None and window.dictation_model.isEnabled()
    window, _chosen = dictation_window((dictation.SYSTEM, dictation.ON_DEVICE))   # Windows
    titles = [window.dictation_way.itemText(i) for i in range(window.dictation_way.count())]
    assert titles == ["Windows Voice Typing", "On This Computer"]
    assert not window.dictation_model.isEnabled()          # Voice typing has no model here


def test_choosing_the_way_the_model_and_the_language():
    import dictation
    from dictation import models
    window, chosen = dictation_window((dictation.SYSTEM, dictation.ON_DEVICE))
    window.dictation_way.activated.emit(1)
    assert chosen[-1].way == dictation.ON_DEVICE and window.dictation_model.isEnabled()
    models_offered = [window.dictation_model.itemText(i)
                      for i in range(window.dictation_model.count())]
    assert models_offered == ["Standard (161 MB)", "More accurate (376 MB)"]
    window.dictation_model.activated.emit(1)
    assert chosen[-1].model == models.ACCURATE
    language = window.dictation_language
    assert language.itemText(0) == "Automatic" and language.currentData() == "de"
    language.activated.emit(language.findData("fr"))
    assert chosen[-1].language == "fr" and chosen[-1].model == models.ACCURATE
    assert unnamed_controls(window) == []


def test_a_downloaded_model_can_be_removed(tmp_path, monkeypatch):
    import alerts
    import dictation
    from dictation import models
    monkeypatch.setenv("HOME", str(tmp_path))
    model = models.MODELS[models.STANDARD]
    for file in models.files_of(model):
        os.makedirs(os.path.dirname(models.path_of(file)), exist_ok=True)
        with open(models.path_of(file), "wb") as f:
            f.write(b"\0" * file.size)
    asked = []
    monkeypatch.setattr(alerts, "confirm_destructive",
                        lambda *a, **k: asked.append(k["title"]) or True)
    window, _chosen = dictation_window((dictation.ON_DEVICE,))
    assert window.dictation_status.text() == "Downloaded."
    assert window.dictation_remove.isVisibleTo(window)
    window.dictation_remove.click()
    assert asked == ["Remove the speech model?"] and not models.installed(model)
    assert window.dictation_status.text() == "Downloaded the first time you dictate."
    assert not window.dictation_remove.isVisibleTo(window)
