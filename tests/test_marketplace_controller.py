"""Controller orchestration: catalog merge, install state, uninstall."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import List

import pytest

from plugin_marketplace import installer as installer_mod
from plugin_marketplace.controller import MarketplaceController
from plugin_marketplace.models import PluginListing
from plugin_marketplace import registry as registry_mod
from plugin_marketplace.registry import FetchResult, RegistrySource
from plugin_system import PluginInfo, PluginSettingsStore


class FakeHost:
    def __init__(self) -> None:
        self.loaded: List[PluginInfo] = []
        self.reloaded: List[Path] = []
        self.unloaded: List[str] = []

    def reload_plugin(self, folder: Path):
        self.reloaded.append(folder)
        # Simulate the host's reload behaviour for the test:
        self.loaded.append(PluginInfo(
            plugin_id=folder.name,
            name=folder.name,
            version="1.0.0",
            folder=folder,
            source="user",
        ))

    def unload_plugin(self, plugin_id: str) -> bool:
        before = len(self.loaded)
        self.loaded = [p for p in self.loaded if p.plugin_id != plugin_id]
        self.unloaded.append(plugin_id)
        return len(self.loaded) != before

    @property
    def loaded_plugins(self):
        return self.loaded


@pytest.fixture
def settings(tmp_path) -> PluginSettingsStore:
    return PluginSettingsStore(path=tmp_path / "settings.json")


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


@pytest.fixture
def controller(host, settings) -> MarketplaceController:
    return MarketplaceController(host=host, settings=settings)


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


# ──────────────────────────────────────────────────────────────────────
# Catalog merge
# ──────────────────────────────────────────────────────────────────────

def test_refresh_merges_results_first_seen_wins(monkeypatch, controller):
    def fake_fetch_all(sources):
        return [
            FetchResult(source=sources[0], listings=[
                _listing("a"), _listing("b", name="from-primary"),
            ]),
            FetchResult(source=sources[0], listings=[
                _listing("b", name="from-secondary"),
                _listing("c"),
            ]),
        ]
    monkeypatch.setattr(registry_mod, "fetch_all_indices", fake_fetch_all)
    monkeypatch.setattr(
        "plugin_marketplace.controller.fetch_all_indices",
        fake_fetch_all,
    )
    snapshot = controller.refresh_catalog()
    by_id = {li.plugin_id: li for li in snapshot.listings}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["b"].name == "from-primary"
    assert snapshot.fetch_errors == {}


def test_refresh_records_per_registry_errors(monkeypatch, controller):
    def fake_fetch_all(sources):
        return [
            FetchResult(
                source=RegistrySource(name="dead", index_url="https://x"),
                listings=[],
                error="connection refused",
            ),
            FetchResult(
                source=RegistrySource(name="live", index_url="https://y"),
                listings=[_listing("a")],
            ),
        ]
    monkeypatch.setattr(
        "plugin_marketplace.controller.fetch_all_indices",
        fake_fetch_all,
    )
    snapshot = controller.refresh_catalog()
    assert [li.plugin_id for li in snapshot.listings] == ["a"]
    assert "dead" in snapshot.fetch_errors


# ──────────────────────────────────────────────────────────────────────
# Installed state
# ──────────────────────────────────────────────────────────────────────

def test_is_installed_reads_host(controller, host):
    host.loaded.append(PluginInfo("a", "A", "1.0", Path("/x"), "user"))
    assert controller.is_installed("a") is True
    assert controller.is_installed("b") is False


def test_has_update_returns_true_for_newer_listing(controller, host):
    host.loaded.append(PluginInfo("a", "A", "1.0.0", Path("/x"), "user"))
    assert controller.has_update(_listing("a", version="1.0.1")) is True
    assert controller.has_update(_listing("a", version="1.0.0")) is False
    assert controller.has_update(_listing("a", version="0.9.0")) is False


def test_is_bundled_routes_to_host(controller, host):
    host.loaded.append(PluginInfo("a", "A", "1.0", Path("/x"), "bundled"))
    host.loaded.append(PluginInfo("b", "B", "1.0", Path("/y"), "user"))
    assert controller.is_bundled("a") is True
    assert controller.is_bundled("b") is False


# ──────────────────────────────────────────────────────────────────────
# Install / Uninstall
# ──────────────────────────────────────────────────────────────────────

def test_install_then_hot_reload(monkeypatch, controller, host, tmp_path):
    payload = _build_zip("acme")
    sha = hashlib.sha256(payload).hexdigest()

    def fake_download(url, out_path, *, progress_cb=None):
        out_path.write_bytes(payload)
    monkeypatch.setattr(installer_mod, "_download", fake_download)

    listing = _listing("acme", sha256=sha)

    # The real installer writes to user_plugins_dir; redirect to tmp.
    monkeypatch.setattr(
        "plugin_marketplace.installer.user_plugins_dir",
        lambda: tmp_path,
    )
    result = controller.install(listing)
    assert result.install_path == (tmp_path / "acme").resolve()
    controller.hot_reload(result.install_path)
    assert host.reloaded == [result.install_path]


def test_uninstall_unloads_then_removes_folder(monkeypatch, controller, host, tmp_path):
    folder = tmp_path / "gone"
    folder.mkdir()
    (folder / "manifest.json").write_text("{}")
    host.loaded.append(PluginInfo("gone", "Gone", "1.0", folder, "user"))
    monkeypatch.setattr(
        "plugin_marketplace.installer.user_plugins_dir",
        lambda: tmp_path,
    )
    assert controller.uninstall("gone") is True
    assert "gone" in host.unloaded
    assert not folder.exists()


def test_installed_listings_read_manifest_fields(controller, host, tmp_path):
    """Installed listings should carry rich manifest data (author,
    description, license, tags) so the UI doesn't render placeholders."""
    folder = tmp_path / "rich"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "rich",
        "name": "Rich Plugin",
        "version": "1.2.3",
        "api_version": 1,
        "author": "Alice",
        "description": "A short description.",
        "long_description": "<p>Long body</p>",
        "homepage": "https://example.com/rich",
        "license": "MIT",
        "category": "editor",
        "tags": ["a", "b"],
        "lightning_address": "alice@example.com",
    }))
    host.loaded.append(PluginInfo("rich", "Rich Plugin", "1.2.3", folder, "user"))

    listings = controller.installed_listings()
    assert len(listings) == 1
    li = listings[0]
    assert li.author == "Alice"
    assert li.description == "A short description."
    assert li.long_description == "<p>Long body</p>"
    assert li.homepage == "https://example.com/rich"
    assert li.license == "MIT"
    assert li.category == "editor"
    assert li.tags == ("a", "b")
    assert li.lightning_address == "alice@example.com"
    assert li.source_name == "user"


def test_installed_listings_fall_back_when_manifest_missing(controller, host, tmp_path):
    """If a manifest can't be read, the listing still appears with a
    sensible default description so the user isn't staring at a blank row."""
    host.loaded.append(PluginInfo("no_manifest", "No Manifest", "1.0.0", tmp_path / "ghost", "bundled"))
    listings = controller.installed_listings()
    assert len(listings) == 1
    assert listings[0].description == "Bundled with the editor."


def test_installed_listing_prefers_catalog_when_available(monkeypatch, controller, host, tmp_path):
    """If we have a catalog entry for an installed plugin, use that
    listing instead of synthesizing from the manifest."""
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "p", "name": "From Manifest", "version": "1.0.0", "api_version": 1,
    }))
    host.loaded.append(PluginInfo("p", "From Manifest", "1.0.0", folder, "user"))
    # Inject a catalog entry.
    from plugin_marketplace.controller import CatalogSnapshot
    controller._catalog = CatalogSnapshot(listings=[
        _listing("p", name="From Catalog", description="Catalog wins"),
    ])
    listings = controller.installed_listings()
    assert listings[0].name == "From Catalog"
    assert listings[0].description == "Catalog wins"


def test_uninstall_unloads_even_when_folder_missing(monkeypatch, controller, host, tmp_path):
    """If the user already deleted the folder by hand, uninstall must
    still unload the running plugin."""
    host.loaded.append(PluginInfo("phantom", "P", "1.0", tmp_path / "phantom", "user"))
    monkeypatch.setattr(
        "plugin_marketplace.installer.user_plugins_dir",
        lambda: tmp_path,
    )
    controller.uninstall("phantom")
    assert "phantom" in host.unloaded


# ──────────────────────────────────────────────────────────────────────
# Registry source persistence
# ──────────────────────────────────────────────────────────────────────

def test_default_registry_always_present(controller):
    sources = controller.registry_sources()
    assert sources[0].name.startswith("my-editor")


def test_user_added_source_is_persisted(controller):
    sources = controller.registry_sources()
    sources.append(RegistrySource(
        name="custom", index_url="https://custom.example/idx.json",
    ))
    controller.set_registry_sources(sources)
    refreshed = controller.registry_sources()
    names = [s.name for s in refreshed]
    assert "custom" in names
    # Default still first.
    assert refreshed[0].name.startswith("my-editor")


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _build_zip(plugin_id: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{plugin_id}/manifest.json", json.dumps({
            "id": plugin_id, "name": plugin_id, "version": "1.0.0", "api_version": 1,
        }))
        zf.writestr(f"{plugin_id}/__init__.py", "def register(api): pass\n")
    return buf.getvalue()
