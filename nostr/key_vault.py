# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private keys kept on this computer, for accounts that sign in with one.

Most people sign with a signer app, and then no private key ever reaches
MyEditor. Someone who creates an account here, or signs in with their
private key, chooses to keep the key on this computer instead. It lives
in one file:

    ~/.config/my_editor/nostr_keys.json     chmod 600, folder chmod 700

readable only by this account, written atomically, keyed by public key.

Why a file and not the system keychain: MyEditor's builds are not signed
with a paid certificate, so on a Mac every update changes the app's code
signature, and the keychain would ask for the login password after each
update. A permissions-locked file is the same protection the signer
pairing secrets already have, and the person chose local storage.

Every key read back is checked against the public key it is filed under,
so a damaged or edited file can never sign as somebody else.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from nostr import crypto

KEYS_DIR = Path.home() / ".config" / "my_editor"
KEYS_FILE = KEYS_DIR / "nostr_keys.json"


class KeyVault:
    """Secret keys by public key, in a file only this account can read."""

    def __init__(self, path: Path = KEYS_FILE) -> None:
        self._path = Path(path)

    def store(self, secret_key: bytes) -> str:
        """Keep ``secret_key``; return its public key (hex)."""
        if len(secret_key) != 32:
            raise ValueError("a secret key is 32 bytes")
        pubkey = crypto.get_public_key(secret_key).hex()
        keys = self._read()
        keys[pubkey] = secret_key.hex()
        self._write(keys)
        return pubkey

    def load(self, pubkey_hex: str) -> Optional[bytes]:
        """The secret key filed under ``pubkey_hex``, or None.

        None as well when the stored key does not belong to that public
        key: a key that would sign as someone else is not a key.
        """
        value = self._read().get((pubkey_hex or "").lower())
        if not isinstance(value, str):
            return None
        try:
            secret = bytes.fromhex(value)
        except ValueError:
            return None
        if len(secret) != 32:
            return None
        try:
            if crypto.get_public_key(secret).hex() != pubkey_hex.lower():
                return None
        except Exception:  # noqa: BLE001, an invalid scalar is not a key
            return None
        return secret

    def has(self, pubkey_hex: str) -> bool:
        return self.load(pubkey_hex) is not None

    def forget(self, pubkey_hex: str) -> None:
        keys = self._read()
        if keys.pop((pubkey_hex or "").lower(), None) is not None:
            self._write(keys)

    # -- file ----------------------------------------------------------------------

    def _read(self) -> Dict[str, str]:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        keys = data.get("keys") if isinstance(data, dict) else None
        return {k: v for k, v in keys.items() if isinstance(k, str)} \
            if isinstance(keys, dict) else {}

    def _write(self, keys: Dict[str, str]) -> None:
        folder = self._path.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".nostr_keys_", suffix=".tmp", dir=str(folder))
        try:
            os.chmod(tmp, 0o600)   # before a single key byte is written
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "keys": keys}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
