"""Fetch + parse plugin registry indices.

Multiple registries can be configured. The default one ships in code
(``DEFAULT_REGISTRY``) and is the only one the user trusts unless
they explicitly add another. The user's list of registries is
persisted in the plugin settings store under the reserved
``__marketplace__`` namespace.

Network code is deliberately isolated from the Qt UI: the UI calls
``fetch_index()`` from a worker thread and gets back parsed
``PluginListing`` records. No Qt imports here.

Errors surfaced to the UI are translated into human-readable
sentences. The user must never see "Errno 8" or a stack-traced
``URLError``. Each category maps to a friendly explanation and,
where applicable, a hint about what to try.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from plugin_system import PluginSettingsStore

from .models import (
    PluginListing,
    RegistryParseError,
    RegistrySource,
    parse_registry_index,
)


# Reserved settings namespace for marketplace state. Plugins can never
# write to a settings_scope("__marketplace__") because their scope is
# always their plugin_id (which is validated to lowercase ASCII).
_MARKETPLACE_NS = "__marketplace__"
_REGISTRIES_KEY = "registries"


# Default registry. Empty index_url for now - the project's hosted
# registry comes online in M8. The placeholder still lets the
# marketplace UI render an empty state gracefully.
DEFAULT_REGISTRY = RegistrySource(
    name="my-editor official",
    index_url="https://plugins.my-editor.example/index.json",
    enabled=True,
    trusted=True,
)


# Conservative timeouts. A slow / hanging registry must not freeze
# the marketplace dialog.
_CONNECT_TIMEOUT_S: float = 8.0
_READ_TIMEOUT_S: float = 15.0
_MAX_INDEX_BYTES: int = 4 * 1024 * 1024  # 4 MiB - well over any sane index size


# ──────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────

class RegistryFetchError(RuntimeError):
    """Raised on network / HTTP failure when fetching a registry index.

    Distinct from ``RegistryParseError`` so the UI can choose a
    different message ("couldn't reach the registry" vs. "the
    registry's index is broken").

    The ``message`` is always pre-formatted for end-user display
    (no errnos, no Python tracebacks). ``kind`` is a coarse category
    the UI can use to pick an icon and an action ("Retry" vs.
    "Check connection" vs. "Open registry settings").
    """

    # Coarse categories for UI dispatch. Keep this list small; the UI
    # only branches on a handful of distinct empty-state copies.
    KIND_DNS = "dns"
    KIND_NETWORK = "network"
    KIND_TIMEOUT = "timeout"
    KIND_HTTP = "http"
    KIND_TLS = "tls"
    KIND_SIZE = "size"
    KIND_OTHER = "other"

    def __init__(self, message: str, *, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


# ──────────────────────────────────────────────────────────────────────
# Source persistence
# ──────────────────────────────────────────────────────────────────────

def load_registry_sources(settings: PluginSettingsStore) -> List[RegistrySource]:
    """Return the user-configured registry list, with the default first.

    The default registry is always present in the returned list; if
    the user disabled or removed it from their saved state we still
    show it (disabled if they chose so) so they can re-enable without
    having to remember the URL.
    """
    raw = settings.get(_MARKETPLACE_NS, _REGISTRIES_KEY, default=None)
    user_sources: List[RegistrySource] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            index_url = entry.get("index_url") or ""
            naddr = entry.get("naddr") or ""
            if not isinstance(index_url, str) or not isinstance(naddr, str):
                continue
            if not index_url.strip() and not naddr.strip():
                continue
            user_sources.append(RegistrySource(
                name=name.strip(),
                index_url=index_url.strip(),
                naddr=naddr.strip(),
                enabled=bool(entry.get("enabled", True)),
                trusted=bool(entry.get("trusted", False)),
            ))

    # Replace the default's row if the user persisted preferences for it,
    # otherwise prepend the default. Either way the default's identity
    # is preserved so the UI can mark it as "official".
    out: List[RegistrySource] = []
    have_default = False
    for src in user_sources:
        if src.index_url == DEFAULT_REGISTRY.index_url:
            out.append(RegistrySource(
                name=DEFAULT_REGISTRY.name,
                index_url=DEFAULT_REGISTRY.index_url,
                enabled=src.enabled,
                trusted=True,  # default is always trusted
            ))
            have_default = True
        else:
            out.append(src)
    if not have_default:
        out.insert(0, DEFAULT_REGISTRY)
    return out


def save_registry_sources(
    settings: PluginSettingsStore,
    sources: List[RegistrySource],
) -> None:
    """Persist the user's registry list."""
    payload = [
        {
            "name": s.name,
            "index_url": s.index_url,
            "naddr": s.naddr,
            "enabled": s.enabled,
            "trusted": s.trusted,
        }
        for s in sources
    ]
    settings.set(_MARKETPLACE_NS, _REGISTRIES_KEY, payload)


# ──────────────────────────────────────────────────────────────────────
# Fetching
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FetchResult:
    """One registry's contribution to the combined catalog."""

    source: RegistrySource
    listings: List[PluginListing]
    error: Optional[str] = None  # filled when fetch or parse failed


def fetch_all_indices(sources: List[RegistrySource]) -> List[FetchResult]:
    """Fetch every enabled source, accumulating successes and failures.

    Each source is fetched independently - one unreachable registry
    can't hide a working one. The UI surfaces partial failures
    (broken-link icon next to the offending registry) rather than
    showing nothing.
    """
    out: List[FetchResult] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            listings = fetch_index(source)
            out.append(FetchResult(source=source, listings=listings))
        except (RegistryFetchError, RegistryParseError) as exc:
            out.append(FetchResult(source=source, listings=[], error=str(exc)))
    return out


def fetch_index(source: RegistrySource) -> List[PluginListing]:
    """Fetch and parse a single registry's index.

    Routes ``naddr`` sources through the Nostr curator-index reader
    when a relay-pool callable is registered (via
    ``register_nostr_resolver``). HTTPS sources continue to use the
    standard urllib path.

    Raises:
        RegistryFetchError: network/HTTP error.
        RegistryParseError: index decoded but didn't match the schema.
    """
    if source.is_nostr:
        resolver = _nostr_resolver
        if resolver is None:
            raise RegistryFetchError(
                "This source uses a Nostr curator index, but no relay "
                "pool is available to resolve it.",
                kind=RegistryFetchError.KIND_OTHER,
            )
        return resolver(source)
    if not source.index_url:
        raise RegistryFetchError(
            "Registry source has neither an HTTPS URL nor an naddr.",
            kind=RegistryFetchError.KIND_OTHER,
        )
    raw_bytes = _http_get_json(source.index_url)
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryParseError(f"index is not valid JSON: {exc}") from exc
    return parse_registry_index(data, source_name=source.name)


# Pluggable resolver for ``naddr`` sources. The editor's main window
# registers a callable that walks the relay pool synchronously (for the
# worker thread that drives ``fetch_all_indices``) and returns parsed
# listings. We hold it at module scope so the registry module stays
# free of Qt imports.
_nostr_resolver: Optional[callable] = None  # type: ignore[name-defined]


def register_nostr_resolver(resolver) -> None:
    """Install a callable that resolves Nostr-naddr sources synchronously.

    The callable receives a ``RegistrySource`` and must return a list of
    ``PluginListing`` records. Implementations live in ``main_window``
    so they can capture the relay pool and outbox router.
    """
    global _nostr_resolver
    _nostr_resolver = resolver


# ──────────────────────────────────────────────────────────────────────
# Low-level HTTP
# ──────────────────────────────────────────────────────────────────────

def _http_get_json(url: str) -> bytes:
    """GET ``url`` and return the body bytes, with size + timeout caps.

    Uses ``urllib`` rather than ``requests`` so the editor doesn't pick
    up a heavy transitive dependency. The downside is that ``urllib``
    follows redirects across schemes by default; we lock it to HTTPS.

    Errors are translated into ``RegistryFetchError`` with a friendly
    message + a coarse ``kind`` so the UI can render a sensible empty
    state. The user must never see "Errno 8" or a urllib traceback.
    """
    if not url.lower().startswith("https://"):
        raise RegistryFetchError(
            "Registry URL must use HTTPS.",
            kind=RegistryFetchError.KIND_OTHER,
        )

    request = urllib.request.Request(
        url,
        headers={
            # Polite identification - registries can rate-limit on UA.
            "User-Agent": "my-editor-plugin-marketplace/1.0",
            "Accept": "application/json",
        },
    )
    # Use a default SSL context so we get the platform's CA store on
    # all three OSes. Windows + macOS keychains, Linux /etc/ssl/certs.
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(
            request,
            timeout=_CONNECT_TIMEOUT_S,
            context=ctx,
        ) as resp:
            if resp.status != 200:
                raise RegistryFetchError(
                    f"Registry returned HTTP {resp.status}.",
                    kind=RegistryFetchError.KIND_HTTP,
                )
            ctype = resp.headers.get("Content-Type", "")
            if "text/html" in ctype.lower():
                raise RegistryFetchError(
                    "Registry returned a web page instead of an index file. "
                    "The URL may be wrong.",
                    kind=RegistryFetchError.KIND_HTTP,
                )
            body = resp.read(_MAX_INDEX_BYTES + 1)
            if len(body) > _MAX_INDEX_BYTES:
                raise RegistryFetchError(
                    "Registry index is unexpectedly large; refusing to load it.",
                    kind=RegistryFetchError.KIND_SIZE,
                )
            return body
    except urllib.error.HTTPError as exc:
        raise RegistryFetchError(
            f"Registry returned HTTP {exc.code} {exc.reason}.",
            kind=RegistryFetchError.KIND_HTTP,
        ) from exc
    except urllib.error.URLError as exc:
        raise _translate_url_error(exc) from exc
    except TimeoutError as exc:
        raise RegistryFetchError(
            "Registry took too long to respond.",
            kind=RegistryFetchError.KIND_TIMEOUT,
        ) from exc
    except ssl.SSLError as exc:
        raise RegistryFetchError(
            f"Could not verify the registry's TLS certificate: {exc}.",
            kind=RegistryFetchError.KIND_TLS,
        ) from exc
    except OSError as exc:
        raise RegistryFetchError(
            "Couldn't reach the registry. Check your connection.",
            kind=RegistryFetchError.KIND_NETWORK,
        ) from exc


def _translate_url_error(exc: urllib.error.URLError) -> RegistryFetchError:
    """Convert a ``URLError`` into a user-readable RegistryFetchError.

    The most common failure here is DNS lookup failing because the
    user has no internet, the registry's hostname is wrong, or (as
    today) the default registry hasn't been hosted yet - surfaced by
    libc as "Errno 8 nodename nor servername provided". We hide that
    behind plain language.
    """
    reason = exc.reason
    # ``reason`` is either a string or an exception wrapping one.
    inner = reason if not isinstance(reason, BaseException) else reason
    text = str(inner).lower()
    # Match common DNS-failure errnos / messages across platforms.
    dns_hits = (
        "nodename nor servername",  # macOS / BSD
        "name or service not known",  # Linux
        "getaddrinfo failed",  # Windows
        "no address associated with hostname",
    )
    if isinstance(inner, socket.gaierror) or any(h in text for h in dns_hits):
        return RegistryFetchError(
            "Couldn't find the registry's address. Either the URL is wrong "
            "or this computer is offline.",
            kind=RegistryFetchError.KIND_DNS,
        )
    if "timed out" in text:
        return RegistryFetchError(
            "Registry took too long to respond.",
            kind=RegistryFetchError.KIND_TIMEOUT,
        )
    if "ssl" in text or "certificate" in text:
        return RegistryFetchError(
            "Could not verify the registry's TLS certificate.",
            kind=RegistryFetchError.KIND_TLS,
        )
    # Generic fallback. Keep the wording warm and not jargon-y.
    return RegistryFetchError(
        "Couldn't reach the registry. Check your connection and try again.",
        kind=RegistryFetchError.KIND_NETWORK,
    )
