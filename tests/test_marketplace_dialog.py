"""State-machine tests for MarketplaceDialog.

The dialog has a single ``_render()`` path that decides which empty-
state card to show based on the catalog, filters, and section. These
tests poke that decision logic without running real workers.

We construct a real ``MarketplaceDialog`` with a stubbed MainWindow.
That requires Qt but no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget

from plugin_marketplace.controller import CatalogSnapshot
from plugin_marketplace.models import PluginListing
from plugin_marketplace.ui.marketplace_dialog import MarketplaceDialog, Section
from plugin_marketplace.ui import empty_state as es
from plugin_system import PluginInfo, PluginSettingsStore
from plugin_system.loader import LoadReport


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class StubMainWindow(QWidget):
    """Minimal QWidget that satisfies MarketplaceDialog's parent
    requirement and the small slice of the MainWindow API it reads."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self._plugin_settings = PluginSettingsStore(path=tmp_path / "settings.json")
        self._plugin_report = LoadReport()
        self.reloaded: List[Path] = []
        self.unloaded: List[str] = []
        self.statuses: List[str] = []

    def reload_plugin(self, folder):
        self.reloaded.append(folder)

    def unload_plugin(self, pid):
        self.unloaded.append(pid)
        return True

    def editor_show_status(self, msg, timeout_ms=4000):
        self.statuses.append(msg)

    def open_plugin_log_dialog(self):
        pass


@pytest.fixture
def stub_mw(tmp_path, qapp):
    return StubMainWindow(tmp_path)


@pytest.fixture
def dialog(stub_mw):
    # The dialog auto-starts a refresh in __init__. Force the fetch
    # state to "done" so render() doesn't pick the loading card.
    dlg = MarketplaceDialog(stub_mw)
    dlg._fetch_in_flight = False
    dlg._first_fetch_done = True
    return dlg


def _listing(plugin_id: str, **kw) -> PluginListing:
    base = dict(
        plugin_id=plugin_id,
        name=plugin_id.title(),
        version="1.0.0",
        download_url="https://e/p.zip",
        sha256="a" * 64,
        source_name="default",
    )
    base.update(kw)
    return PluginListing(**base)


def _holder_card(dlg: MarketplaceDialog):
    """Return the EmptyStateCard currently inside the empty holder, if any."""
    layout = dlg._empty_holder_layout
    if layout.count() == 0:
        return None
    return layout.itemAt(0).widget()


def _is_empty_state_visible(dlg: MarketplaceDialog) -> bool:
    return dlg._content_stack.currentWidget() is dlg._empty_holder


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────

def test_loading_state_shown_on_first_fetch(stub_mw):
    """During the very first fetch, a loading card is visible."""
    dlg = MarketplaceDialog(stub_mw)
    # During __init__ we kick off the refresh; first render happens
    # synchronously and should show the loading card.
    assert _is_empty_state_visible(dlg)
    card = _holder_card(dlg)
    assert card is not None


def test_discover_empty_catalog_shows_published_yet_card(dialog):
    """Discover, no catalog, no errors -> "No plugins published yet"."""
    dialog._controller._catalog = CatalogSnapshot()
    dialog._render()
    assert _is_empty_state_visible(dialog)


def test_discover_with_fetch_error_shows_unreachable_card(dialog):
    """Discover, no listings, registry errored -> "registry unreachable"."""
    dialog._controller._catalog = CatalogSnapshot(
        listings=[],
        fetch_errors={"my-editor official": "Couldn't find the registry's address."},
    )
    dialog._render()
    assert _is_empty_state_visible(dialog)


def test_discover_with_listings_shows_master_detail(dialog):
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("a"), _listing("b")],
    )
    dialog._render()
    assert not _is_empty_state_visible(dialog)
    assert dialog._list.count() == 2


def test_search_no_matches_shows_clear_filters_card(dialog):
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("alpha"), _listing("beta")],
    )
    dialog._search.setText("zzzzz")  # triggers _render via signal
    assert _is_empty_state_visible(dialog)


def test_installed_section_empty_shows_no_installed_card(dialog):
    dialog._select_section(Section.INSTALLED)
    assert _is_empty_state_visible(dialog)


def test_installed_section_with_plugin_shows_list(dialog, stub_mw):
    stub_mw._plugin_report.loaded.append(
        PluginInfo("hello", "Hello", "1.0", Path("/x"), "bundled")
    )
    dialog._select_section(Section.INSTALLED)
    assert not _is_empty_state_visible(dialog)
    assert dialog._list.count() == 1


def test_updates_section_when_nothing_updateable(dialog, stub_mw):
    stub_mw._plugin_report.loaded.append(
        PluginInfo("hello", "Hello", "1.0.0", Path("/x"), "user"),
    )
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("hello", version="1.0.0")],
    )
    dialog._select_section(Section.UPDATES)
    assert _is_empty_state_visible(dialog)


def test_updates_section_when_updates_available(dialog, stub_mw):
    stub_mw._plugin_report.loaded.append(
        PluginInfo("hello", "Hello", "1.0.0", Path("/x"), "user"),
    )
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("hello", version="1.0.1")],
    )
    dialog._select_section(Section.UPDATES)
    assert not _is_empty_state_visible(dialog)
    assert dialog._list.count() == 1
    # Updates badge in the section button reflects the count.
    assert "1" in dialog._btn_updates.text()


def test_filter_strip_hidden_outside_discover(dialog):
    """Filter chips are Discover-only; switching sections hides them.

    We test ``isVisibleTo(master_detail)`` because the filter strip
    lives inside the master-detail pane; that isolates the
    setVisible() call we care about from the content stack page
    swap.
    """
    md = dialog._master_detail
    assert dialog._filter_strip.isVisibleTo(md)
    dialog._select_section(Section.INSTALLED)
    assert not dialog._filter_strip.isVisibleTo(md)
    dialog._select_section(Section.DISCOVER)
    assert dialog._filter_strip.isVisibleTo(md)


def test_clear_filters_empty_state_action_resets_state(dialog):
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("alpha")],
    )
    dialog._search.setText("nomatch")
    dialog._chip_free.setChecked(True)
    dialog._free_only = True
    dialog._handle_empty_action(es.ACTION_CLEAR_FILTERS)
    assert dialog._search.text() == ""
    assert dialog._free_only is False
    assert dialog._chip_free.isChecked() is False


def test_partial_fetch_error_shows_inline_banner(dialog):
    """When *some* listings come back but a registry errored, the
    inline banner appears above the list (not the empty state card)."""
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("a")],
        fetch_errors={"community": "Couldn't reach the registry."},
    )
    dialog._render()
    assert not _is_empty_state_visible(dialog)
    assert dialog._error_panel.isVisibleTo(dialog)


def test_partial_fetch_error_hidden_outside_discover(dialog, stub_mw):
    """The inline error banner is only useful on Discover."""
    stub_mw._plugin_report.loaded.append(
        PluginInfo("a", "A", "1.0", Path("/x"), "user")
    )
    dialog._controller._catalog = CatalogSnapshot(
        listings=[_listing("a")],
        fetch_errors={"community": "Couldn't reach the registry."},
    )
    dialog._select_section(Section.INSTALLED)
    assert not dialog._error_panel.isVisibleTo(dialog)
