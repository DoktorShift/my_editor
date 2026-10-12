# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Drafts panel with its second view, Published.

What must hold: the panel shows Drafts and Published, and the switch
between them never cuts a word, in English and German, at each system's
own text size and up to 200 percent of it, in the fonts the three
systems draw it with: macOS's system font, Windows' Segoe UI (Verdana,
which is wider, where there is no Segoe UI) and DejaVu Sans, the
interface font of many Linux systems (bundled for the test,
tests/fonts/). At the systems' own size band 1 stays on one line and
the panel keeps its narrowest width; larger, the panel asks for no more
than the room a view's name needs; the buttons stay inside it. The
panel's action menu sits in band 1, opens on Return and Enter too, and
holds Refresh (the view shown reads again, named for it), Draft Relays…
(only with an account, asking the host for it) and, in the Drafts view,
Select All and Delete; Tab reaches the switch on the view shown; every
control has a name.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import i18n  # noqa: E402
from nostr.profiles import Profile  # noqa: E402
from nostr.published import PublishedList  # noqa: E402
from nostr.ui.drafts_panel import DEFAULT_PANEL_WIDTH, MIN_PANEL_WIDTH, DraftsPanel  # noqa: E402
from nostr.ui.published_view import PANEL_VIEW, PublishedView, add_to_panel  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    PK, FakeClient, FakePool, FakeRelayDirectory, FakeSessionPool,
)
from tests.widget_lifetime import delete_new_windows  # noqa: E402

FONTS = pathlib.Path(__file__).resolve().parent / "fonts"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _windows_deleted():
    yield from delete_new_windows()


@pytest.fixture
def app_font():
    """The application font is process-wide: put it back after the test."""
    app = QApplication.instance()
    original = app.font()
    yield app
    app.setFont(original)


@pytest.fixture
def language():
    """Show the texts in a language for the test, and English again after."""
    yield i18n.install
    i18n.install("en")


_LOADED = {}


def dejavu_sans() -> str:
    """DejaVu Sans, from the fixture (once per run)."""
    if "dejavu" not in _LOADED:
        loaded = QFontDatabase.addApplicationFont(str(FONTS / "DejaVuSans-subset.ttf"))
        families = QFontDatabase.applicationFontFamilies(loaded) if loaded >= 0 else []
        _LOADED["dejavu"] = families[0] if families else ""
    return _LOADED["dejavu"]


def system_font(system: str):
    """(family, its usual interface size in points), or None where this
    computer does not have the font."""
    families = set(QFontDatabase.families())
    if system == "macOS":
        return (".AppleSystemUIFont", 13.0) if ".AppleSystemUIFont" in families else None
    if system == "Windows":
        family = next((f for f in ("Segoe UI", "Verdana") if f in families), None)
        return (family, 9.0) if family else None
    family = dejavu_sans()
    return (family, 10.0) if family else None


def use_font(app_font, system: str, scale: float) -> None:
    found = system_font(system)
    if found is None:
        pytest.skip(f"no {system} interface font on this computer")
    family, size = found
    font = QFont(family)
    font.setPointSizeF(size * scale)
    app_font.setFont(font)


def alice() -> Profile:
    return Profile(user_pubkey=PK, bunker_pubkey="b" * 64, bunker_relays=["wss://relay.example"],
                   local_secret_hex="c" * 64, display_name="Alice")


def published_view() -> PublishedView:
    model = PublishedList(relay_pool=FakePool(), relay_directory=FakeRelayDirectory({}),
                          session_pool=FakeSessionPool(FakeClient()))
    return PublishedView(model, is_dark=True)


def panel_with_published(width: int, view_key: str = "drafts"):
    panel = DraftsPanel(is_dark=True)
    view = published_view()
    add_to_panel(panel, view)
    panel.show_view(view_key)
    panel.resize(width, 600)
    panel.show()
    panel.layout().activate()
    return panel, view


def painted_words(panel) -> list:
    switch = panel._view_switch
    if switch.is_popup():
        return [switch.popup.painted_text()]
    return [segment.painted_text() for segment in switch.segments]


def titles(panel) -> list:
    return [title for _key, title, _hint in panel._views]


def current_title(panel) -> str:
    return dict((key, title) for key, title, _hint in panel._views)[panel.current_view()]


# --------------------------------------------------------------------------- #
# The switch: Drafts | Published                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("view_key", ["drafts", PANEL_VIEW])
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
@pytest.mark.parametrize("width", [MIN_PANEL_WIDTH, DEFAULT_PANEL_WIDTH])
@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("system", ["macOS", "Windows", "Linux"])
def test_the_switch_never_cuts_a_word(app_font, language, system, lang, width, scale,
                                      view_key):
    language(lang)
    use_font(app_font, system, scale)
    panel, _view = panel_with_published(width, view_key)
    try:
        switch = panel._view_switch
        wanted = [current_title(panel)] if switch.is_popup() else titles(panel)
        assert painted_words(panel) == wanted, (
            f"{system} {lang} at {scale:.0%} on {width}px: {painted_words(panel)}")
        if scale == 1.0:
            # At the system's own size, band 1 is one line at every width,
            # and no name makes the panel wider.
            assert not panel._top_band.is_stacked()
            assert panel.minimumWidth() == MIN_PANEL_WIDTH
        assert panel.minimumWidth() <= max(MIN_PANEL_WIDTH, panel._top_band.popup_room())
        right = panel._close_btn.mapTo(panel, panel._close_btn.rect().topRight()).x()
        assert right <= panel.width()
    finally:
        panel.hide()


def test_the_views_are_named_in_german(language):
    language("de")
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        assert panel.views() == ["drafts", PANEL_VIEW]
        assert titles(panel) == ["Entwürfe", "Veröffentlicht"]
    finally:
        panel.hide()


# --------------------------------------------------------------------------- #
# The panel's action menu (review N9, N11)                                    #
# --------------------------------------------------------------------------- #

def test_the_action_menu_holds_refresh_draft_relays_and_the_bulk_actions():
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        panel._update_actions_menu()
        shown = [a.text() for a in panel._actions_btn.menu().actions()
                 if a.isVisible() and not a.isSeparator()]
        assert shown == ["Refresh Drafts", "Draft Relays…", "Select All Drafts",
                         "Delete Draft"]
        assert not panel._select_all_action.isEnabled()          # no drafts listed
        assert not panel._delete_selected_action.isEnabled()     # none selected
        panel.show_view(PANEL_VIEW)                              # drafts' own: not here
        shown = [a.text() for a in panel._actions_btn.menu().actions()
                 if a.isVisible() and not a.isSeparator()]
        assert shown == ["Refresh Published Notes and Articles", "Draft Relays…"]
    finally:
        panel.hide()


def test_draft_relays_is_offered_only_with_an_account():
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        asked = []
        panel.draft_relays_requested.connect(lambda: asked.append(True))
        action = panel._draft_relays_action
        assert action in panel._actions_btn.menu().actions()
        assert action.text() == "Draft Relays…" and not action.isEnabled()
        panel.set_active_profile(alice())
        assert action.isEnabled()
        action.trigger()
        assert asked == [True]
        panel.set_active_profile(None)
        assert not action.isEnabled()
    finally:
        panel.hide()


def test_the_action_menu_sits_with_the_panel_wide_controls_in_band_1():
    # Beside the search field it read as a search option (review N9).
    panel, _view = panel_with_published(MIN_PANEL_WIDTH)
    try:
        assert panel._actions_btn.accessibleName() == "Actions"
        assert panel._top_band.isAncestorOf(panel._actions_btn)
        assert not panel._top_band.is_stacked()                  # band 1 on one line
        actions = panel._actions_btn.mapTo(panel, panel._actions_btn.rect().topRight())
        close = panel._close_btn.mapTo(panel, panel._close_btn.rect().topLeft())
        assert actions.x() <= close.x() and actions.y() == close.y()
    finally:
        panel.hide()


def test_return_and_enter_open_the_action_menu():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    opened = []
    panel._actions_btn.showMenu = lambda: opened.append(True)   # no modal loop in a test
    try:
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QTest.keyClick(panel._actions_btn, key)
        assert opened == [True, True]
    finally:
        panel.hide()


# --------------------------------------------------------------------------- #
# Refresh                                                                      #
# --------------------------------------------------------------------------- #

def test_refresh_reads_the_view_shown_again_and_is_named_for_it():
    panel, view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        drafts_refreshed, published_refreshed = [], []
        panel.refresh_requested.connect(lambda: drafts_refreshed.append(True))
        view.model.refresh = lambda: published_refreshed.append(True)
        refresh = panel._refresh_action
        assert refresh.text() == "Refresh Drafts"
        refresh.trigger()
        assert (drafts_refreshed, published_refreshed) == ([True], [])
        panel.show_view(PANEL_VIEW)
        assert refresh.text() == "Refresh Published Notes and Articles"
        refresh.trigger()
        assert (drafts_refreshed, published_refreshed) == ([True], [True])
        panel.show_view("drafts")
        assert refresh.text() == "Refresh Drafts"
    finally:
        panel.hide()


# --------------------------------------------------------------------------- #
# The switch and the keyboard (review N10)                                    #
# --------------------------------------------------------------------------- #

def test_tab_reaches_the_switch_on_the_view_shown():
    from PySide6.QtCore import Qt
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        drafts, published = panel._view_switch.segments
        assert drafts.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert published.focusPolicy() == Qt.FocusPolicy.ClickFocus
        panel.show_view(PANEL_VIEW)                              # by the host, or a click
        assert published.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert drafts.focusPolicy() == Qt.FocusPolicy.ClickFocus
        if panel._view_switch.is_popup():
            return                                               # no segments to reach
        panel.activateWindow()
        panel._actions_btn.setFocus(Qt.FocusReason.OtherFocusReason)
        panel.focusNextPrevChild(False)                          # Shift-Tab, back to it
        assert panel.focusWidget() is published
    finally:
        panel.hide()


def test_every_control_has_a_name_in_both_views():
    panel, _view = panel_with_published(DEFAULT_PANEL_WIDTH)
    try:
        panel.set_active_profile(alice())
        assert unnamed_controls(panel) == []
        panel.show_view(PANEL_VIEW)
        assert unnamed_controls(panel) == []
    finally:
        panel.hide()
