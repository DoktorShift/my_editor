# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the guard against Nostr secret keys in what is written
(nostrdesign.org, "private key safeguarding").

What must hold:

  A paste or drop with a secret key in it, into prose while a Nostr
  account is in use, is stopped with a warning; the person may paste the
  public key (the link to the key's owner) instead, or nothing. Without
  an account, and in plain text and code files, a paste is never stopped.

  Nothing is published with a secret key in it: the person may replace
  each one with its owner's public key in the document, and publish then;
  an encrypted key has no public key to offer and must be removed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from editor import HtmlEditor  # noqa: E402
from nostr import bech32, refs  # noqa: E402
from nostr.crypto import get_public_key  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

SECRET = "11" * 32
NSEC = bech32.encode_nsec(SECRET)
OWNER = "nostr:" + bech32.encode_npub(get_public_key(bytes.fromhex(SECRET)).hex())


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def text(words: str) -> QMimeData:
    mime = QMimeData()
    mime.setText(words)
    return mime


def test_a_plain_key_has_its_owner_and_an_encrypted_one_has_none():
    assert refs.public_reference_for(NSEC) == OWNER
    assert refs.without_secret_keys(f"my key {NSEC}.") == f"my key {OWNER}."
    assert refs.without_secret_keys(f"{NSEC} ncryptsec1{'q' * 40}") is None


@pytest.mark.parametrize("answer, shown", [(None, ""), ("other words", "other words"),
                                           (f"key {NSEC}", f"key {NSEC}")])
def test_the_guard_decides_what_a_paste_brings(answer, shown):
    ed = HtmlEditor()
    asked = []

    def guard(words):
        asked.append(words)
        return answer if answer != f"key {NSEC}" else words
    ed.set_paste_guard(guard)
    ed.insertFromMimeData(text(f"key {NSEC}"))
    assert asked == [f"key {NSEC}"]
    assert ed.toPlainText() == shown


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr import bech32

nsec = bech32.encode_nsec("11" * 32)
asked = []
answers = []
def fake_ask(parent, **kw):
    asked.append({"title": kw.get("title"),
                  "buttons": [b.label for b in kw.get("buttons", ())]})
    return answers.pop(0)
main_window.ask = fake_ask
main_window.inform = lambda parent, **kw: asked.append({"title": kw.get("title"),
                                                        "buttons": ["OK"]})


def paste(ed, words):
    mime = QMimeData()
    mime.setText(words)
    ed.insertFromMimeData(mime)


w = main_window.MainWindow()
w.show()
ed = w.new_tab()
app.processEvents()
r = {}
paste(ed, f"no account {nsec}")
r["without_account"] = [ed.toPlainText(), len(asked)]
ed.selectAll()
ed.textCursor().removeSelectedText()
w.nostr_state._active = True
answers.append(None)                                  # Don't Paste
paste(ed, f"mine {nsec}")
r["refused"] = [ed.toPlainText(), asked[-1]]
public = "nostr:" + bech32.encode_npub(
    __import__("nostr.crypto", fromlist=["x"]).get_public_key(bytes.fromhex("11" * 32)).hex())
answers.append(f"mine {public}")                     # Paste Public Key
paste(ed, f"mine {nsec}")
r["public"] = ed.toPlainText()
txt = os.path.join(os.environ["HOME"], "keys.txt")
with open(txt, "w", encoding="utf-8") as f:
    f.write("")
plain = w.open_path(txt)
before = len(asked)
paste(plain, nsec)
r["plain_file"] = [plain.toPlainText() == nsec, len(asked) == before]
# Publishing: never with a key in it.
ed.selectAll()
ed.textCursor().insertText(f"Hello {nsec} there")
answers.append(False)                                # Cancel
r["publish_cancelled"] = [w._no_secret_keys(ed, ed.toPlainText()), ed.toPlainText()]
answers.append(True)                                 # Replace with Public Key
r["publish_replaced"] = [w._no_secret_keys(ed, ed.toPlainText()), ed.toPlainText()]
ed.document().undo()
r["undo"] = ed.toPlainText()
ed.selectAll()
ed.textCursor().insertText("ncryptsec1" + "q" * 40)
r["encrypted"] = [w._no_secret_keys(ed, ed.toPlainText()), asked[-1]["buttons"]]
for e in (ed, plain):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps({**r, "public_ref": public}))
"""


def test_secret_keys_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    nsec = NSEC
    assert r["without_account"] == [f"no account {nsec}", 0]
    assert r["refused"][0] == ""
    assert r["refused"][1]["title"] == "This is a secret key"
    assert r["refused"][1]["buttons"] == ["Paste Public Key", "Don't Paste"]
    assert r["public"] == f"mine {r['public_ref']}"
    assert r["plain_file"] == [True, True]
    assert r["publish_cancelled"] == [False, f"Hello {nsec} there"]
    assert r["publish_replaced"] == [True, f"Hello {r['public_ref']} there"]
    assert r["undo"] == f"Hello {nsec} there"                     # one step
    assert r["encrypted"] == [False, ["OK"]]
