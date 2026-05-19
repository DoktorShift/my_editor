"""Icon path resolution + fallback rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from plugin_marketplace.controller import _resolve_icon_path, _listing_from_manifest
from plugin_marketplace.ui.icons import (
    plugin_pixmap,
    _color_for,
    _initial_for,
)
from plugin_system import PluginInfo


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


# ──────────────────────────────────────────────────────────────────────
# _resolve_icon_path  (controller path-safety)
# ──────────────────────────────────────────────────────────────────────

def test_resolve_returns_empty_when_field_missing(tmp_path):
    assert _resolve_icon_path(tmp_path, None) == ""
    assert _resolve_icon_path(tmp_path, "") == ""
    assert _resolve_icon_path(tmp_path, "   ") == ""


def test_resolve_returns_empty_when_file_missing(tmp_path):
    assert _resolve_icon_path(tmp_path, "nope.svg") == ""


def test_resolve_returns_absolute_when_present(tmp_path):
    icon = tmp_path / "icon.svg"
    icon.write_text("<svg/>")
    out = _resolve_icon_path(tmp_path, "icon.svg")
    assert out == str(icon.resolve())


def test_resolve_rejects_path_traversal(tmp_path):
    """A manifest pointing at ``../../etc/passwd`` must not escape the
    plugin folder, even if the file exists."""
    outside = tmp_path.parent / "outside.svg"
    try:
        outside.write_text("<svg/>")
        assert _resolve_icon_path(tmp_path, "../outside.svg") == ""
    finally:
        outside.unlink(missing_ok=True)


def test_resolve_rejects_absolute_path(tmp_path):
    icon = tmp_path / "icon.svg"
    icon.write_text("<svg/>")
    assert _resolve_icon_path(tmp_path, str(icon)) == ""


# ──────────────────────────────────────────────────────────────────────
# Manifest -> listing carries icon_path
# ──────────────────────────────────────────────────────────────────────

def test_listing_from_manifest_includes_icon_path(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "icon.svg").write_text("<svg/>")
    (folder / "manifest.json").write_text(json.dumps({
        "id": "p", "name": "P", "version": "1.0.0", "api_version": 1,
        "icon": "icon.svg",
    }))
    info = PluginInfo("p", "P", "1.0.0", folder, "user")
    listing = _listing_from_manifest(info)
    assert listing.icon_path == str((folder / "icon.svg").resolve())


def test_listing_from_manifest_empty_icon_path_when_missing(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "p", "name": "P", "version": "1.0.0", "api_version": 1,
    }))
    info = PluginInfo("p", "P", "1.0.0", folder, "user")
    assert _listing_from_manifest(info).icon_path == ""


# ──────────────────────────────────────────────────────────────────────
# Avatar fallback
# ──────────────────────────────────────────────────────────────────────

def test_color_is_deterministic():
    """Same id -> same color across runs (used by the avatar palette)."""
    assert _color_for("hello_world").name() == _color_for("hello_world").name()


def test_initial_uses_first_alnum_of_name():
    assert _initial_for("foo", "Hello World") == "H"
    assert _initial_for("foo", "  example") == "E"


def test_initial_falls_back_to_plugin_id():
    assert _initial_for("zeta", "") == "Z"


def test_initial_handles_no_alnum_chars():
    assert _initial_for("", "...") == "?"


def test_avatar_pixmap_is_not_null(qapp):
    """Generated avatar is a real pixmap at the requested size."""
    pm = plugin_pixmap(plugin_id="foo", name="Foo", icon_path="", size=48)
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()
    assert pm.width() >= 32  # honors size request modulo DPR


def test_avatar_pixmap_used_when_file_missing(qapp, tmp_path):
    """A bogus icon_path falls back to the generated avatar instead
    of crashing or returning a null pixmap."""
    pm = plugin_pixmap(
        plugin_id="bar", name="Bar",
        icon_path=str(tmp_path / "nope.svg"),
        size=32,
    )
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()


def test_icon_pixmap_loads_real_svg(qapp, tmp_path):
    """Files that exist on disk are loaded (SVG specifically because
    that's what bundled plugins ship)."""
    icon = tmp_path / "icon.svg"
    icon.write_text(
        "<?xml version='1.0'?>"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
        "<rect width='16' height='16' fill='red'/></svg>"
    )
    pm = plugin_pixmap(
        plugin_id="x", name="X", icon_path=str(icon), size=32,
    )
    assert not pm.isNull()
