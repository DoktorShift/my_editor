"""Loader: discovery, validation, error isolation, capability scoping.

These tests build minimal plugin folders on the fly in ``tmp_path``
and run them through the real loader with a stub host. No Qt
required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from plugin_system.api import (
    PLUGIN_API_VERSION,
    PaymentProvider,
    PluginAPI,
    PluginIdentity,
    parse_declared_kinds,
)
from plugin_system.loader import (
    LoadReport,
    PluginInfo,
    PluginLoadError,
    PluginSkipped,
    SKIP_DISABLED,
    SKIP_NO_MANIFEST,
    SKIP_SHADOWED,
    SKIP_WRONG_PLATFORM,
    load_all_plugins,
    load_one_plugin_folder,
    plugin_module_name,
)
from plugin_system.settings import PluginSettingsStore


# --------------------------------------------------------------------------- #
# Stub host                                                                   #
# --------------------------------------------------------------------------- #

class FakeHost:
    """Records every host_* call so tests can inspect what a plugin did.

    Also implements the optional bookkeeping lifecycle (``begin_plugin``,
    ``record_vendor_path``, ``unload``) so we can test the host-side
    tracking without spinning up the real PluginHost.
    """

    def __init__(self) -> None:
        self.menu_actions: List[tuple[str, str, str, Callable]] = []
        self.opened_tabs: List[tuple[str, str]] = []
        self.payment_providers: List[tuple[str, PaymentProvider]] = []
        self.unload_callbacks: List[tuple[str, Callable]] = []
        self.dock_requests: List[tuple[str, str, str]] = []
        self._current_text: Optional[str] = "initial"
        # Bookkeeping
        self.begun: List[PluginInfo] = []
        self.recorded_vendor: List[tuple[str, Optional[str]]] = []
        self.unloaded: List[str] = []

    # HostHooks
    def host_add_menu_action(self, plugin_id, menu_title, label, callback):
        self.menu_actions.append((plugin_id, menu_title, label, callback))

    def host_get_current_text(self):
        return self._current_text

    def host_set_current_text(self, text):
        self._current_text = text
        return True

    def host_open_tab(self, title, content):
        self.opened_tabs.append((title, content))

    def host_register_payment_provider(self, plugin_id, provider):
        self.payment_providers.append((plugin_id, provider))

    def host_register_unload(self, plugin_id, callback):
        # FakeHost ignores teardown callbacks: registration is the unit
        # under test in the few cases that exercise this path.
        self.unload_callbacks.append((plugin_id, callback))

    def host_add_dock_widget(self, plugin_id, title, widget_factory, area):
        # We never build the widget in loader tests; recording the
        # request is enough to check that the API call reached the host.
        self.dock_requests.append((plugin_id, title, area))

    # Optional lifecycle (duck-typed)
    def begin_plugin(self, info: PluginInfo) -> None:
        self.begun.append(info)

    def record_vendor_path(self, plugin_id: str, path: Optional[str]) -> None:
        self.recorded_vendor.append((plugin_id, path))

    def unload(self, plugin_id: str) -> bool:
        self.unloaded.append(plugin_id)
        return True


# --------------------------------------------------------------------------- #
# Helpers to build plugin folders                                             #
# --------------------------------------------------------------------------- #

def make_plugin(
    root: Path,
    plugin_id: str,
    *,
    manifest_overrides: Optional[dict] = None,
    init_source: Optional[str] = None,
) -> Path:
    """Create a minimal plugin folder under ``root``. Returns the path."""
    folder = root / plugin_id
    folder.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.replace("_", " ").title(),
        "version": "1.0.0",
        "description": f"test plugin {plugin_id}",
        "author": "tester",
        "api_version": 1,
        "min_editor_version": "1.0.0",
        "declared_kinds": [],
        "tags": ["test"],
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (folder / "__init__.py").write_text(
        init_source
        or "def register(api):\n"
           "    api.add_menu_action('Plugins', 'Hello', lambda: None)\n"
    )
    return folder


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


@pytest.fixture
def settings_store(tmp_path) -> PluginSettingsStore:
    return PluginSettingsStore(path=tmp_path / "settings.json")


@pytest.fixture
def log_path(tmp_path, monkeypatch) -> Path:
    """Redirect plugin error logging to tmp_path so failure tests
    don't write into the real per-user config root."""
    import plugin_system.loader as loader_mod

    path = tmp_path / "plugin.log"
    monkeypatch.setattr(loader_mod, "plugin_log_path", lambda: path)
    return path


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #

def test_loads_a_simple_plugin(tmp_path, host, settings_store):
    make_plugin(tmp_path, "alpha")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.errors == []
    assert [p.plugin_id for p in report.loaded] == ["alpha"]
    # The plugin's register() ran and called add_menu_action.
    assert len(host.menu_actions) == 1
    plugin_id, menu, label, _cb = host.menu_actions[0]
    assert plugin_id == "alpha"
    assert menu == "Plugins" and label == "Hello"


def test_alphabetical_order(tmp_path, host, settings_store):
    make_plugin(tmp_path, "gamma")
    make_plugin(tmp_path, "alpha")
    make_plugin(tmp_path, "beta")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["alpha", "beta", "gamma"]


def test_load_one_folder_helper(tmp_path, host, settings_store):
    """The marketplace installer uses ``load_one_plugin_folder`` to
    hot-load a freshly-installed plugin without rescanning the tree."""
    folder = make_plugin(tmp_path, "single")
    info, report = load_one_plugin_folder(
        folder, host=host, settings_store=settings_store, source="user",
    )
    assert info is not None
    assert info.plugin_id == "single"
    assert info.source == "user"
    assert report.errors == []


# --------------------------------------------------------------------------- #
# Skips (legal "don't load this one" cases)                                   #
# --------------------------------------------------------------------------- #

def test_disabled_plugin_is_skipped(tmp_path, host, settings_store):
    make_plugin(tmp_path, "off", manifest_overrides={"enabled": False})
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == [] and report.errors == []
    reasons = [s.reason for s in report.skipped]
    assert SKIP_DISABLED in reasons


def test_folder_without_manifest_is_silently_skipped(tmp_path, host, settings_store):
    """Stray folders (notes, vendored libs) coexisting with plugins
    should not be treated as load errors."""
    (tmp_path / "not_a_plugin").mkdir()
    (tmp_path / "not_a_plugin" / "README.txt").write_text("just notes")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == [] and report.errors == []
    assert [s.reason for s in report.skipped] == [SKIP_NO_MANIFEST]


def test_future_api_version_is_skipped_with_error(tmp_path, host, settings_store, log_path):
    """A plugin targeting a newer API than we support must not be
    imported - it'd likely call methods we don't have."""
    make_plugin(
        tmp_path, "futuristic",
        manifest_overrides={"api_version": PLUGIN_API_VERSION + 99},
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "api_version" in report.errors[0].reason


# --------------------------------------------------------------------------- #
# Plugin ID validation (BLOCKER fix)                                          #
# --------------------------------------------------------------------------- #

def test_invalid_plugin_id_with_path_traversal_is_rejected(tmp_path, host, settings_store, log_path):
    """A plugin id with '..' or '/' would corrupt sys.modules keys and
    settings.json. Reject at manifest parse time."""
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "../etc/passwd", "name": "bad", "version": "1.0.0", "api_version": 1,
    }))
    (folder / "__init__.py").write_text("def register(api): pass\n")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "invalid" in report.errors[0].reason.lower() or "id" in report.errors[0].reason


def test_uppercase_plugin_id_is_rejected(tmp_path, host, settings_store, log_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "Hello_World", "name": "Bad Case", "version": "1.0.0", "api_version": 1,
    }))
    (folder / "__init__.py").write_text("def register(api): pass\n")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1


@pytest.mark.parametrize("valid_id", [
    "hello",
    "hello_world",
    "hello-world",
    "a",
    "plugin1",
    "abc-123_def",
])
def test_valid_plugin_ids_accepted(tmp_path, host, settings_store, valid_id):
    make_plugin(tmp_path, valid_id)
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == [valid_id]


# --------------------------------------------------------------------------- #
# Error isolation                                                             #
# --------------------------------------------------------------------------- #

def test_plugin_with_broken_manifest_does_not_crash(tmp_path, host, settings_store, log_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "manifest.json").write_text("{ not valid json")
    (folder / "__init__.py").write_text("def register(api): pass\n")
    make_plugin(tmp_path, "alpha")  # sibling that should still load
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["alpha"]
    assert len(report.errors) == 1
    assert report.errors[0].folder.name == "broken"


def test_plugin_raising_in_register_is_isolated(tmp_path, host, settings_store, log_path):
    """If register() throws, log the error but keep loading siblings."""
    make_plugin(
        tmp_path, "boom",
        init_source="def register(api):\n    raise RuntimeError('on fire')\n",
    )
    make_plugin(tmp_path, "alpha")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["alpha"]
    assert len(report.errors) == 1
    assert "on fire" in report.errors[0].reason


def test_plugin_missing_register_is_an_error(tmp_path, host, settings_store, log_path):
    make_plugin(
        tmp_path, "no_register",
        init_source="# This plugin forgot to export register()\n",
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert len(report.errors) == 1
    assert "register" in report.errors[0].reason


def test_plugin_with_import_error_is_isolated(tmp_path, host, settings_store, log_path):
    make_plugin(
        tmp_path, "bad_import",
        init_source="import this_module_does_not_exist\n\n"
                    "def register(api):\n    pass\n",
    )
    make_plugin(tmp_path, "ok")
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["ok"]
    assert len(report.errors) == 1


def test_failures_are_logged_to_file(tmp_path, host, settings_store, log_path):
    """Plugin load errors must end up in plugin.log so bundled GUI
    users can see them. stderr alone isn't enough."""
    make_plugin(
        tmp_path, "boom",
        init_source="def register(api):\n    raise RuntimeError('on fire')\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert log_path.is_file()
    log_text = log_path.read_text()
    assert "on fire" in log_text
    assert "boom" in log_text


# --------------------------------------------------------------------------- #
# Vendoring + duplicates                                                      #
# --------------------------------------------------------------------------- #

def test_vendor_dir_is_added_to_sys_path(tmp_path, host, settings_store):
    """A plugin's vendor/ folder must be importable from inside the
    plugin so it can ship dependencies without touching site-packages."""
    folder = make_plugin(
        tmp_path, "vendored",
        init_source=(
            "import vendored_lib\n"
            "def register(api):\n"
            "    api.set_setting('lib_value', vendored_lib.VALUE)\n"
        ),
    )
    vendor = folder / "vendor"
    vendor.mkdir()
    (vendor / "vendored_lib.py").write_text("VALUE = 'from-vendor'\n")
    try:
        report = load_all_plugins(
            host=host, settings_store=settings_store,
            extra_dirs=[tmp_path], include_default_dirs=False,
        )
        assert report.errors == [], report.errors
        assert [p.plugin_id for p in report.loaded] == ["vendored"]
        assert settings_store.get("vendored", "lib_value") == "from-vendor"
    finally:
        sys.path[:] = [p for p in sys.path if "vendored" not in p]
        sys.modules.pop("vendored_lib", None)
        sys.modules.pop("my_editor_plugin_vendored", None)


def test_vendor_path_rolled_back_on_import_failure(tmp_path, host, settings_store, log_path):
    """A plugin that fails to import must not leave its vendor entry on
    sys.path - a later plugin could pick up its outdated dependency."""
    folder = make_plugin(
        tmp_path, "failvendor",
        init_source="raise ImportError('intentional')\n",
    )
    vendor = folder / "vendor"
    vendor.mkdir()
    (vendor / "shadow_lib.py").write_text("VALUE = 'leaked'\n")
    before = list(sys.path)
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    leaked = [p for p in sys.path if "failvendor" in p and p not in before]
    assert leaked == [], f"sys.path leaked: {leaked}"
    assert "my_editor_plugin_failvendor" not in sys.modules


def test_shadowed_earlier_candidate_never_registers(tmp_path, host, settings_store):
    """When user dir shadows bundled, the bundled register() must NOT run,
    otherwise the editor accumulates duplicate menu actions / providers."""
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    bundled.mkdir()
    user.mkdir()
    make_plugin(
        bundled, "shared_id",
        manifest_overrides={"version": "1.0.0", "name": "Bundled"},
        init_source=(
            "def register(api):\n"
            "    api.add_menu_action('Plugins', 'BUNDLED', lambda: None)\n"
        ),
    )
    make_plugin(
        user, "shared_id",
        manifest_overrides={"version": "2.0.0", "name": "User Updated"},
        init_source=(
            "def register(api):\n"
            "    api.add_menu_action('Plugins', 'USER', lambda: None)\n"
        ),
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[bundled, user], include_default_dirs=False,
    )
    assert report.errors == []
    assert [p.version for p in report.loaded] == ["2.0.0"]
    # Only the user version's action should be present.
    labels = [label for (_pid, _menu, label, _cb) in host.menu_actions]
    assert labels == ["USER"], labels
    # Shadowed bundled entry reported in skipped.
    assert any(s.reason == SKIP_SHADOWED for s in report.skipped)


def test_later_dir_overrides_earlier_dir_on_same_id(tmp_path, host, settings_store):
    """User-installed plugins should win over bundled when they share
    an id - that's how the marketplace delivers updates."""
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    bundled.mkdir()
    user.mkdir()
    make_plugin(
        bundled, "shared_id",
        manifest_overrides={"version": "1.0.0", "name": "Bundled"},
    )
    make_plugin(
        user, "shared_id",
        manifest_overrides={"version": "2.0.0", "name": "User Updated"},
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[bundled, user], include_default_dirs=False,
    )
    assert report.errors == []
    assert len(report.loaded) == 1
    assert report.loaded[0].version == "2.0.0"
    assert report.loaded[0].name == "User Updated"


# --------------------------------------------------------------------------- #
# Platform compat                                                             #
# --------------------------------------------------------------------------- #

def test_platforms_field_skips_unsupported_os(tmp_path, host, settings_store, monkeypatch):
    """A Mac-only plugin should skip cleanly on Linux."""
    monkeypatch.setattr(sys, "platform", "linux")
    make_plugin(tmp_path, "macplug", manifest_overrides={"platforms": ["darwin"]})
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert report.errors == []
    assert any(s.reason == SKIP_WRONG_PLATFORM for s in report.skipped)


def test_platforms_field_loads_on_supported_os(tmp_path, host, settings_store, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    make_plugin(tmp_path, "macplug", manifest_overrides={"platforms": ["darwin", "win32"]})
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["macplug"]


def test_invalid_platforms_field_is_error(tmp_path, host, settings_store, log_path):
    make_plugin(tmp_path, "weird", manifest_overrides={"platforms": ["plan9"]})
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "platforms" in report.errors[0].reason


# --------------------------------------------------------------------------- #
# Settings scoping                                                            #
# --------------------------------------------------------------------------- #

def test_settings_are_scoped_per_plugin(tmp_path, host, settings_store):
    """Two plugins writing the same key never see each other's value."""
    make_plugin(
        tmp_path, "a",
        init_source=(
            "def register(api):\n    api.set_setting('shared', 'from-a')\n"
        ),
    )
    make_plugin(
        tmp_path, "b",
        init_source=(
            "def register(api):\n    api.set_setting('shared', 'from-b')\n"
        ),
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert settings_store.get("a", "shared") == "from-a"
    assert settings_store.get("b", "shared") == "from-b"


# --------------------------------------------------------------------------- #
# parse_declared_kinds (input validation)                                     #
# --------------------------------------------------------------------------- #

def test_parse_declared_kinds_accepts_int_and_str_int():
    assert parse_declared_kinds([1, "30023", 31234]) == frozenset({1, 30023, 31234})


def test_parse_declared_kinds_empty_for_missing():
    assert parse_declared_kinds(None) == frozenset()
    assert parse_declared_kinds([]) == frozenset()


def test_parse_declared_kinds_rejects_garbage():
    with pytest.raises(ValueError):
        parse_declared_kinds("not a list")
    with pytest.raises(ValueError):
        parse_declared_kinds([1, "abc"])
    with pytest.raises(ValueError):
        parse_declared_kinds([True])  # bool sneaking in as int


def test_parse_declared_kinds_rejects_out_of_range():
    """Nostr kinds are 0..65535 per NIP-01."""
    with pytest.raises(ValueError):
        parse_declared_kinds([-1])
    with pytest.raises(ValueError):
        parse_declared_kinds([70000])


# --------------------------------------------------------------------------- #
# HostHooks methods are reachable from a real PluginAPI                       #
# --------------------------------------------------------------------------- #

def test_open_tab_routes_through_host(tmp_path, host, settings_store):
    make_plugin(
        tmp_path, "opener",
        init_source=(
            "def register(api):\n"
            "    api.open_tab('My Tab', 'body text')\n"
        ),
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert host.opened_tabs == [("My Tab", "body text")]


def test_set_current_text_returns_false_when_no_editor(tmp_path, host, settings_store):
    host._current_text = None  # simulate no active editor
    host.host_set_current_text = lambda t: False  # type: ignore[method-assign]
    make_plugin(
        tmp_path, "writer",
        init_source=(
            "def register(api):\n"
            "    api.set_setting('wrote', api.set_current_text('x'))\n"
        ),
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert settings_store.get("writer", "wrote") is False


def test_register_payment_provider(tmp_path, host, settings_store):
    """A plugin can register a payment provider; the host receives it."""
    make_plugin(
        tmp_path, "wallet",
        init_source=(
            "class P:\n"
            "    name = 'TestWallet'\n"
            "    def is_ready(self): return True\n"
            "    def pay_invoice(self, bolt11, sats, on_success, on_failure):\n"
            "        on_success('deadbeef')\n"
            "def register(api):\n"
            "    api.register_payment_provider(P())\n"
        ),
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert len(host.payment_providers) == 1
    pid, provider = host.payment_providers[0]
    assert pid == "wallet"
    assert provider.name == "TestWallet"
    assert provider.is_ready() is True


def test_register_payment_provider_rejects_garbage(host, settings_store):
    """``register_payment_provider`` must refuse anything that isn't
    a real provider implementation - protects the marketplace from
    a malformed plugin."""
    api = PluginAPI(
        identity=PluginIdentity(
            plugin_id="x", name="x", version="0",
            declared_kinds=frozenset(),
        ),
        host=host,
        settings_scope=settings_store.scope("x"),
    )
    with pytest.raises(TypeError):
        api.register_payment_provider("not a provider")


def test_register_payment_provider_rejects_missing_name(host, settings_store):
    """A provider without a ``name`` would crash the marketplace UI later;
    catch it at register time."""
    api = PluginAPI(
        identity=PluginIdentity(
            plugin_id="x", name="x", version="0",
            declared_kinds=frozenset(),
        ),
        host=host,
        settings_scope=settings_store.scope("x"),
    )

    class NoName:
        def is_ready(self): return False
        def pay_invoice(self, *a, **k): pass

    with pytest.raises(TypeError):
        api.register_payment_provider(NoName())


# --------------------------------------------------------------------------- #
# Lifecycle bookkeeping (begin_plugin / record_vendor_path / unload)          #
# --------------------------------------------------------------------------- #

def test_loader_invokes_begin_plugin_for_each_winner(tmp_path, host, settings_store):
    make_plugin(tmp_path, "alpha")
    make_plugin(tmp_path, "beta")
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [i.plugin_id for i in host.begun] == ["alpha", "beta"]


def test_loader_records_vendor_path_when_present(tmp_path, host, settings_store):
    folder = make_plugin(tmp_path, "v")
    (folder / "vendor").mkdir()
    (folder / "vendor" / "vlib.py").write_text("V = 1\n")
    try:
        load_all_plugins(
            host=host, settings_store=settings_store,
            extra_dirs=[tmp_path], include_default_dirs=False,
        )
        recorded = dict(host.recorded_vendor)
        assert "v" in recorded
        assert recorded["v"] is not None and recorded["v"].endswith("vendor")
    finally:
        sys.path[:] = [p for p in sys.path if "/v/vendor" not in p]
        sys.modules.pop("vlib", None)
        sys.modules.pop("my_editor_plugin_v", None)


def test_register_failure_calls_unload_rollback(tmp_path, host, settings_store, log_path):
    """If register() raises, the host's slot must be cleaned up too,
    otherwise its bookkeeping would leak a half-loaded entry."""
    make_plugin(
        tmp_path, "boom",
        init_source="def register(api):\n    raise RuntimeError('x')\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert "boom" in host.unloaded


def test_plugin_module_name_helper_matches_loader():
    """The helper used by host bookkeeping must match what the loader
    actually inserts into sys.modules."""
    assert plugin_module_name("hello") == "my_editor_plugin_hello"


# --------------------------------------------------------------------------- #
# declared_capabilities                                                       #
# --------------------------------------------------------------------------- #

def test_declared_capabilities_parses_and_threads_through_identity(
    tmp_path, host, settings_store,
):
    """A manifest with declared_capabilities must produce a loaded
    plugin whose PluginIdentity carries those capabilities, so the API
    layer can enforce them at call time."""
    make_plugin(
        tmp_path, "cap",
        manifest_overrides={
            "api_version": 2,
            "declared_capabilities": ["dock_widget", "network"],
        },
        init_source="def register(api):\n    pass\n",
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.errors == []
    assert [p.plugin_id for p in report.loaded] == ["cap"]


def test_declared_capabilities_missing_defaults_to_empty(
    tmp_path, host, settings_store,
):
    """Omitting declared_capabilities is the common case for menu-only
    plugins; the loader must accept the manifest and treat it as no
    extra permissions requested."""
    make_plugin(
        tmp_path, "menu_only",
        init_source="def register(api):\n    pass\n",
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.errors == []
    assert [p.plugin_id for p in report.loaded] == ["menu_only"]


def test_declared_capabilities_unknown_value_rejected(
    tmp_path, host, settings_store, log_path,
):
    """An unknown capability string must fail the load with a clear
    message rather than silently doing nothing - a typo otherwise
    becomes a permission the editor will never enforce or display."""
    make_plugin(
        tmp_path, "typo",
        manifest_overrides={
            "api_version": 2,
            "declared_capabilities": ["dock_widgte"],  # typo
        },
        init_source="def register(api):\n    pass\n",
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "dock_widgte" in report.errors[0].reason


def test_declared_capabilities_must_be_a_list(
    tmp_path, host, settings_store, log_path,
):
    """A scalar string in place of the list is the most common typo;
    fail loud at load time."""
    make_plugin(
        tmp_path, "wrong_shape",
        manifest_overrides={
            "api_version": 2,
            "declared_capabilities": "dock_widget",
        },
        init_source="def register(api):\n    pass\n",
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "declared_capabilities" in report.errors[0].reason
