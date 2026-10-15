# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the speech models for dictation on this computer (dictation/models.py).

What must hold: each model's files come from its makers' own place over
HTTPS, each pinned to its size and SHA-256; a model counts as installed
only when every file is there whole; installing fetches what is missing
and reports progress over the whole model; removing a model frees its
files, and the voice detector goes with the last one.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictation import models  # noqa: E402
from verified_download import host_allowed  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def fake_fetch(fetched):
    """A download that writes the pinned size, as the real one would."""
    def fetch(url, dest, *, hosts, sha256, progress, cancel, user_agent):
        file = next(f for f in models.all_files() if f.url == url)
        with open(dest, "wb") as out:
            out.write(b"\0" * file.size)
        progress(file.size, file.size)
        fetched.append(os.path.basename(dest))
        return dest
    return fetch


def test_every_file_is_official_and_pinned():
    assert set(models.MODELS) == {models.STANDARD, models.ACCURATE}
    for file in models.all_files():
        assert host_allowed(file.url, models.HOSTS), file.url
        assert len(file.sha256) == 64 and file.size > 0
    standard, accurate = models.MODELS[models.STANDARD], models.MODELS[models.ACCURATE]
    assert 150e6 < standard.size < 170e6 and 360e6 < accurate.size < 390e6


def test_installing_fetches_what_is_missing_and_says_how_far_it_is():
    fetched, seen = [], []
    model = models.MODELS[models.STANDARD]
    assert not models.installed(model)
    total = models.download_size(model)
    assert total == model.size + models.VOICE_DETECTOR.size
    models.install(model, progress=lambda done, total: seen.append((done, total)),
                   fetch=fake_fetch(fetched))
    assert models.installed(model)
    assert sorted(fetched) == sorted(os.path.basename(models.path_of(f))
                                     for f in models.files_of(model))
    assert seen[-1] == (total, total) and models.download_size(model) == 0
    fetched.clear()
    models.install(model, fetch=fake_fetch(fetched))           # nothing left to fetch
    assert fetched == []


def test_a_file_cut_short_is_not_installed():
    model = models.MODELS[models.STANDARD]
    models.install(model, fetch=fake_fetch([]))
    encoder = models.path_of(model.encoder)
    with open(encoder, "r+b") as f:
        f.truncate(10)
    assert not models.installed(model)


def test_removing_frees_the_model_and_the_detector_goes_with_the_last():
    standard, accurate = models.MODELS[models.STANDARD], models.MODELS[models.ACCURATE]
    models.install(standard, fetch=fake_fetch([]))
    models.install(accurate, fetch=fake_fetch([]))
    models.remove(standard)
    assert not models.installed(standard) and models.installed(accurate)
    models.remove(accurate)
    assert not os.path.exists(models.path_of(models.VOICE_DETECTOR))
    assert models.download_size(standard) == standard.size + models.VOICE_DETECTOR.size
