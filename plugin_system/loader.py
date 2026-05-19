"""Plugin discovery + import + register.

The loader runs at startup (and at marketplace install time for
hot-load). It works in two phases:

  1. **Discover** every candidate folder, parse its manifest, and
     decide whether it's loadable. Duplicates from later directories
     overwrite earlier ones - that's how a user-installed update
     wins over a bundled version. Earlier candidates that lost the
     coin flip never reach phase 2.
  2. **Register** each winner: install vendor/ on ``sys.path``,
     import ``__init__.py``, call ``register(api)``. Every step is
     wrapped so one bad plugin can't take down the editor or any
     sibling plugin; if a step fails we roll back any partial state.

The function returns a ``LoadReport`` capturing successful loads,
errors, and legal-but-not-loaded skips so the host can surface a
useful summary instead of guessing.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .api import (
    HostHooks,
    PLUGIN_API_VERSION,
    PluginAPI,
    PluginIdentity,
    parse_declared_capabilities,
    parse_declared_kinds,
)
from .paths import bundled_plugins_dir, plugin_log_path, user_plugins_dir
from .settings import PluginSettingsStore


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

# Plugin ids end up as sys.modules keys, settings.json top-level keys,
# and folder names. A strict ASCII-only pattern keeps every downstream
# user simple - no path traversal, no quoting headaches, no surprises
# when a user copies their plugins folder between machines.
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_PLATFORM_KEYS = ("darwin", "win32", "linux")

# Module-name prefix; stable across releases so plugins importing
# their own siblings via ``from . import X`` keep working.
_MODULE_PREFIX = "my_editor_plugin_"


# --------------------------------------------------------------------------- #
# Public records                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PluginInfo:
    """The post-load record for one successfully-registered plugin."""

    plugin_id: str
    name: str
    version: str
    folder: Path
    source: str  # "bundled", "user", or "extra"


@dataclass(frozen=True)
class PluginLoadError:
    """A plugin folder we tried to load but couldn't. Surfaced so the
    host can show "1 plugin failed to load" in its status bar / log."""

    folder: Path
    reason: str


# Reason codes for ``PluginSkipped`` - strings so they're easy to
# render in a UI and easy to assert in tests.
SKIP_DISABLED = "disabled"
SKIP_NO_MANIFEST = "no_manifest"
SKIP_SHADOWED = "shadowed"
SKIP_WRONG_PLATFORM = "wrong_platform"


@dataclass(frozen=True)
class PluginSkipped:
    """A folder the loader chose not to load - *not* an error.

    The host can surface skips in a "diagnostics" pane so a user who
    disabled a plugin or installed a Mac-only plugin on Linux gets a
    clear signal about *why* it isn't running.
    """

    folder: Path
    reason: str  # one of the SKIP_* constants
    detail: Optional[str] = None  # human-readable extra context


@dataclass
class LoadReport:
    """Returned from :func:`load_all_plugins` so the host can render
    a complete summary of what happened, not just successes/failures.
    """

    loaded: List[PluginInfo] = field(default_factory=list)
    errors: List[PluginLoadError] = field(default_factory=list)
    skipped: List[PluginSkipped] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Public entry points                                                         #
# --------------------------------------------------------------------------- #

def load_all_plugins(
    *,
    host: HostHooks,
    settings_store: PluginSettingsStore,
    extra_dirs: Optional[Iterable[Path]] = None,
    include_default_dirs: bool = True,
) -> LoadReport:
    """Discover and register every plugin we can find.

    Scanning order:
      1. ``bundled_plugins/`` (read-only, ships with the editor)
      2. ``user_plugins_dir()`` (marketplace-installed)
      3. Anything in ``extra_dirs`` (test harnesses, dev override)

    A plugin id discovered later overrides an earlier one with the
    same id - so a user-installed update wins over the bundled
    version. The shadowed earlier candidate is reported under
    ``skipped`` so the host can show "Hidden by user copy" diagnostics
    instead of leaving the user wondering why a bundled plugin isn't
    visible.

    Tests can pass ``include_default_dirs=False`` to scan only the
    folders they explicitly listed, keeping the test isolated from
    whatever bundled plugins the editor ships.
    """
    search_dirs: List[Tuple[Path, str]] = []
    if include_default_dirs:
        search_dirs.append((bundled_plugins_dir(), "bundled"))
        search_dirs.append((user_plugins_dir(), "user"))
    if extra_dirs:
        for d in extra_dirs:
            search_dirs.append((Path(d), "extra"))

    report = LoadReport()

    # Phase 1: parse every manifest. Duplicates resolved last-wins.
    winners: dict[str, _Candidate] = {}
    for directory, source in search_dirs:
        if not directory.is_dir():
            continue
        for folder in sorted(directory.iterdir()):
            if not folder.is_dir():
                continue
            candidate = _discover(folder, source, report)
            if candidate is None:
                continue
            existing = winners.get(candidate.plugin_id)
            if existing is not None:
                report.skipped.append(PluginSkipped(
                    folder=existing.folder,
                    reason=SKIP_SHADOWED,
                    detail=f"hidden by {candidate.folder}",
                ))
            winners[candidate.plugin_id] = candidate

    # Phase 2: register the winners.
    for candidate in winners.values():
        info = _register(
            candidate, host=host, settings_store=settings_store, report=report,
        )
        if info is not None:
            report.loaded.append(info)

    return report


def load_one_plugin_folder(
    folder: Path,
    *,
    host: HostHooks,
    settings_store: PluginSettingsStore,
    source: str = "user",
) -> Tuple[Optional[PluginInfo], LoadReport]:
    """Load exactly one folder.

    Used by the marketplace installer to hot-load a freshly-installed
    plugin without re-scanning the whole tree. The plugin should
    already have been unloaded by the host first if it was previously
    loaded under the same id.
    """
    report = LoadReport()
    candidate = _discover(folder, source, report)
    if candidate is None:
        return None, report
    info = _register(
        candidate, host=host, settings_store=settings_store, report=report,
    )
    if info is not None:
        report.loaded.append(info)
    return info, report


# --------------------------------------------------------------------------- #
# Candidate                                                                   #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _Candidate:
    """Parsed manifest + folder, before we attempt to import/register.

    Splitting discovery out of registration is what makes shadowing
    work cleanly: we identify all the winners first, then only the
    winners get their ``register()`` invoked.
    """

    folder: Path
    source: str
    plugin_id: str
    name: str
    version: str
    declared_kinds: frozenset
    declared_capabilities: frozenset
    manifest: dict


def _discover(folder: Path, source: str, report: LoadReport) -> Optional[_Candidate]:
    """Parse a folder's manifest and decide whether it's a candidate.

    Returns ``None`` and appends to ``report.skipped`` or
    ``report.errors`` for any non-loadable folder. Returns a
    ``_Candidate`` for folders that look loadable.
    """
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        # Foreign folders (notes, vendored libraries, .DS_Store on
        # macOS) coexist silently; not an error.
        report.skipped.append(PluginSkipped(folder=folder, reason=SKIP_NO_MANIFEST))
        return None

    try:
        manifest = _read_manifest(manifest_path)
        plugin_id = _validate_plugin_id(_require_str(manifest, "id"))
        name = _require_str(manifest, "name", default=plugin_id)
        version = _require_str(manifest, "version", default="0.0.0")

        if manifest.get("enabled", True) is False:
            report.skipped.append(PluginSkipped(folder=folder, reason=SKIP_DISABLED))
            return None

        _check_api_version(manifest.get("api_version", 1))
        _check_platform_compat(manifest, folder, report)
        declared_kinds = parse_declared_kinds(manifest.get("declared_kinds"))
        declared_capabilities = parse_declared_capabilities(
            manifest.get("declared_capabilities")
        )
    except _SkippedHere:
        return None
    except Exception as exc:  # noqa: BLE001 - isolate per plugin
        report.errors.append(PluginLoadError(folder=folder, reason=str(exc)))
        _log_failure(folder, exc, phase="discovery")
        return None

    return _Candidate(
        folder=folder,
        source=source,
        plugin_id=plugin_id,
        name=name,
        version=version,
        declared_kinds=declared_kinds,
        declared_capabilities=declared_capabilities,
        manifest=manifest,
    )


class _SkippedHere(Exception):
    """Internal marker used inside ``_discover`` to bail out without
    treating the bail-out as a hard error (because the skip was already
    appended to ``report.skipped``)."""


def _check_api_version(api_version) -> None:
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise ValueError(f"invalid api_version {api_version!r}")
    if api_version < 1:
        raise ValueError(f"invalid api_version {api_version!r}")
    if api_version > PLUGIN_API_VERSION:
        raise ValueError(
            f"plugin requires api_version {api_version} "
            f"but this editor only supports up to {PLUGIN_API_VERSION}"
        )


def _check_platform_compat(manifest: dict, folder: Path, report: LoadReport) -> None:
    """If the manifest declares ``platforms``, the current OS must be in it.

    Missing key means "any platform". When the current OS is excluded
    we record a structured skip with the platform list so the host
    can surface "this plugin is Mac-only" in its diagnostics.
    """
    raw = manifest.get("platforms")
    if raw is None:
        return
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        raise ValueError("manifest 'platforms' must be a list of strings")
    normalized = {p.strip().lower() for p in raw if p.strip()}
    unknown = normalized - set(_PLATFORM_KEYS)
    if unknown:
        raise ValueError(
            f"manifest 'platforms' contains unknown entries {sorted(unknown)!r}; "
            f"expected subset of {list(_PLATFORM_KEYS)}"
        )
    if sys.platform not in normalized:
        report.skipped.append(PluginSkipped(
            folder=folder,
            reason=SKIP_WRONG_PLATFORM,
            detail=f"supported: {sorted(normalized)}, current: {sys.platform}",
        ))
        raise _SkippedHere()


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #

def _register(
    candidate: _Candidate,
    *,
    host: HostHooks,
    settings_store: PluginSettingsStore,
    report: LoadReport,
) -> Optional[PluginInfo]:
    """Import the plugin module and run its ``register`` callback.

    Rolls back any partial state (sys.path entry, sys.modules entry)
    if anything between vendor-install and register-success fails. A
    failed plugin must not leave the process in a state that affects
    later plugin loads.
    """
    folder = candidate.folder
    plugin_id = candidate.plugin_id
    module_name = _MODULE_PREFIX + plugin_id

    inserted_sys_path: Optional[str] = None
    module = None

    info = PluginInfo(
        plugin_id=plugin_id,
        name=candidate.name,
        version=candidate.version,
        folder=folder,
        source=candidate.source,
    )

    # Hosts that maintain per-plugin bookkeeping (the real PluginHost
    # adapter) implement these optional lifecycle hooks. Stub hosts
    # in tests need not - they're looked up via duck-typing.
    begin_plugin = getattr(host, "begin_plugin", None)
    record_vendor_path = getattr(host, "record_vendor_path", None)

    try:
        if begin_plugin is not None:
            begin_plugin(info)

        # Vendored deps on sys.path BEFORE the import. Plugins ship
        # third-party libraries in <plugin>/vendor/ so we don't mutate
        # the user's site-packages. Prepended so the plugin's version
        # wins over anything coincidentally available globally.
        vendor_dir = folder / "vendor"
        if vendor_dir.is_dir():
            vendor_str = str(vendor_dir.resolve())
            if vendor_str not in sys.path:
                sys.path.insert(0, vendor_str)
                inserted_sys_path = vendor_str
        if record_vendor_path is not None:
            record_vendor_path(plugin_id, inserted_sys_path)

        module = _import_plugin_module(folder, module_name)

        register = getattr(module, "register", None)
        if not callable(register):
            raise ValueError("plugin's __init__.py must export a callable register(api)")

        identity = PluginIdentity(
            plugin_id=plugin_id,
            name=candidate.name,
            version=candidate.version,
            declared_kinds=candidate.declared_kinds,
            declared_capabilities=candidate.declared_capabilities,
        )
        api = PluginAPI(
            identity=identity,
            host=host,
            settings_scope=settings_store.scope(plugin_id),
        )

        register(api)

    except Exception as exc:  # noqa: BLE001 - isolate per plugin
        # Roll back any state we built before re-raising. The host
        # never sees half-loaded plugins.
        if module is not None:
            sys.modules.pop(module_name, None)
        if inserted_sys_path is not None:
            try:
                sys.path.remove(inserted_sys_path)
            except ValueError:
                pass
        # Roll back host-side bookkeeping too: the slot was opened in
        # begin_plugin but the plugin never reached a working state.
        rollback = getattr(host, "unload", None)
        if rollback is not None:
            try:
                rollback(plugin_id)
            except Exception:  # noqa: BLE001
                pass
        report.errors.append(PluginLoadError(folder=folder, reason=str(exc)))
        _log_failure(folder, exc, phase="register")
        return None

    return info


# --------------------------------------------------------------------------- #
# Manifest helpers                                                            #
# --------------------------------------------------------------------------- #

def _read_manifest(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest.json must be a JSON object")
    return data


def _require_str(manifest: dict, key: str, *, default: Optional[str] = None) -> str:
    value = manifest.get(key, default)
    if value is None or not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest field {key!r} must be a non-empty string")
    return value.strip()


def _validate_plugin_id(plugin_id: str) -> str:
    """Reject anything that isn't a safe filesystem + module identifier.

    Plugin ids appear in sys.modules, settings.json keys, and folder
    paths. A strict pattern keeps every downstream user simple and
    closes off path-traversal and quoting issues.
    """
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise ValueError(
            f"plugin id {plugin_id!r} is invalid; must match "
            f"{_PLUGIN_ID_RE.pattern} (lowercase, digits, '_' or '-')"
        )
    return plugin_id


# --------------------------------------------------------------------------- #
# Import                                                                      #
# --------------------------------------------------------------------------- #

class _NoCacheSourceLoader(importlib.machinery.SourceFileLoader):
    """``SourceFileLoader`` that never writes ``.pyc`` files.

    The marketplace's hot-reload flow can rewrite a plugin file in less
    time than the filesystem's mtime resolution (one second on HFS+,
    two seconds on FAT). A stale ``.pyc`` with the same mtime as the
    newly-written source would shadow the update, so we keep plugin
    bytecode out of the cache entirely. Plugin code is small; the cost
    of re-parsing source on each load is negligible.
    """

    def set_data(self, *args, **kwargs):  # type: ignore[override]
        return None


def _import_plugin_module(folder: Path, module_name: str):
    """Import the plugin's ``__init__.py`` as a uniquely-named module.

    Plugins live in arbitrary folders, not under a Python package, so
    we use ``importlib.util.spec_from_file_location`` directly. The
    chosen module name uses a stable prefix so several plugins can
    coexist in ``sys.modules`` without colliding on common names like
    ``utils``.

    Any pre-existing ``__pycache__`` inside the plugin folder is purged
    before the load and we install a loader that never writes new
    bytecode (see ``_NoCacheSourceLoader``). Both pieces are required so
    a marketplace update to a plugin always runs the new source instead
    of a same-mtime ``.pyc`` from before the rewrite.
    """
    init_path = folder / "__init__.py"
    if not init_path.is_file():
        raise ValueError("plugin folder must contain __init__.py")

    pycache = folder / "__pycache__"
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)
    importlib.invalidate_caches()

    loader = _NoCacheSourceLoader(module_name, str(init_path))
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        loader=loader,
        submodule_search_locations=[str(folder.resolve())],
    )
    if spec is None or spec.loader is None:
        raise ValueError("could not build an import spec for the plugin")
    module = importlib.util.module_from_spec(spec)
    # Register before execution so the plugin can ``import`` its own
    # sibling modules via the standard ``from . import ...`` form.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def plugin_module_name(plugin_id: str) -> str:
    """Return the ``sys.modules`` key the loader uses for ``plugin_id``.

    Exposed so the host can drop the module on unload without
    duplicating the prefix convention.
    """
    return _MODULE_PREFIX + plugin_id


def read_plugin_manifest(folder: Path) -> Optional[dict]:
    """Return the parsed ``manifest.json`` for ``folder`` or ``None``.

    Read-only convenience for callers that want the full manifest
    (e.g. the marketplace UI rendering an installed plugin's rich
    metadata). The loader itself doesn't expose the manifest dict on
    ``PluginInfo`` because most callers only need the small post-load
    summary; this helper covers the rest.
    """
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return _read_manifest(manifest_path)
    except Exception:  # noqa: BLE001
        # Read failures are surfaced to the user at load time; here
        # the caller just gets None and renders a fallback.
        return None


# --------------------------------------------------------------------------- #
# Diagnostic logging                                                          #
# --------------------------------------------------------------------------- #

def _log_failure(folder: Path, exc: BaseException, *, phase: str) -> None:
    """Append a structured record of a plugin failure to ``plugin.log``.

    The log file is the primary diagnostic path for users who run the
    editor as a bundled GUI app with no stderr they can read. We also
    keep the stderr echo because dev builds and ``python -m`` invocations
    have a useful terminal.
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    record = (
        f"[{timestamp}] {phase} failure in {folder}\n"
        f"  {type(exc).__name__}: {exc}\n"
        + "".join(f"    {line}" for line in tb_text.splitlines(keepends=True))
        + "\n"
    )
    try:
        log_path = plugin_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(record)
    except OSError:
        # Log writes are best-effort; we never fail a plugin load
        # because we couldn't write the diagnostic file.
        pass
    # Echo to stderr for dev convenience. GUI users see nothing here
    # but that's why the file exists.
    print(record, file=sys.stderr, end="")
