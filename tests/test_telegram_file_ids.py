"""Unit tests for the per-bot file_id cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from publishers.telegram.file_ids import FileIdCache, sha256_file


@pytest.fixture
def cache(tmp_path: Path) -> FileIdCache:
    return FileIdCache(path=tmp_path / "file_ids.json")


def test_empty_cache_miss(cache):
    assert cache.get("sha", "bot_a", "photo") is None


def test_put_get_round_trip(cache):
    cache.put("sha1", "bot_a", "photo", "FILE_ID_X")
    assert cache.get("sha1", "bot_a", "photo") == "FILE_ID_X"


def test_per_bot_scoping(cache):
    cache.put("sha1", "bot_a", "photo", "fid_a")
    cache.put("sha1", "bot_b", "photo", "fid_b")
    assert cache.get("sha1", "bot_a", "photo") == "fid_a"
    assert cache.get("sha1", "bot_b", "photo") == "fid_b"


def test_per_kind_scoping(cache):
    cache.put("sha1", "bot_a", "photo", "fid_p")
    cache.put("sha1", "bot_a", "document", "fid_d")
    assert cache.get("sha1", "bot_a", "photo") == "fid_p"
    assert cache.get("sha1", "bot_a", "document") == "fid_d"


def test_evict_removes_entry(cache):
    cache.put("sha1", "bot_a", "photo", "fid")
    cache.evict("sha1", "bot_a", "photo")
    assert cache.get("sha1", "bot_a", "photo") is None


def test_evict_missing_is_noop(cache):
    cache.evict("missing", "bot", "photo")  # no exception


def test_evict_bot_removes_all_entries_for_that_bot(cache):
    cache.put("sha1", "bot_a", "photo", "x")
    cache.put("sha2", "bot_a", "document", "y")
    cache.put("sha1", "bot_b", "photo", "z")
    cache.evict_bot("bot_a")
    assert cache.get("sha1", "bot_a", "photo") is None
    assert cache.get("sha2", "bot_a", "document") is None
    assert cache.get("sha1", "bot_b", "photo") == "z"


def test_disk_round_trip(cache, tmp_path):
    cache.put("abc", "bot", "photo", "FILE_ID")
    fresh = FileIdCache(path=cache._path)
    assert fresh.get("abc", "bot", "photo") == "FILE_ID"


def test_sha256_file_streams_chunks(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"hello world")
    sha = sha256_file(path)
    # Known SHA-256 of "hello world".
    assert sha == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
