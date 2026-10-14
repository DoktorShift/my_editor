# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starting the app just far enough to prove a build is whole.

``my-editor --self-test`` loads the app (which imports nearly every module)
and checks the pieces a frozen build can lose: translations and icons left out
of the bundle, a Qt module or the TLS backend missing, a library whose
native part did not come along. It prints one line per check and exits with
0 when every check passed, 1 otherwise. The release workflow runs it on each
build before packaging, so a broken bundle never becomes an installer.

A Windows build has no console, so the report also goes to the file named
by ``MYEDITOR_SELF_TEST_REPORT`` when that is set.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Tuple

import constants

Check = Tuple[str, Callable[[], str]]

# Systems that always have a spell checker of their own: there, a missing
# one means the build lost the code that reaches it.
_SPELLING_EXPECTED = ("darwin", "win32")


def _translations() -> str:
    import i18n
    found = i18n.available()
    if "de" not in found:
        raise RuntimeError(f"the German catalogue is missing (found {sorted(found)})")
    return ", ".join(sorted(found))


def _icon() -> str:
    from main import resource_path
    path = resource_path("packaging/icons/icon-256.png")
    if not os.path.isfile(path):
        raise RuntimeError(f"{path} is missing")
    return "packaging/icons/icon-256.png"


def _secure_connections() -> str:
    # Relays, media servers and updates are all reached over TLS.
    from PySide6.QtNetwork import QSslSocket
    if not QSslSocket.supportsSsl():
        raise RuntimeError("Qt has no TLS backend")
    return QSslSocket.activeBackend() or "available"


def _keys_and_encryption() -> str:
    # The native secp256k1 code and NIP-44 encryption, with a fresh key.
    import hashlib

    from nostr import crypto
    secret = crypto.generate_secret_key()
    public = crypto.get_public_key(secret)
    digest = hashlib.sha256(b"self-test").digest()
    if not crypto.verify_schnorr(public, crypto.sign_schnorr(secret, digest), digest):
        raise RuntimeError("a signature did not verify")
    key = crypto.conversation_key(secret, public)
    if crypto.decrypt(crypto.encrypt("self-test", key), key) != "self-test":
        raise RuntimeError("NIP-44 did not round-trip")
    return "sign, verify, encrypt, decrypt"


def _modules() -> str:
    # Imported lazily by the app, so a missing one shows only when used.
    import feedparser  # noqa: F401  imports
    import markdownify  # noqa: F401
    import readability  # noqa: F401
    import segno  # noqa: F401  QR codes for pairing and payment
    from PySide6 import QtPdf, QtPdfWidgets, QtWebSockets  # noqa: F401
    import export_html  # noqa: F401
    import export_pdf  # noqa: F401
    import pdf_viewer  # noqa: F401
    return "PDF, web sockets, exports, imports, QR codes"


def _spelling() -> str:
    from spelling import SpellChecker
    checker = SpellChecker()
    if checker.is_available():
        return "available"
    problem = checker.backend.problem or "unavailable"
    if sys.platform in _SPELLING_EXPECTED:
        raise RuntimeError(problem)
    return f"not offered by this system ({problem})"


def _editor() -> str:
    # The text stack end to end: an editor, Markdown in and out. Building
    # the whole main window would restore the person's session and start
    # the single-instance server, which a self-test must not do.
    from editor import HtmlEditor
    from markdown_writer import READ_FEATURES, document_to_markdown
    editor = HtmlEditor()
    try:
        editor.document().setMarkdown("# Title\n\nSome **bold** text.\n", READ_FEATURES)
        written = document_to_markdown(editor.document(), lambda _fmt: None)
    finally:
        editor.deleteLater()
    if "# Title" not in written or "**bold**" not in written:
        raise RuntimeError(f"Markdown did not round-trip: {written!r}")
    return "Markdown in and out"


CHECKS: List[Check] = [
    ("translations", _translations),
    ("icon", _icon),
    ("secure connections", _secure_connections),
    ("keys and encryption", _keys_and_encryption),
    ("modules", _modules),
    ("spelling", _spelling),
    ("editor", _editor),
]


def run(checks: List[Check] = CHECKS) -> int:
    """Run every check and report; 0 when all passed."""
    lines = [f"MyEditor {constants.APP_VERSION} self-test ({sys.platform})"]
    failed = 0
    for name, check in checks:
        try:
            lines.append(f"ok      {name}: {check()}")
        except Exception as exc:     # a self-test reports every failure, of any kind
            failed += 1
            lines.append(f"FAILED  {name}: {type(exc).__name__}: {exc}")
    lines.append("all checks passed" if not failed else f"{failed} check(s) failed")
    _report("\n".join(lines) + "\n")
    return 0 if not failed else 1


def _report(text: str) -> None:
    if sys.stdout is not None:
        sys.stdout.write(text)
        sys.stdout.flush()
    target = os.environ.get("MYEDITOR_SELF_TEST_REPORT")
    if target:
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
