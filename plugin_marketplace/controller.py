"""Coordinator between the marketplace UI and the plugin runtime.

Sits between the Qt dialog and the lower-level registry/installer
modules. The dialog calls controller methods; the controller emits
plain-Python callbacks (no Qt signals) so the same logic can be
unit-tested without spinning up a window.

The controller never blocks the calling thread on the network - the
dialog's QThread workers are the ones that do I/O and they call the
controller methods that perform the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from plugin_system import (
    PluginInfo,
    PluginSettingsStore,
    read_plugin_manifest,
    user_plugins_dir,
)

from .installer import InstallError, InstallResult, install_plugin, uninstall_plugin
from .models import PluginListing, RegistrySource, filter_listings
from .registry import (
    DEFAULT_REGISTRY,
    FetchResult,
    fetch_all_indices,
    load_registry_sources,
    save_registry_sources,
)
from .social import (
    CommenterProfileFetcher,
    EngagementCache,
    EngagementFetcher,
    EngagementPublisher,
    Nip05Verifier,
    PluginAnchor,
)


class MarketplaceHost(Protocol):
    """The slice of MainWindow the controller calls into.

    Keeping this narrow lets controller logic stay testable without
    needing a real MainWindow.
    """

    def reload_plugin(self, folder: Path): ...

    def unload_plugin(self, plugin_id: str) -> bool: ...

    @property
    def loaded_plugins(self) -> List[PluginInfo]: ...


# ──────────────────────────────────────────────────────────────────────
# Catalog snapshot
# ──────────────────────────────────────────────────────────────────────

@dataclass
class CatalogSnapshot:
    """Combined result of fetching every enabled registry.

    The UI renders ``listings`` (one merged list, deduplicated by
    plugin_id with the first-seen winning) and ``fetch_errors`` (so
    the user knows which registries didn't answer).
    """

    fetched_at: float = 0.0
    listings: List[PluginListing] = field(default_factory=list)
    fetch_errors: Dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.listings


# ──────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────

class MarketplaceController:
    """All the marketplace logic that isn't Qt.

    Constructed once per marketplace dialog opening. Holds the
    fetched catalog, the user's registry list, and the
    installed-plugin index so the dialog can ask
    "is this plugin already installed?" without re-implementing it.
    """

    def __init__(
        self,
        *,
        host: MarketplaceHost,
        settings: PluginSettingsStore,
        engagement_cache: Optional[EngagementCache] = None,
        engagement_fetcher: Optional[EngagementFetcher] = None,
        engagement_publisher: Optional[EngagementPublisher] = None,
        nip05_verifier: Optional[Nip05Verifier] = None,
        commenter_profile_fetcher: Optional[CommenterProfileFetcher] = None,
        outbox_router=None,
    ) -> None:
        self._host = host
        self._settings = settings
        self._catalog = CatalogSnapshot()
        # The social stack is optional so non-Nostr tests can still
        # construct a controller. When present the marketplace UI
        # uses the live Nostr data; when absent it falls back to the
        # manifest values advertised on each listing.
        self._engagement_cache = engagement_cache
        self._engagement_fetcher = engagement_fetcher
        self._engagement_publisher = engagement_publisher
        self._nip05_verifier = nip05_verifier
        self._commenter_profile_fetcher = commenter_profile_fetcher
        self._outbox_router = outbox_router

    # ---- Social stack accessors ------------------------------------------

    @property
    def engagement_cache(self) -> Optional[EngagementCache]:
        return self._engagement_cache

    @property
    def engagement_fetcher(self) -> Optional[EngagementFetcher]:
        return self._engagement_fetcher

    @property
    def engagement_publisher(self) -> Optional[EngagementPublisher]:
        return self._engagement_publisher

    @property
    def nip05_verifier(self) -> Optional[Nip05Verifier]:
        return self._nip05_verifier

    @property
    def commenter_profile_fetcher(self) -> Optional[CommenterProfileFetcher]:
        return self._commenter_profile_fetcher

    @property
    def outbox_router(self):
        """The NIP-65 router used by every social I/O path.

        Returns ``None`` when no relay-list cache is configured (for
        example in lightweight tests that don't construct the full
        Nostr stack). Production callers should treat ``None`` as
        "use seed relays only."
        """
        return self._outbox_router

    def listing_by_anchor(self, anchor: "PluginAnchor"):
        """Return the listing whose anchor matches, or ``None``.

        Unified lookup across installed and discoverable listings so
        callers no longer have to scan two lists by plugin_id. The
        anchor's plugin_id is the discriminator: when multiple
        sources report the same plugin_id we prefer the installed
        copy because that's what the user is interacting with.
        """
        for listing in self.installed_listings():
            if listing.plugin_id == anchor.plugin_id:
                return listing
        for listing in self.current_catalog().listings:
            if listing.plugin_id == anchor.plugin_id:
                return listing
        return None

    def anchor_for(self, listing: PluginListing) -> Optional[PluginAnchor]:
        """Decode the listing's ``nostr_naddr`` into a usable anchor.

        Returns ``None`` for any listing without a parseable Nostr
        coordinate; the social panel renders a "no listing event"
        notice in that case so the user understands why engagement
        is unavailable for this plugin.
        """
        if not listing.nostr_naddr:
            return None
        return _anchor_from_naddr_or_coord(listing.nostr_naddr)

    # ---- Registry sources -------------------------------------------------

    def registry_sources(self) -> List[RegistrySource]:
        return load_registry_sources(self._settings)

    def set_registry_sources(self, sources: List[RegistrySource]) -> None:
        save_registry_sources(self._settings, sources)

    # ---- Catalog ----------------------------------------------------------

    def refresh_catalog(self) -> CatalogSnapshot:
        """Fetch every enabled registry and rebuild the catalog snapshot.

        Called from a worker thread by the dialog. Returns the
        snapshot so the caller can hand it back to the UI thread.
        """
        import time
        results = fetch_all_indices(self.registry_sources())
        merged = _merge_results(results)
        self._catalog = CatalogSnapshot(
            fetched_at=time.time(),
            listings=merged,
            fetch_errors={
                r.source.name: r.error for r in results if r.error
            },
        )
        return self._catalog

    def current_catalog(self) -> CatalogSnapshot:
        return self._catalog

    def filter_catalog(
        self,
        *,
        query: str = "",
        category: Optional[str] = None,
        free_only: bool = False,
    ) -> List[PluginListing]:
        return filter_listings(
            self._catalog.listings,
            query=query,
            category=category,
            free_only=free_only,
        )

    # ---- Installed inventory ---------------------------------------------

    def installed_plugin_ids(self) -> List[str]:
        """Return the ids of plugins currently loaded by the editor."""
        return [info.plugin_id for info in self._host.loaded_plugins]

    def is_installed(self, plugin_id: str) -> bool:
        return plugin_id in self.installed_plugin_ids()

    def installed_listings(self) -> List[PluginListing]:
        """Build full ``PluginListing`` records for every loaded plugin.

        Reads each plugin's ``manifest.json`` from disk so the
        marketplace's Installed view renders bundled and user-installed
        plugins with the same richness as registry plugins (author,
        long description, license, tags, homepage, etc.). For plugins
        that already exist in the fetched catalog, the catalog listing
        is preferred because it carries fields the manifest doesn't
        (like ``download_url`` and ``sha256``).
        """
        catalog_by_id = {li.plugin_id: li for li in self._catalog.listings}
        out: List[PluginListing] = []
        for info in self._host.loaded_plugins:
            if info.plugin_id in catalog_by_id:
                out.append(catalog_by_id[info.plugin_id])
                continue
            out.append(_listing_from_manifest(info))
        return out

    def installed_version(self, plugin_id: str) -> Optional[str]:
        for info in self._host.loaded_plugins:
            if info.plugin_id == plugin_id:
                return info.version
        return None

    def has_update(self, listing: PluginListing) -> bool:
        """Return True if ``listing`` is a newer version than what's installed."""
        installed = self.installed_version(listing.plugin_id)
        if installed is None:
            return False
        return _compare_versions(listing.version, installed) > 0

    # ---- Install / Uninstall ---------------------------------------------

    def install(
        self,
        listing: PluginListing,
        *,
        progress_cb=None,
    ) -> InstallResult:
        """Download + verify + install ``listing``, then hot-load it.

        Called from a worker thread. The hot-load step touches Qt
        (creating menu actions), so the caller must hop back to the
        UI thread before invoking ``self._host.reload_plugin``.

        Returns the install result; the caller is responsible for
        firing the reload on the main thread.
        """
        return install_plugin(listing, progress_cb=progress_cb)

    def hot_reload(self, install_path: Path) -> None:
        """Tell the host to reload the plugin at ``install_path``.

        Must run on the UI thread.
        """
        self._host.reload_plugin(install_path)

    def uninstall(self, plugin_id: str) -> bool:
        """Unload the plugin then remove its folder from disk.

        Bundled plugins live outside ``user_plugins_dir`` so they're
        never removed by this call. The caller (UI) checks first and
        shows a different button label for bundled plugins.
        """
        # Unload BEFORE remove so the import / sys.path / menu state
        # is already gone by the time the directory disappears.
        self._host.unload_plugin(plugin_id)
        return uninstall_plugin(plugin_id)

    def is_bundled(self, plugin_id: str) -> bool:
        """Bundled plugins ship with the editor and live outside the
        user plugins root. We never uninstall them - the marketplace
        UI hides the uninstall button for these."""
        for info in self._host.loaded_plugins:
            if info.plugin_id == plugin_id:
                return info.source == "bundled"
        return False


# ──────────────────────────────────────────────────────────────────────
# Merge + version helpers
# ──────────────────────────────────────────────────────────────────────

def _merge_results(results: List[FetchResult]) -> List[PluginListing]:
    """Combine listings from every registry, deduplicated by plugin_id.

    First-seen wins so the user's primary (top-of-list) registry can
    override a secondary registry's stale entry.
    """
    seen: Dict[str, PluginListing] = {}
    for result in results:
        for listing in result.listings:
            if listing.plugin_id in seen:
                continue
            seen[listing.plugin_id] = listing
    # Stable ordering by name for the UI.
    return sorted(seen.values(), key=lambda p: (p.name.lower(), p.plugin_id))


def _listing_from_manifest(info: PluginInfo) -> PluginListing:
    """Synthesize a ``PluginListing`` from a loaded plugin's manifest.

    Used in the marketplace's Installed view when the plugin isn't in
    the fetched catalog (always true for bundled plugins, sometimes
    true for sideloaded ones). Fields the manifest doesn't carry
    (``download_url``, ``sha256``) are filled with safe placeholders;
    the marketplace UI never uses them for installed plugins.
    """
    manifest = read_plugin_manifest(info.folder) or {}

    def _str(key: str, default: str = "") -> str:
        v = manifest.get(key, default)
        return v.strip() if isinstance(v, str) else default

    def _tuple(key: str) -> tuple[str, ...]:
        v = manifest.get(key)
        if not isinstance(v, list):
            return ()
        return tuple(s.strip() for s in v if isinstance(s, str) and s.strip())

    def _int_tuple(key: str) -> tuple[int, ...]:
        v = manifest.get(key)
        if not isinstance(v, list):
            return ()
        out: list[int] = []
        for item in v:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                out.append(item)
        return tuple(out)

    def _int(key: str, default: int = 0) -> int:
        v = manifest.get(key, default)
        if isinstance(v, bool):
            return default
        if isinstance(v, int):
            return v
        return default

    # Default description mirrors what the synthetic version used so a
    # plugin with a bare-minimum manifest still reads correctly.
    fallback_description = (
        "Bundled with the editor."
        if info.source == "bundled"
        else "Installed locally."
    )

    icon_path = _resolve_icon_path(info.folder, manifest.get("icon"))

    return PluginListing(
        plugin_id=info.plugin_id,
        name=info.name,
        version=info.version,
        # Placeholders; never read for installed listings.
        download_url="local://" + info.plugin_id,
        sha256="0" * 64,
        author=_str("author"),
        description=_str("description", default=fallback_description),
        long_description=_str("long_description"),
        homepage=_str("homepage"),
        license=_str("license"),
        tags=_tuple("tags"),
        category=_str("category", default="other") or "other",
        price_sats=_int("price_sats", default=0),
        lightning_address=_str("lightning_address"),
        api_version=_int("api_version", default=1),
        min_editor_version=_str("min_editor_version"),
        platforms=_tuple("platforms"),
        icon_url=_str("icon_url"),
        icon_path=icon_path,
        nostr_naddr=_str("nostr_naddr"),
        source_name=info.source,
        rating_avg=_clamped_float(manifest.get("rating_avg"), lo=0.0, hi=5.0),
        rating_count=_int("rating_count", default=0),
        downloads=_int("downloads", default=0),
        comments_count=_int("comments_count", default=0),
        zaps_sats=_int("zaps_sats", default=0),
        updated_at=_str("updated_at"),
        declared_capabilities=_tuple("declared_capabilities"),
        declared_kinds=_int_tuple("declared_kinds"),
    )


def _clamped_float(raw, *, lo: float, hi: float, default: float = 0.0) -> float:
    if isinstance(raw, bool):
        return default
    if not isinstance(raw, (int, float)):
        return default
    return max(lo, min(hi, float(raw)))


def _resolve_icon_path(folder: Path, raw: object) -> str:
    """Resolve ``manifest["icon"]`` to an absolute filesystem path.

    Rules:
      - Must be a non-empty string and resolve to a file inside the
        plugin's folder. Path traversal (``..``) and absolute paths
        are rejected so a malicious manifest can't make us read
        arbitrary files.
      - If the field is missing, malformed, or the file doesn't exist,
        return an empty string and let the UI render a generated
        fallback avatar.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    relative = raw.strip()
    if relative.startswith("/") or relative.startswith("\\"):
        return ""
    candidate = (folder / relative).resolve()
    try:
        candidate.relative_to(folder.resolve())
    except ValueError:
        return ""
    if not candidate.is_file():
        return ""
    return str(candidate)


def _anchor_from_naddr_or_coord(value: str) -> Optional[PluginAnchor]:
    """Accept either a ``kind:pubkey:d-tag`` coord or a bech32 naddr.

    Registries advertise listing coordinates in whichever form is most
    convenient for them: a coord triple for HTTPS indexes, an naddr
    for Nostr-native indexes. The marketplace tolerates either so a
    plugin's social layer keeps working whichever route the user
    discovered it through.

    Returns ``None`` for unparseable input so the social panel can
    fall back to its "no listing event" notice rather than crashing.
    """
    if not value:
        return None
    if value.startswith("naddr"):
        from nostr.bech32 import decode_naddr
        try:
            identifier, author_hex, kind, _relays = decode_naddr(value)
        except Exception:  # noqa: BLE001
            return None
        return PluginAnchor(kind=kind, author_pubkey=author_hex, plugin_id=identifier)
    return PluginAnchor.parse(value)


def _compare_versions(a: str, b: str) -> int:
    """Compare semver-ish version strings.

    Falls back to lexical comparison when the strings aren't dot-
    separated integers, which keeps the function honest for
    pre-release suffixes like "1.0.0-rc1". A marketplace badge that
    sometimes says "Update available" too eagerly is much better
    than one that quietly skips a real update.
    """
    def parts(v: str):
        out: List[tuple[int, str]] = []
        for chunk in v.split("."):
            try:
                out.append((0, "")); out[-1] = (int(chunk), "")
            except ValueError:
                out.append((-1, chunk))
        return out
    pa, pb = parts(a), parts(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0
