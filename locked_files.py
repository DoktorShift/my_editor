# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Documents locked with a password (File > Set Password…).

A locked document keeps its name and type, as a locked Pages document does:
a .md stays a .md. In place of its text it holds that text encrypted to its
password in age's format (age-encryption.org/v1): scrypt makes each guess
at the password slow, ChaCha20-Poly1305 encrypts and seals the text. The
format is open, so ``age -d`` opens the file without MyEditor. The work is
done by pyrage, age's Rust implementation.

The password stays in memory while the document is open, so that saving it
again asks for nothing; it is never written anywhere. Nothing of a locked
document is written in the clear either: its crash backups (recovery.py),
written every few seconds, are sealed too. Running scrypt for each of them
would stall the typing, so each open document has a key of its own that
seals them at once (X25519), and only that key is locked with the password,
once (``seal_backup``).

If the password is forgotten, the document cannot be opened: by MyEditor,
by age, or by anyone.
"""

from __future__ import annotations

import base64
import hmac
import os
from typing import Dict, Tuple

from pyrage import DecryptError, decrypt, encrypt, passphrase, x25519

# How every age file begins.
MAGIC = b"age-encryption.org/v1\n"


class WrongPassword(ValueError):
    """The password does not open this document."""


def is_locked(data: bytes) -> bool:
    """Whether ``data`` is a locked document's (an age file)."""
    return data.startswith(MAGIC)


def is_locked_file(path: os.PathLike) -> bool:
    """Whether the file at ``path`` is a locked document; False when it
    cannot be read."""
    try:
        with open(path, "rb") as f:
            return is_locked(f.read(len(MAGIC)))
    except OSError:
        return False


class Lock:
    """An open document's password: what it is saved with (``seal``), and
    what its crash backups are sealed with (``seal_backup``)."""

    def __init__(self, password: str, *, backup_key: Tuple[x25519.Identity, str] = None) -> None:
        if not password:
            raise ValueError("a lock needs a password")
        self._password = password
        # The key the backups are sealed with, and that key locked with the
        # password: made once, here, since locking it takes scrypt's time.
        if backup_key is None:
            identity = x25519.Identity.generate()
            locked = _text(passphrase.encrypt(str(identity).encode("ascii"), password))
            backup_key = (identity, locked)
        self._backup_identity, self._locked_backup_key = backup_key

    # -- the document --------------------------------------------------------------

    @classmethod
    def open(cls, data: bytes, password: str) -> Tuple["Lock", bytes]:
        """``data``'s own bytes, and the lock to save it with again. Raises
        WrongPassword."""
        try:
            opened = passphrase.decrypt(data, password)
        except DecryptError as wrong:
            raise WrongPassword() from wrong
        return cls(password), opened

    def unseal(self, data: bytes) -> bytes:
        """``data`` (the document, changed on the disk and read again)
        opened with this password. Raises WrongPassword when it was locked
        with another one since."""
        try:
            return passphrase.decrypt(data, self._password)
        except DecryptError as wrong:
            raise WrongPassword() from wrong

    def seal(self, data: bytes) -> bytes:
        """``data`` locked with the password, as the document is saved."""
        return passphrase.encrypt(data, self._password)

    def matches(self, password: str) -> bool:
        """Whether ``password`` is this document's (asked before it is
        changed or taken off)."""
        return hmac.compare_digest(password.encode("utf-8"), self._password.encode("utf-8"))

    # -- its crash backups ---------------------------------------------------------

    def seal_backup(self, data: bytes) -> Dict[str, str]:
        """A crash backup's bytes, sealed: no password work, so a backup
        every few seconds costs nothing."""
        sealed = encrypt(data, [self._backup_identity.to_public()])
        return {"key": self._locked_backup_key, "sealed": _text(sealed)}

    @classmethod
    def open_backup(cls, backup: Dict[str, str], password: str) -> Tuple["Lock", bytes]:
        """A sealed backup's bytes (``seal_backup``), and the lock its
        document goes on with. Raises WrongPassword."""
        try:
            key = passphrase.decrypt(_bytes(backup["key"]), password).decode("ascii")
        except DecryptError as wrong:
            raise WrongPassword() from wrong
        identity = x25519.Identity.from_str(key)
        data = decrypt(_bytes(backup["sealed"]), [identity])
        return cls(password, backup_key=(identity, backup["key"])), data


def _text(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _bytes(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))
