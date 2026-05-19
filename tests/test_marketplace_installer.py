"""Installer tests: zip extraction safety + atomic replace.

Network is stubbed by writing zips to disk + pointing the installer
at a ``file://``-like local URL is not supported, so we exercise
``install_plugin`` by patching the download helper instead.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from plugin_marketplace import installer as installer_mod
from plugin_marketplace.installer import (
    InstallError,
    install_plugin,
    uninstall_plugin,
)
from plugin_marketplace.models import PluginListing


# ──────────────────────────────────────────────────────────────────────
# Zip helpers
# ──────────────────────────────────────────────────────────────────────

def build_zip(
    *,
    plugin_id: str,
    files: dict[str, str],
    extra_entries: list[zipfile.ZipInfo] | None = None,
) -> bytes:
    """Build a zip whose entries are all under ``plugin_id/``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for relpath, content in files.items():
            zf.writestr(f"{plugin_id}/{relpath}", content)
        for entry in extra_entries or []:
            zf.writestr(entry, b"")
    return buf.getvalue()


def standard_manifest(plugin_id: str) -> str:
    return json.dumps({
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "1.0.0",
        "api_version": 1,
    })


# ──────────────────────────────────────────────────────────────────────
# install_plugin (patched download)
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def patch_download(monkeypatch):
    """Patch the network download with a function that writes given bytes
    to the requested output path. Test passes a payload-builder lambda."""
    holder = {}

    def _download(url, out_path, *, progress_cb=None):
        out_path.write_bytes(holder["payload"])
        if progress_cb is not None:
            progress_cb(1.0)

    monkeypatch.setattr(installer_mod, "_download", _download)
    return holder


def _listing_for(plugin_id: str, payload: bytes) -> PluginListing:
    return PluginListing(
        plugin_id=plugin_id,
        name=plugin_id.title(),
        version="1.0.0",
        download_url="https://example.com/p.zip",
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_install_extracts_into_user_plugins_dir(tmp_path, patch_download):
    payload = build_zip(
        plugin_id="acme",
        files={
            "manifest.json": standard_manifest("acme"),
            "__init__.py": "def register(api): pass\n",
        },
    )
    patch_download["payload"] = payload
    listing = _listing_for("acme", payload)

    result = install_plugin(listing, install_root=tmp_path)

    assert result.install_path == (tmp_path / "acme").resolve()
    assert (tmp_path / "acme" / "manifest.json").is_file()
    assert (tmp_path / "acme" / "__init__.py").is_file()


def test_install_refuses_hash_mismatch(tmp_path, patch_download):
    payload = build_zip(
        plugin_id="m",
        files={"manifest.json": standard_manifest("m"), "__init__.py": "x"},
    )
    patch_download["payload"] = payload
    listing = PluginListing(
        plugin_id="m",
        name="M",
        version="1",
        download_url="https://e/p.zip",
        sha256="0" * 64,  # wrong digest
    )
    with pytest.raises(InstallError) as exc:
        install_plugin(listing, install_root=tmp_path)
    assert "hash" in str(exc.value).lower()
    assert not (tmp_path / "m").exists()


def test_install_refuses_zip_slip(tmp_path, patch_download):
    """An archive with ``..`` should be rejected before any extraction."""
    plugin_id = "slip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Manifest under the legal path (so the validator gets past
        # the "no manifest" check if it ever runs).
        zf.writestr(f"{plugin_id}/manifest.json", standard_manifest(plugin_id))
        zf.writestr(f"{plugin_id}/../../etc/evil", "")
    payload = buf.getvalue()
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)
    with pytest.raises(InstallError) as exc:
        install_plugin(listing, install_root=tmp_path)
    assert "unsafe" in str(exc.value).lower() or ".." in str(exc.value)


def test_install_refuses_absolute_paths(tmp_path, patch_download):
    plugin_id = "abs"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{plugin_id}/manifest.json", standard_manifest(plugin_id))
        zf.writestr("/etc/evil", "")
    payload = buf.getvalue()
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)
    with pytest.raises(InstallError):
        install_plugin(listing, install_root=tmp_path)


def test_install_refuses_entries_outside_plugin_folder(tmp_path, patch_download):
    """An archive that scatters files outside ``plugin_id/`` is malformed."""
    plugin_id = "scatter"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{plugin_id}/manifest.json", standard_manifest(plugin_id))
        zf.writestr("other_pkg/foo.py", "")
    payload = buf.getvalue()
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)
    with pytest.raises(InstallError):
        install_plugin(listing, install_root=tmp_path)


def test_install_rejects_symlink_entry(tmp_path, patch_download):
    plugin_id = "linky"
    info = zipfile.ZipInfo(f"{plugin_id}/symlink")
    info.external_attr = (0o120777 << 16)  # symlink mode bits
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{plugin_id}/manifest.json", standard_manifest(plugin_id))
        zf.writestr(info, "..")
    payload = buf.getvalue()
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)
    with pytest.raises(InstallError) as exc:
        install_plugin(listing, install_root=tmp_path)
    assert "symlink" in str(exc.value).lower()


def test_install_requires_manifest_id_matches_listing(tmp_path, patch_download):
    """A registry that advertises plugin 'pretty' but serves a zip with
    manifest id 'evil' must not install."""
    plugin_id = "pretty"
    payload = build_zip(
        plugin_id=plugin_id,
        files={
            "manifest.json": json.dumps({"id": "evil", "name": "evil", "version": "1.0.0", "api_version": 1}),
            "__init__.py": "",
        },
    )
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)
    with pytest.raises(InstallError) as exc:
        install_plugin(listing, install_root=tmp_path)
    assert "id" in str(exc.value).lower()


def test_install_atomically_replaces_existing(tmp_path, patch_download):
    """An update should overwrite the old folder without losing data
    on a mid-rename crash. We verify the final tree contains only the
    new contents."""
    plugin_id = "upd"
    # Pre-existing v1 folder
    old = tmp_path / plugin_id
    old.mkdir()
    (old / "manifest.json").write_text(json.dumps({"id": plugin_id, "name": "v1", "version": "1.0.0", "api_version": 1}))
    (old / "old_file.txt").write_text("v1 content")

    # New v2 payload
    payload = build_zip(
        plugin_id=plugin_id,
        files={
            "manifest.json": json.dumps({"id": plugin_id, "name": "v2", "version": "2.0.0", "api_version": 1}),
            "new_file.txt": "v2 content",
        },
    )
    patch_download["payload"] = payload
    listing = _listing_for(plugin_id, payload)

    install_plugin(listing, install_root=tmp_path)

    assert (tmp_path / plugin_id / "new_file.txt").read_text() == "v2 content"
    # Old file must be gone — atomic replace, not merge.
    assert not (tmp_path / plugin_id / "old_file.txt").exists()


def test_install_rejects_future_api_version(tmp_path, patch_download):
    payload = build_zip(
        plugin_id="future",
        files={"manifest.json": standard_manifest("future"), "__init__.py": ""},
    )
    patch_download["payload"] = payload
    listing = PluginListing(
        plugin_id="future", name="Future", version="1.0",
        download_url="https://e/p.zip", sha256=hashlib.sha256(payload).hexdigest(),
        api_version=99,
    )
    with pytest.raises(InstallError):
        install_plugin(listing, install_root=tmp_path)


# ──────────────────────────────────────────────────────────────────────
# Uninstall
# ──────────────────────────────────────────────────────────────────────

def test_uninstall_removes_folder(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "manifest.json").write_text("{}")
    assert uninstall_plugin("p", install_root=tmp_path) is True
    assert not folder.exists()


def test_uninstall_missing_is_noop(tmp_path):
    assert uninstall_plugin("nope", install_root=tmp_path) is False


def test_uninstall_refuses_path_traversal(tmp_path):
    """Even though plugin_id is validated upstream, defence-in-depth."""
    other = tmp_path.parent / "outside"
    other.mkdir(exist_ok=True)
    try:
        with pytest.raises(InstallError):
            uninstall_plugin("../outside", install_root=tmp_path)
    finally:
        shutil.rmtree(other, ignore_errors=True)
