# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``nostr.blossom.settings.BlossomSettings``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nostr.blossom import settings
from nostr.blossom.servers import DEFAULT_BLOSSOM_SERVERS


def _store(tmp_path: Path) -> settings.BlossomSettings:
    return settings.BlossomSettings(path=tmp_path / "blossom_servers.json")


def test_fresh_install_uses_defaults(tmp_path):
    s = _store(tmp_path)
    assert s.configured_servers == list(DEFAULT_BLOSSOM_SERVERS)
    assert s.primary == DEFAULT_BLOSSOM_SERVERS[0]
    assert s.custom_servers == []


def test_add_server_materializes_defaults(tmp_path):
    s = _store(tmp_path)
    s.add_server("https://example.com")
    assert s.custom_servers[: len(DEFAULT_BLOSSOM_SERVERS)] == list(DEFAULT_BLOSSOM_SERVERS)
    assert s.custom_servers[-1] == "https://example.com"


def test_make_primary_moves_to_index_zero(tmp_path):
    s = _store(tmp_path)
    s.make_primary("https://nostr.download")
    assert s.primary == "https://nostr.download"


def test_remove_server_drops_entry(tmp_path):
    s = _store(tmp_path)
    s.remove_server("https://blossom.primal.net")
    assert "https://blossom.primal.net" not in s.configured_servers


def test_set_custom_servers_normalizes_and_dedupes(tmp_path):
    s = _store(tmp_path)
    persisted = s.set_custom_servers([
        "https://Blossom.Band/",
        "https://blossom.band",        # duplicate after normalization
        "  https://nostr.download  ",
        "ftp://nope.example",          # rejected scheme
        "",
    ])
    assert persisted == ["https://blossom.band", "https://nostr.download"]


def test_reset_to_defaults(tmp_path):
    s = _store(tmp_path)
    s.set_custom_servers(["https://x.example"])
    out = s.reset_to_defaults()
    assert s.custom_servers == []
    assert out == list(DEFAULT_BLOSSOM_SERVERS)


def test_persists_across_instances(tmp_path):
    path = tmp_path / "blossom_servers.json"
    a = settings.BlossomSettings(path=path)
    a.set_custom_servers(["https://blossom.band", "https://nostr.download"])
    b = settings.BlossomSettings(path=path)
    assert b.configured_servers == ["https://blossom.band", "https://nostr.download"]


def test_file_format_is_versioned(tmp_path):
    path = tmp_path / "blossom_servers.json"
    s = settings.BlossomSettings(path=path)
    s.set_custom_servers(["https://blossom.band"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["custom"] == ["https://blossom.band"]


def test_normalize_rejects_invalid_inputs():
    assert settings._normalize("") is None
    assert settings._normalize("not a url") is None
    assert settings._normalize("ftp://x.example") is None
    assert settings._normalize(None) is None  # type: ignore[arg-type]


def test_normalize_strips_path_and_lowercases_host():
    assert settings._normalize("HTTPS://Blossom.Band/foo/bar") == "https://blossom.band"


# --------------------------------------------------------------------------- #
# Scheme policy: https anywhere, http only on loopback
# --------------------------------------------------------------------------- #

def test_plain_http_server_is_rejected(tmp_path):
    # The docstring always promised http was for localhost only; the
    # scheme check did not enforce it, so a plain-http origin could be
    # configured and every upload would go out in the clear.
    s = _store(tmp_path)
    before = s.custom_servers
    assert s.add_server("http://evil.example") == before
    assert "http://evil.example" not in s.configured_servers
    assert settings._normalize("http://evil.example") is None


@pytest.mark.parametrize("url,expected", [
    ("http://localhost:3000", "http://localhost:3000"),
    ("http://127.0.0.1:3000", "http://127.0.0.1:3000"),
    ("http://[::1]:3000", "http://[::1]:3000"),
])
def test_loopback_dev_servers_still_work(tmp_path, url, expected):
    s = _store(tmp_path)
    assert settings._normalize(url) == expected
    s.add_server(url)
    assert expected in s.configured_servers


def test_persisted_plain_http_entry_is_dropped_on_load(tmp_path):
    path = tmp_path / "blossom_servers.json"
    path.write_text(json.dumps({
        "version": 1,
        "custom": ["https://blossom.band", "http://evil.example"],
    }), encoding="utf-8")
    s = settings.BlossomSettings(path=path)
    assert s.custom_servers == ["https://blossom.band"]


def test_save_never_writes_outside_the_configured_directory(tmp_path):
    # A temp file created beside the default settings file would both
    # touch the real config directory and break os.replace across
    # filesystems.
    path = tmp_path / "nested" / "blossom_servers.json"
    s = settings.BlossomSettings(path=path)
    s.set_custom_servers(["https://blossom.band"])
    assert path.is_file()
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "blossom_servers.json"]
