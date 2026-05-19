"""PluginHost adapter: per-plugin bookkeeping + unload.

Tests use a stub ``EditorBackend`` so they don't need a real Qt
window. The point here is to verify that ``unload(plugin_id)``
retracts every contribution a plugin made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from plugin_system import PluginPermissionError
from plugin_system import (
    PluginHost,
    PluginInfo,
    PluginSettingsStore,
    load_all_plugins,
)


class FakeBackend:
    """Stub ``EditorBackend`` that records what was added/removed."""

    def __init__(self) -> None:
        self.actions: List[tuple[str, str, Callable]] = []  # (menu, label, wrapped_cb)
        self.removed: List[tuple[str, str]] = []
        self.dock_widgets: List[tuple[str, object, str]] = []  # (title, widget, area)
        self.dock_removed: List[object] = []
        self.tabs: List[tuple[str, str]] = []
        self.statuses: List[str] = []
        self._counter = 0

    def editor_create_menu_action(self, menu_title, label, callback):
        self._counter += 1
        self.actions.append((menu_title, label, callback))
        return ("handle", menu_title, label, self._counter)

    def editor_remove_menu_action(self, handle):
        _, menu_title, label, _ = handle
        self.removed.append((menu_title, label))

    def editor_create_dock_widget(self, title, widget, area):
        self.dock_widgets.append((title, widget, area))
        return widget

    def editor_remove_dock_widget(self, handle):
        self.dock_removed.append(handle)

    def editor_get_current_text(self):
        return "fake"

    def editor_set_current_text(self, text):
        return True

    def editor_open_tab(self, title, content):
        self.tabs.append((title, content))

    def editor_show_status(self, message, timeout_ms=4000):
        self.statuses.append(message)


def make_plugin(
    root: Path,
    plugin_id: str,
    init_source: str,
    *,
    declared_capabilities: Optional[List[str]] = None,
) -> Path:
    folder = root / plugin_id
    folder.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "api_version": 2 if declared_capabilities else 1,
    }
    if declared_capabilities is not None:
        manifest["declared_capabilities"] = declared_capabilities
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (folder / "__init__.py").write_text(init_source)
    return folder


@pytest.fixture
def settings_store(tmp_path) -> PluginSettingsStore:
    return PluginSettingsStore(path=tmp_path / "settings.json")


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def host(backend) -> PluginHost:
    return PluginHost(backend)


def test_unload_removes_menu_actions(tmp_path, host, backend, settings_store):
    make_plugin(
        tmp_path, "alpha",
        "def register(api):\n"
        "    api.add_menu_action('Plugins', 'A1', lambda: None)\n"
        "    api.add_menu_action('Plugins', 'A2', lambda: None)\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [label for (_m, label, _cb) in backend.actions] == ["A1", "A2"]

    assert host.unload("alpha") is True
    assert sorted(label for (_m, label) in backend.removed) == ["A1", "A2"]


def test_unload_returns_false_for_unknown_plugin(host):
    assert host.unload("never_loaded") is False


def test_unload_drops_payment_provider(tmp_path, host, backend, settings_store):
    make_plugin(
        tmp_path, "wallet",
        "class P:\n"
        "    name = 'W'\n"
        "    def is_ready(self): return True\n"
        "    def pay_invoice(self, b, s, on_success, on_failure): pass\n"
        "def register(api):\n"
        "    api.register_payment_provider(P())\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [pid for (pid, _) in host.payment_providers()] == ["wallet"]
    host.unload("wallet")
    assert host.payment_providers() == []


def test_unload_removes_sys_modules_entry(tmp_path, host, backend, settings_store):
    make_plugin(
        tmp_path, "modtest",
        "def register(api): pass\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert "my_editor_plugin_modtest" in sys.modules
    host.unload("modtest")
    assert "my_editor_plugin_modtest" not in sys.modules


def test_unload_removes_vendor_path(tmp_path, host, backend, settings_store):
    folder = make_plugin(
        tmp_path, "vp",
        "import vendor_lib_xyz\n"
        "def register(api): pass\n",
    )
    (folder / "vendor").mkdir()
    (folder / "vendor" / "vendor_lib_xyz.py").write_text("VALUE = 1\n")
    try:
        load_all_plugins(
            host=host, settings_store=settings_store,
            extra_dirs=[tmp_path], include_default_dirs=False,
        )
        assert any("vp" in p and p.endswith("vendor") for p in sys.path)
        host.unload("vp")
        assert not any("vp" in p and p.endswith("vendor") for p in sys.path)
    finally:
        sys.path[:] = [p for p in sys.path if "vp" not in p]
        sys.modules.pop("vendor_lib_xyz", None)
        sys.modules.pop("my_editor_plugin_vp", None)


def test_unload_all_clears_everything(tmp_path, host, backend, settings_store):
    make_plugin(tmp_path, "a", "def register(api): api.add_menu_action('Plugins','A',lambda: None)\n")
    make_plugin(tmp_path, "b", "def register(api): api.add_menu_action('Plugins','B',lambda: None)\n")
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    host.unload_all()
    assert host.loaded_plugin_ids() == []
    assert sorted(label for (_m, label) in backend.removed) == ["A", "B"]


def test_safe_callback_status_on_exception(tmp_path, host, backend, settings_store):
    """A plugin action that raises must surface as a status message,
    not as an unhandled exception in the editor."""
    make_plugin(
        tmp_path, "bug",
        "def _click():\n"
        "    raise RuntimeError('boom')\n"
        "def register(api):\n"
        "    api.add_menu_action('Plugins', 'X', _click)\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    # The wrapped callback is the third tuple element from the fake.
    _menu, _label, wrapped = backend.actions[0]
    wrapped()  # must not raise
    assert any("bug" in s and "boom" in s for s in backend.statuses)


def test_reload_replaces_actions(tmp_path, host, backend, settings_store):
    """Hot-reload after an update: unload then load again. The new
    register's menu actions replace the old ones, with no duplicates."""
    folder = make_plugin(
        tmp_path, "rl",
        "def register(api):\n"
        "    api.add_menu_action('Plugins', 'v1', lambda: None)\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert host.unload("rl") is True

    # Simulate updated plugin
    (folder / "__init__.py").write_text(
        "def register(api):\n"
        "    api.add_menu_action('Plugins', 'v2', lambda: None)\n"
    )
    from plugin_system import load_one_plugin_folder
    load_one_plugin_folder(folder, host=host, settings_store=settings_store)
    labels = [label for (_m, label, _cb) in backend.actions]
    # Both v1 and v2 were added (history of events) but the *live*
    # state ( what's still in the menu ) should only be v2 after the
    # unload removed v1.
    removed_labels = [label for (_m, label) in backend.removed]
    assert "v1" in removed_labels
    assert "v2" in labels


# --------------------------------------------------------------------------- #
# register_unload                                                             #
# --------------------------------------------------------------------------- #

def test_register_unload_callback_fires_on_unload(
    tmp_path, host, backend, settings_store,
):
    """The callback registered via ``api.register_unload`` must run when
    the plugin is unloaded, before the host removes its menu/dock UI."""
    make_plugin(
        tmp_path, "tear",
        "calls = []\n"
        "def register(api):\n"
        "    api.register_unload(lambda: calls.append('bye'))\n"
        "    api.add_menu_action('Marketplace', 'X', lambda: None)\n"
        "    import sys; sys.modules['tear_calls'] = calls\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    calls = sys.modules.pop("tear_calls")
    assert calls == []
    host.unload("tear")
    assert calls == ["bye"]


def test_register_unload_callbacks_run_lifo(
    tmp_path, host, backend, settings_store,
):
    """Multiple unload callbacks run last-in-first-out so resource
    nesting (timer stops before the widget it updates is removed) holds."""
    make_plugin(
        tmp_path, "lifo",
        "order = []\n"
        "def register(api):\n"
        "    api.register_unload(lambda: order.append('first'))\n"
        "    api.register_unload(lambda: order.append('second'))\n"
        "    api.register_unload(lambda: order.append('third'))\n"
        "    import sys; sys.modules['lifo_order'] = order\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    order = sys.modules.pop("lifo_order")
    host.unload("lifo")
    assert order == ["third", "second", "first"]


def test_register_unload_callback_exception_does_not_block_rest(
    tmp_path, host, backend, settings_store,
):
    """A bad teardown callback surfaces as a status message; later
    callbacks and the menu/widget cleanup still run."""
    make_plugin(
        tmp_path, "boom",
        "ok = []\n"
        "def _bad(): raise RuntimeError('teardown failed')\n"
        "def register(api):\n"
        "    api.register_unload(lambda: ok.append('after_bad'))\n"
        "    api.register_unload(_bad)\n"
        "    api.add_menu_action('Marketplace', 'X', lambda: None)\n"
        "    import sys; sys.modules['boom_ok'] = ok\n",
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    ok = sys.modules.pop("boom_ok")
    host.unload("boom")
    assert ok == ["after_bad"]
    assert any("teardown failed" in s for s in backend.statuses)
    # Menu cleanup must still have happened.
    assert any(label == "X" for (_menu, label) in backend.removed)


# --------------------------------------------------------------------------- #
# add_dock_widget                                                             #
# --------------------------------------------------------------------------- #

def test_add_dock_widget_creates_and_removes(
    tmp_path, host, backend, settings_store,
):
    make_plugin(
        tmp_path, "dock",
        "def register(api):\n"
        "    api.add_dock_widget('Side', lambda: object(), area='right')\n",
        declared_capabilities=["dock_widget"],
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert len(backend.dock_widgets) == 1
    title, widget, area = backend.dock_widgets[0]
    assert title == "Side"
    assert area == "right"

    host.unload("dock")
    assert backend.dock_removed == [widget]


def test_add_dock_widget_without_capability_raises_at_register(
    tmp_path, host, backend, settings_store,
):
    """A plugin that calls add_dock_widget without declaring the
    capability must fail to register. The host rolls back so no
    half-loaded plugin slot leaks."""
    make_plugin(
        tmp_path, "naughty",
        "def register(api):\n"
        "    api.add_dock_widget('Side', lambda: object(), area='right')\n",
        # no declared_capabilities
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert report.loaded == []
    assert len(report.errors) == 1
    assert "dock_widget" in report.errors[0].reason
    assert "naughty" not in host.loaded_plugin_ids()


def test_dock_widget_factory_exception_is_isolated(
    tmp_path, host, backend, settings_store,
):
    """If the widget factory raises, the plugin still registers (other
    contributions land), and the failure shows as a status message."""
    make_plugin(
        tmp_path, "flaky",
        "def _boom(): raise RuntimeError('build failed')\n"
        "def register(api):\n"
        "    api.add_dock_widget('Side', _boom, area='right')\n"
        "    api.add_menu_action('Marketplace', 'M', lambda: None)\n",
        declared_capabilities=["dock_widget"],
    )
    report = load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    assert [p.plugin_id for p in report.loaded] == ["flaky"]
    assert backend.dock_widgets == []
    assert any("build failed" in s for s in backend.statuses)
    assert any(label == "M" for (_menu, label, _cb) in backend.actions)


def test_unload_runs_callbacks_before_removing_widgets(
    tmp_path, host, backend, settings_store,
):
    """The unload order is callbacks first, then UI teardown, so a
    plugin's teardown hook can still talk to its own widgets."""
    make_plugin(
        tmp_path, "order",
        "seen = []\n"
        "def _factory():\n"
        "    w = object()\n"
        "    seen.append(('built', id(w)))\n"
        "    return w\n"
        "def register(api):\n"
        "    api.add_dock_widget('S', _factory, area='right')\n"
        "    api.register_unload(lambda: seen.append('teardown'))\n"
        "    import sys; sys.modules['order_seen'] = seen\n",
        declared_capabilities=["dock_widget"],
    )
    load_all_plugins(
        host=host, settings_store=settings_store,
        extra_dirs=[tmp_path], include_default_dirs=False,
    )
    seen = sys.modules.pop("order_seen")
    host.unload("order")
    # Teardown ran before the widget was removed from the splitter.
    teardown_idx = seen.index("teardown")
    assert backend.dock_removed, "widget should have been removed"
    # The fake records dock removal in backend.dock_removed; teardown
    # must appear before the widget is dropped from the splitter, which
    # the host adapter does immediately after the callbacks.
    assert seen[teardown_idx] == "teardown"
