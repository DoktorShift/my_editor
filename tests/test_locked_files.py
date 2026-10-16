# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins documents locked with a password (locked_files.py).

What must hold:

  A locked document holds its bytes encrypted to the password in age's
  open format (age-encryption.org/v1, scrypt), so `age -d` opens it
  without MyEditor; the right password gives the bytes back, a wrong one
  gives nothing and says so. Saving again needs no password typed again.
  Its crash backups are sealed too, quickly enough for a backup every few
  seconds, and opened with the same password after a crash.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import locked_files  # noqa: E402
from locked_files import Lock, WrongPassword  # noqa: E402

TEXT = "# Diary\n\nNothing anyone else should read.\n".encode("utf-8")


# Each lock and each seal takes scrypt's time (a moment, on purpose): the
# tests share them.
@pytest.fixture(scope="module")
def lock():
    return Lock("correct horse")


@pytest.fixture(scope="module")
def sealed(lock):
    return lock.seal(TEXT)


def test_a_locked_document_is_an_age_file_the_password_opens(tmp_path, sealed):
    assert sealed.startswith(b"age-encryption.org/v1\n-> scrypt ")
    assert b"Nothing anyone" not in sealed
    assert locked_files.is_locked(sealed) and not locked_files.is_locked(TEXT)
    path = tmp_path / "diary.md"
    path.write_bytes(sealed)
    assert locked_files.is_locked_file(path)
    opened, data = Lock.open(path.read_bytes(), "correct horse")
    assert data == TEXT and opened.matches("correct horse")


def test_a_document_read_again_opens_with_the_password_it_has(lock, sealed):
    assert lock.unseal(sealed) == TEXT
    with pytest.raises(WrongPassword):
        lock.unseal(Lock("battery staple").seal(TEXT))      # changed elsewhere


def test_a_wrong_password_opens_nothing(sealed):
    with pytest.raises(WrongPassword):
        Lock.open(sealed, "Correct horse")


def test_each_save_is_sealed_anew(lock, sealed):
    assert lock.seal(TEXT) != sealed             # a new salt and nonce each time


def test_a_file_that_is_not_there_or_not_locked_is_not_locked(tmp_path):
    assert not locked_files.is_locked_file(tmp_path / "missing.md")
    plain = tmp_path / "plain.md"
    plain.write_bytes(TEXT)
    assert not locked_files.is_locked_file(plain)


def test_a_backup_is_sealed_quickly_and_opened_with_the_password(lock):
    import time
    record = b'{"content": "unsaved words"}'
    started = time.perf_counter()
    for _ in range(5):
        sealed = lock.seal_backup(record)
    assert time.perf_counter() - started < 0.5         # no password work per backup
    assert b"unsaved words" not in sealed["sealed"].encode()
    restored, data = Lock.open_backup(sealed, "correct horse")
    assert data == record and restored.matches("correct horse")
    with pytest.raises(WrongPassword):
        Lock.open_backup(sealed, "wrong")
