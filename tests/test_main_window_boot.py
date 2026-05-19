"""Smoke test: MainWindow constructs without raising.

This is the regression lock for the audit's C-1 finding — the editor
crashed on launch because feature wiring introduced an ordering bug
that the unit test suite couldn't see (every protocol test mocks out
the integration path that breaks).

The test runs in offline mode: no relays are contacted, no plugin is
loaded, no files are touched outside the temporary config root. A
construction that succeeds means every cross-method ordering issue
in ``__init__`` is at least syntactically valid; deeper checks live
in dedicated tests.
"""

from __future__ import annotations

import os
import sys

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([sys.argv[0]])


def test_main_window_constructs_without_active_profile(qapp, tmp_path, monkeypatch):
    """The first-run path (no saved profile, no initial file) must
    finish ``__init__`` and present a usable window.

    We point the editor at a temp config root so the SQLite caches,
    receipt store and so on are sandboxed away from the developer's
    machine.
    """
    # Force the editor's config root to the temp directory so SQLite
    # caches, receipts, and so on are sandboxed away from the dev
    # machine's real config tree.
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    import plugin_system
    monkeypatch.setattr(plugin_system, "user_config_root", lambda: cfg)
    import plugin_system.paths as plugin_paths
    monkeypatch.setattr(plugin_paths, "user_config_root", lambda: cfg)
    import main_window
    monkeypatch.setattr(main_window, "user_config_root", lambda: cfg)

    from main_window import MainWindow
    window = MainWindow(None)
    try:
        # If construction got this far we passed the audit's critical
        # finding. A couple of trivial assertions confirm the window
        # is actually usable.
        assert window is not None
        # Bare-minimum sanity: a few invariants that always hold by
        # the end of ``__init__`` regardless of the user's saved state.
        assert window.tabs is not None
        assert window.status is not None
    finally:
        window.close()
        window.deleteLater()


def test_main_window_loads_with_no_plugins(qapp, tmp_path, monkeypatch):
    """The marketplace stack must come up even when no plugins are
    installed.  Catches missing fallbacks for empty caches.
    """
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    import plugin_system
    monkeypatch.setattr(plugin_system, "user_config_root", lambda: cfg)
    import plugin_system.paths as plugin_paths
    monkeypatch.setattr(plugin_paths, "user_config_root", lambda: cfg)
    import main_window
    monkeypatch.setattr(main_window, "user_config_root", lambda: cfg)
    from main_window import MainWindow

    window = MainWindow(None)
    try:
        # The marketplace social stack attributes must exist post-init.
        assert hasattr(window, "_engagement_cache")
        assert hasattr(window, "_engagement_fetcher")
        assert hasattr(window, "_engagement_publisher")
        assert hasattr(window, "_commenter_profile_fetcher")
        assert hasattr(window, "_mute_list_cache")
        assert hasattr(window, "_follow_trust_cache")
        assert hasattr(window, "_payment_receipts")
        assert hasattr(window, "_marketplace_outbox_router")
    finally:
        window.close()
        window.deleteLater()
