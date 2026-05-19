"""Typed records for the marketplace.

The marketplace talks to two sources:

  - **Registry index**: a JSON document hosted on HTTPS (or a local
    file in dev) that lists every plugin a registry advertises.
  - **Plugin listing**: one entry in that index, expanded into a
    typed record we render in the UI and pass to the installer.

The on-the-wire schema is intentionally small - every field except
``id``, ``version``, and ``download_url`` is optional - so a registry
maintainer with one plugin and no website can publish a useful index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


# Registry index schema version. Bump when fields are renamed or
# removed; additive changes (new optional fields) don't need a bump.
REGISTRY_SCHEMA_VERSION: int = 1


# ──────────────────────────────────────────────────────────────────────
# Categories shown as filter chips. Kept short, mutually-exclusive and
# generic so a registry maintainer can place their plugin without
# guessing taxonomy. Plugins may belong to one category; if missing,
# the UI shows them under "Other".
# ──────────────────────────────────────────────────────────────────────
CATEGORIES: tuple[str, ...] = (
    "editor",
    "nostr",
    "wallets",
    "publishing",
    "import-export",
    "themes",
    "developer-tools",
    "other",
)


@dataclass(frozen=True)
class RegistrySource:
    """A registry endpoint the editor knows about.

    Multiple sources can coexist (a curated default + a power user's
    pin) and the UI lets the user enable/disable each. The default
    registry ships in code; everything else is user-added.

    Two transports are supported:

      - HTTPS index (``index_url``): a JSON file matching the
        marketplace's registry schema.
      - Nostr curator index (``naddr``): a kind:30750 addressable
        event whose ``a`` tags list the kind:30700 plugin listings
        the curator endorses. ``naddr`` takes precedence if both are
        set, so a curator can migrate by editing one row.
    """

    name: str
    index_url: str = ""
    naddr: str = ""
    enabled: bool = True
    trusted: bool = False  # bypass install-time warning dialog

    @property
    def is_nostr(self) -> bool:
        """``True`` when this source resolves via a Nostr naddr.

        Used by the fetcher to pick HTTPS or relay transport. We
        prefer ``naddr`` over ``index_url`` when both are present so
        a curator can flip transports without forcing every reader to
        re-add the source.
        """
        return bool(self.naddr.strip())


@dataclass(frozen=True)
class PluginListing:
    """One plugin's entry in a registry index, fully parsed.

    ``download_url`` points at a ``.zip`` containing the plugin folder
    laid out exactly as it should appear inside ``user_plugins_dir``.
    ``sha256`` is the hex digest of that zip; the installer refuses
    any download whose digest doesn't match, so a tampered mirror
    can't inject code without breaking the digest.
    """

    plugin_id: str
    name: str
    version: str
    download_url: str
    sha256: str

    # Optional, but heavily used by the UI:
    author: str = ""
    description: str = ""
    long_description: str = ""
    homepage: str = ""
    license: str = ""
    tags: tuple[str, ...] = ()
    category: str = "other"
    price_sats: int = 0  # 0 == free
    lightning_address: str = ""
    api_version: int = 1
    min_editor_version: str = ""
    platforms: tuple[str, ...] = ()  # empty == all
    icon_url: str = ""
    # Absolute path to an icon file already on disk. Set for installed
    # plugins by the controller after resolving manifest["icon"] relative
    # to the plugin folder. Empty for plain registry listings, which carry
    # ``icon_url`` for asynchronous download instead.
    icon_path: str = ""
    screenshots: tuple[str, ...] = ()
    nostr_naddr: str = ""  # canonical Nostr coordinate, for M4 social layer
    source_name: str = ""  # which RegistrySource served this listing

    # ─── Declared permissions (mirrored from manifest.json) ──────────
    # Surfaced in the install dialog so users see what a plugin will be
    # allowed to do before they grant the install. Empty tuples mean
    # "no extra permissions requested"; older listings without these
    # fields land here too.
    declared_capabilities: tuple[str, ...] = ()
    declared_kinds: tuple[int, ...] = ()

    # ─── Engagement metadata ─────────────────────────────────────────
    # Registries advertise these so the marketplace can render rich
    # at-a-glance information the way VS Code Marketplace and App Store
    # do. Pre-M4, the Nostr-derived fields (rating, comments, zaps) come
    # from whatever the registry chose to cache; from M4 on the
    # marketplace will fetch them directly from relays.
    rating_avg: float = 0.0     # average star rating, 0.0..5.0
    rating_count: int = 0       # how many people rated it
    downloads: int = 0          # registry-reported install count
    comments_count: int = 0     # Nostr NIP-22 comments (kind 1111)
    zaps_sats: int = 0          # total sats zapped to this plugin
    updated_at: str = ""        # ISO 8601 timestamp of the listing's last update

    @property
    def is_free(self) -> bool:
        return self.price_sats <= 0

    @property
    def display_category(self) -> str:
        return self.category if self.category in CATEGORIES else "other"


# ──────────────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────────────

class RegistryParseError(ValueError):
    """Raised when a registry index can't be parsed.

    Distinct from a network or HTTP error so the UI can show a clearer
    "this registry's index is broken" message vs. "couldn't reach the
    registry".
    """


def parse_registry_index(
    raw: dict,
    *,
    source_name: str = "",
) -> List[PluginListing]:
    """Parse a registry index document into ``PluginListing`` records.

    The function is intentionally lenient: extra unknown fields are
    ignored (so a future schema version can add fields without
    breaking older editors). Missing required fields on a single
    plugin entry skip just that entry, not the whole index.

    ``source_name`` is stamped onto each listing so the UI can show
    "from <registry>" badges.
    """
    if not isinstance(raw, dict):
        raise RegistryParseError("registry index must be a JSON object")

    schema = raw.get("schema_version", 1)
    if not isinstance(schema, int) or schema < 1:
        raise RegistryParseError(f"invalid schema_version {schema!r}")
    if schema > REGISTRY_SCHEMA_VERSION:
        # Forward-compat: keep parsing but record nothing if we can't
        # interpret the schema. The UI surfaces this as a "newer index"
        # warning so the user knows to update.
        raise RegistryParseError(
            f"registry uses schema_version {schema}, "
            f"this editor understands up to {REGISTRY_SCHEMA_VERSION}"
        )

    plugins_raw = raw.get("plugins")
    if not isinstance(plugins_raw, list):
        raise RegistryParseError("registry index must contain a 'plugins' list")

    out: List[PluginListing] = []
    for entry in plugins_raw:
        if not isinstance(entry, dict):
            continue
        listing = _parse_listing(entry, source_name=source_name)
        if listing is not None:
            out.append(listing)
    return out


def _parse_listing(
    entry: dict,
    *,
    source_name: str,
) -> Optional[PluginListing]:
    """Parse one entry. Returns None for anything missing the required
    fields rather than aborting the whole index parse."""

    try:
        plugin_id = _required_str(entry, "id")
        version = _required_str(entry, "version")
        download_url = _required_str(entry, "download_url")
        sha256 = _required_str(entry, "sha256")
    except RegistryParseError:
        return None

    if not _looks_like_https(download_url):
        return None  # silently drop non-HTTPS downloads
    if not _looks_like_sha256(sha256):
        return None

    name = _optional_str(entry, "name", default=plugin_id)
    return PluginListing(
        plugin_id=plugin_id,
        name=name,
        version=version,
        download_url=download_url,
        sha256=sha256.lower(),
        author=_optional_str(entry, "author"),
        description=_optional_str(entry, "description"),
        long_description=_optional_str(entry, "long_description"),
        homepage=_optional_str(entry, "homepage"),
        license=_optional_str(entry, "license"),
        tags=_optional_tuple(entry, "tags"),
        category=_optional_str(entry, "category", default="other"),
        price_sats=_optional_int(entry, "price_sats", default=0),
        lightning_address=_optional_str(entry, "lightning_address"),
        api_version=_optional_int(entry, "api_version", default=1),
        min_editor_version=_optional_str(entry, "min_editor_version"),
        platforms=_optional_tuple(entry, "platforms"),
        icon_url=_optional_str(entry, "icon_url"),
        screenshots=_optional_tuple(entry, "screenshots"),
        nostr_naddr=_optional_str(entry, "nostr_naddr"),
        source_name=source_name,
        rating_avg=_optional_float(entry, "rating_avg", lo=0.0, hi=5.0),
        rating_count=_optional_int(entry, "rating_count", default=0),
        downloads=_optional_int(entry, "downloads", default=0),
        comments_count=_optional_int(entry, "comments_count", default=0),
        zaps_sats=_optional_int(entry, "zaps_sats", default=0),
        updated_at=_optional_str(entry, "updated_at"),
        declared_capabilities=_optional_tuple(entry, "declared_capabilities"),
        declared_kinds=_optional_int_tuple(entry, "declared_kinds"),
    )


# ──────────────────────────────────────────────────────────────────────
# Field helpers
# ──────────────────────────────────────────────────────────────────────

def _required_str(d: dict, key: str) -> str:
    val = d.get(key)
    if not isinstance(val, str) or not val.strip():
        raise RegistryParseError(f"missing or invalid {key!r}")
    return val.strip()


def _optional_str(d: dict, key: str, *, default: str = "") -> str:
    val = d.get(key)
    if not isinstance(val, str):
        return default
    return val.strip()


def _optional_int(d: dict, key: str, *, default: int = 0) -> int:
    val = d.get(key, default)
    if isinstance(val, bool):
        return default
    if not isinstance(val, int):
        return default
    return val


def _optional_float(d: dict, key: str, *, lo: float, hi: float, default: float = 0.0) -> float:
    """Parse an optional float and clamp to a sensible range.

    Rating fields in particular are scored 0..5; anything outside is a
    registry bug (or worse, a manipulation attempt). Clamping silently
    is safer than rejecting the whole listing.
    """
    val = d.get(key, default)
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        result = float(val)
    else:
        return default
    return max(lo, min(hi, result))


def _optional_tuple(d: dict, key: str) -> tuple[str, ...]:
    val = d.get(key)
    if not isinstance(val, list):
        return ()
    return tuple(s.strip() for s in val if isinstance(s, str) and s.strip())


def _optional_int_tuple(d: dict, key: str) -> tuple[int, ...]:
    """Parse an optional list of ints (e.g. Nostr ``declared_kinds``).

    Tolerates a missing or malformed list (returns empty tuple) so a
    badly-formed listing field never aborts the whole index parse. Each
    valid integer is preserved in source order, with duplicates kept so
    the UI can show them verbatim if it chooses.
    """
    val = d.get(key)
    if not isinstance(val, list):
        return ()
    out: list[int] = []
    for item in val:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
    return tuple(out)


def _looks_like_https(url: str) -> bool:
    """Reject ``file://`` and ``http://`` for downloads.

    The marketplace deliberately refuses cleartext HTTP for plugin
    payloads - even with a sha256 check, downloading code over an
    unauthenticated channel invites confusion at install time about
    *who* served the bytes. A registry index can advertise alternative
    schemes for non-payload links (homepage, etc.); only the
    ``download_url`` is restricted.
    """
    lowered = url.lower()
    return lowered.startswith("https://")


_SHA256_HEX_LEN = 64


def _looks_like_sha256(s: str) -> bool:
    if len(s) != _SHA256_HEX_LEN:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


# ──────────────────────────────────────────────────────────────────────
# Filtering helpers used by the UI
# ──────────────────────────────────────────────────────────────────────

def filter_listings(
    listings: Iterable[PluginListing],
    *,
    query: str = "",
    category: Optional[str] = None,
    free_only: bool = False,
) -> List[PluginListing]:
    """Apply the marketplace UI's filter chips.

    Pulled out of the UI so we can test the matching rules without
    instantiating Qt and so a future "search from CLI" path can reuse
    the same logic.
    """
    q = query.strip().lower()
    out: List[PluginListing] = []
    for listing in listings:
        if free_only and not listing.is_free:
            continue
        if category and category != "all" and listing.display_category != category:
            continue
        if q:
            hay = " ".join((
                listing.plugin_id,
                listing.name,
                listing.author,
                listing.description,
                " ".join(listing.tags),
            )).lower()
            if q not in hay:
                continue
        out.append(listing)
    return out
