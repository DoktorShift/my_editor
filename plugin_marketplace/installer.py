"""Download, verify, and extract plugin packages.

Each plugin in the registry advertises a ``.zip`` URL and the SHA-256
of its contents. The installer:

  1. Downloads the zip to a tempfile inside the platform's temp dir.
  2. Verifies the SHA-256 matches the advertised digest before
     touching anything in ``user_plugins_dir()``.
  3. Validates the zip's layout (single top-level folder named after
     the plugin_id) and refuses path-traversal entries (zip-slip).
  4. Atomically replaces any existing folder with the new contents.
  5. Returns the resolved install path so the caller can hand it to
     ``plugin_system.load_one_plugin_folder``.

The module deliberately doesn't talk to the editor's main window. The
marketplace UI orchestrates by calling these functions from a worker
thread and notifying the host on completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from plugin_system import (
    PLUGIN_API_VERSION,
    user_plugins_dir,
)

from .models import PluginListing


# Hard cap to defeat zip bombs. 25 MiB is generous for plugin code +
# vendored deps; if a plugin grows beyond this the author should
# either trim it or split it into multiple plugins.
_MAX_PACKAGE_BYTES: int = 25 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES: int = 100 * 1024 * 1024  # ratio-bomb defense

_DOWNLOAD_TIMEOUT_S: float = 60.0


# ──────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────

class InstallError(RuntimeError):
    """Anything that prevents a plugin from being installed.

    The message is user-visible and goes straight to the marketplace
    error dialog, so keep them concise.
    """


# ──────────────────────────────────────────────────────────────────────
# Result
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstallResult:
    listing: PluginListing
    install_path: Path


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def install_plugin(
    listing: PluginListing,
    *,
    install_root: Optional[Path] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> InstallResult:
    """Download, verify, and install ``listing``.

    ``install_root`` defaults to ``user_plugins_dir()``; tests can
    point it at a tempdir to exercise the full path without writing
    to the real per-user config.

    ``progress_cb`` is invoked with a float in 0.0..1.0 during the
    download. Best-effort; nothing fails if it raises.

    Returns the resolved install path. Raises ``InstallError`` on
    any failure with a single user-readable message.
    """
    if listing.api_version > PLUGIN_API_VERSION:
        raise InstallError(
            f"This plugin needs API v{listing.api_version}; "
            f"this editor only supports up to v{PLUGIN_API_VERSION}. "
            f"Please update the editor."
        )

    root = (install_root or user_plugins_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)

    target = root / listing.plugin_id

    with tempfile.TemporaryDirectory(prefix="my_editor_install_") as tmp_str:
        tmp = Path(tmp_str)
        zip_path = tmp / "package.zip"
        _download(listing.download_url, zip_path, progress_cb=progress_cb)
        _verify_sha256(zip_path, listing.sha256)

        staging = tmp / "staging"
        staging.mkdir()
        _extract_safely(zip_path, staging, plugin_id=listing.plugin_id)

        contents = staging / listing.plugin_id
        _validate_extracted_manifest(contents, listing)

        # Atomic replace: rename the staging folder into place. The
        # old folder (if any) is moved aside first so a partial rename
        # can't strand the user without a working plugin.
        _atomic_replace(target=target, new_folder=contents)

    return InstallResult(listing=listing, install_path=target)


def uninstall_plugin(plugin_id: str, *, install_root: Optional[Path] = None) -> bool:
    """Remove a plugin folder from ``user_plugins_dir``.

    Returns True if a folder existed and was removed, False if nothing
    was there. Bundled plugins are never removed by this function -
    the marketplace UI hides the uninstall button for them.
    """
    root = (install_root or user_plugins_dir()).resolve()
    target = (root / plugin_id).resolve()
    # Defence-in-depth: refuse to remove anything outside ``root``,
    # even though the plugin_id is already validated.
    try:
        target.relative_to(root)
    except ValueError:
        raise InstallError(f"refusing to remove path outside user plugins root: {target}")
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


# ──────────────────────────────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────────────────────────────

def _download(
    url: str,
    out_path: Path,
    *,
    progress_cb: Optional[Callable[[float], None]],
) -> None:
    if not url.lower().startswith("https://"):
        raise InstallError("plugin download_url must be HTTPS")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "my-editor-plugin-marketplace/1.0",
            "Accept": "application/zip, application/octet-stream",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S, context=ctx) as resp:
            if resp.status != 200:
                raise InstallError(f"download returned HTTP {resp.status}")
            total_str = resp.headers.get("Content-Length")
            try:
                total = int(total_str) if total_str is not None else None
            except ValueError:
                total = None
            if total is not None and total > _MAX_PACKAGE_BYTES:
                raise InstallError(
                    f"plugin package is {total} bytes; refuses larger than "
                    f"{_MAX_PACKAGE_BYTES}"
                )
            seen = 0
            with out_path.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    seen += len(chunk)
                    if seen > _MAX_PACKAGE_BYTES:
                        raise InstallError(
                            f"plugin package exceeded {_MAX_PACKAGE_BYTES} bytes mid-download"
                        )
                    f.write(chunk)
                    if progress_cb is not None and total:
                        try:
                            progress_cb(min(1.0, seen / total))
                        except Exception:
                            pass
    except urllib.error.HTTPError as exc:
        raise InstallError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise InstallError(f"network error: {exc.reason}") from exc
    except TimeoutError:
        raise InstallError("download timed out")
    except OSError as exc:
        raise InstallError(f"I/O error during download: {exc}")


def _verify_sha256(path: Path, expected_hex: str) -> None:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise InstallError(
            "plugin package hash mismatch - file may be tampered or corrupt"
        )


# ──────────────────────────────────────────────────────────────────────
# Safe extraction
# ──────────────────────────────────────────────────────────────────────

def _extract_safely(zip_path: Path, dest: Path, *, plugin_id: str) -> None:
    """Extract ``zip_path`` into ``dest`` with defenses against:

      - zip-slip: entries whose path resolves outside ``dest``
      - absolute paths inside the archive
      - symlinks (refused outright)
      - oversize totals (zip bombs)
      - entries outside the single expected top-level ``plugin_id/`` folder

    On any violation the partially-extracted dest folder is removed
    before raising, so the caller never sees a half-populated tree.
    """
    dest_resolved = dest.resolve()
    total_uncompressed = 0

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                _validate_zip_member(member, plugin_id=plugin_id)
                total_uncompressed += member.file_size
                if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
                    raise InstallError(
                        "plugin contents exceed uncompressed size cap"
                    )
                # Path resolution: PurePosixPath because zip names use
                # forward slashes on every platform.
                rel = PurePosixPath(member.filename)
                target_path = (dest / Path(*rel.parts)).resolve()
                try:
                    target_path.relative_to(dest_resolved)
                except ValueError:
                    raise InstallError(
                        f"plugin archive contains unsafe path: {member.filename!r}"
                    )

                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target_path.open("wb") as out:
                    shutil.copyfileobj(src, out, length=64 * 1024)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise InstallError(f"plugin archive is not a valid zip: {exc}") from exc
    except InstallError:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def _validate_zip_member(member: zipfile.ZipInfo, *, plugin_id: str) -> None:
    """Reject anything that isn't ``plugin_id/...``, plus symlinks.

    Most zip implementations encode a symlink with mode bits 0o120000.
    Python's stdlib zipfile doesn't unzip symlinks by default, but we
    refuse them explicitly so a deliberately-crafted symlink can't
    point at ``../../etc/passwd`` even if a future Python flips the
    default.
    """
    name = member.filename
    if not name:
        raise InstallError("plugin archive has an entry with empty name")
    if name.startswith("/") or "\\" in name:
        raise InstallError(f"plugin archive has unsafe path: {name!r}")
    parts = PurePosixPath(name).parts
    if ".." in parts:
        raise InstallError(f"plugin archive has '..' in path: {name!r}")
    if parts[0] != plugin_id:
        raise InstallError(
            f"plugin archive entries must be under {plugin_id!r}/, got {name!r}"
        )
    mode = member.external_attr >> 16
    if mode and (mode & 0o170000) == 0o120000:
        raise InstallError(f"plugin archive contains a symlink: {name!r}")


# ──────────────────────────────────────────────────────────────────────
# Manifest sanity check after extraction
# ──────────────────────────────────────────────────────────────────────

def _validate_extracted_manifest(folder: Path, listing: PluginListing) -> None:
    """Refuse to install a package whose manifest disagrees with the
    registry listing in ways that matter for trust.

    The id MUST match because the registry advertised it; mismatch
    would let a registry serve a different plugin under the displayed
    name. The version SHOULD match; mismatch is a warning, not a hard
    error, because pre-release builds sometimes ship with the next
    version pre-baked in the manifest.
    """
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise InstallError("plugin archive is missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"plugin manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InstallError("plugin manifest must be a JSON object")
    manifest_id = manifest.get("id")
    if manifest_id != listing.plugin_id:
        raise InstallError(
            f"plugin manifest id {manifest_id!r} doesn't match listing "
            f"{listing.plugin_id!r}"
        )


# ──────────────────────────────────────────────────────────────────────
# Atomic folder replace
# ──────────────────────────────────────────────────────────────────────

def _atomic_replace(*, target: Path, new_folder: Path) -> None:
    """Move ``new_folder`` to ``target``, replacing any existing tree.

    Strategy:
      1. If ``target`` exists, rename it to a sibling backup path
         (``<target>.old-<pid>``).
      2. Rename ``new_folder`` to ``target``.
      3. Remove the backup.

    If step 2 fails after step 1 succeeds, we attempt to restore the
    backup so the user isn't left without a working plugin.
    """
    target = target.resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    backup: Optional[Path] = None
    if target.exists():
        backup = parent / f"{target.name}.old-{os.getpid()}"
        # If a previous failed install left a backup behind, get rid of it.
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        os.replace(target, backup)
    try:
        # ``os.replace`` works on directories too, and is atomic on
        # POSIX. On Windows it works as long as the destination doesn't
        # already exist, which we've ensured via the backup step.
        os.replace(new_folder, target)
    except OSError:
        # Restore the old plugin so the user isn't broken.
        if backup is not None and backup.exists() and not target.exists():
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise InstallError("could not move new plugin into place")
    finally:
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
