"""Network error translation: ugly errnos become user-readable sentences."""

from __future__ import annotations

import socket
import urllib.error

import pytest

from plugin_marketplace import registry as registry_mod
from plugin_marketplace.registry import RegistryFetchError, fetch_index
from plugin_marketplace.models import RegistrySource


def _src(url: str = "https://example.invalid/index.json") -> RegistrySource:
    return RegistrySource(name="test", index_url=url, enabled=True, trusted=False)


# ──────────────────────────────────────────────────────────────────────
# _translate_url_error  (the heart of the friendly-error path)
# ──────────────────────────────────────────────────────────────────────

def test_dns_gaierror_becomes_friendly(monkeypatch):
    """A socket.gaierror with the macOS message must surface as a DNS
    error, not an errno code."""
    def boom(*a, **kw):
        raise urllib.error.URLError(
            socket.gaierror(8, "nodename nor servername provided, or not known")
        )
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_DNS
    assert "registry's address" in str(exc.value)
    assert "errno" not in str(exc.value).lower()


def test_dns_linux_message_classified(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("name or service not known")
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_DNS


def test_dns_windows_message_classified(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("getaddrinfo failed")
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_DNS


def test_timeout_becomes_friendly(monkeypatch):
    def boom(*a, **kw):
        raise TimeoutError()
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_TIMEOUT


def test_http_error_becomes_friendly(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.HTTPError(
            url="https://x/", code=503, msg="Service Unavailable",
            hdrs=None, fp=None,
        )
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_HTTP
    assert "503" in str(exc.value)


def test_non_https_rejected_with_friendly_message():
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src(url="http://insecure.example/index.json"))
    assert "HTTPS" in str(exc.value)


def test_generic_url_error_falls_back_to_network(monkeypatch):
    """Anything we can't classify becomes the generic "couldn't reach" path."""
    def boom(*a, **kw):
        raise urllib.error.URLError("connection reset by peer")
    monkeypatch.setattr(registry_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RegistryFetchError) as exc:
        fetch_index(_src())
    assert exc.value.kind == RegistryFetchError.KIND_NETWORK
