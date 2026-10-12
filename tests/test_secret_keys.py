# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the guard against Nostr secret keys in what is written
(nostrdesign.org, "private key safeguarding").

What must hold:

  A paste or drop with a secret key in it, into prose while a Nostr
  account is in use, is stopped with a warning; the person may paste the
  public key (the link to the key's owner) instead, or nothing. Without
  an account, and in plain text and code files, a paste is never stopped.

  Nothing is published with a secret key in it. The note or article is
  looked at last, as the event that would be signed: its text (link
  addresses too), title, summary and every tag, whatever was typed into
  the publish dialog. The person may publish it with each key replaced by
  its owner's public key, or not at all; an encrypted key has no public
  key to offer and must be removed. PublishJob refuses to sign such an
  event at all.
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
from nostr.events import compute_event_id  # noqa: E402
from nostr.publisher import PublishJob, build_article, build_note, without_secret_keys  # noqa: E402
from nostr.ui import secret_key_check  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402
from tests.outbox_fakes import PK, settle  # noqa: E402
from tests.test_publish_job import _dialog, make_job  # noqa: E402

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
    assert refs.without_secret_keys(f"[it](nostr:{NSEC})") == f"[it]({OWNER})"
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


# -- publishing: the event itself is looked at last ----------------------------

def test_a_key_is_found_in_the_text_a_link_address_and_every_tag():
    assert refs.secret_keys_in_event(build_note("Nothing to hide.", PK)) == []
    linked = build_note(f"My backup (nostr:{NSEC}) is here.", PK)
    assert refs.secret_keys_in_event(linked) == [NSEC]
    article = build_article("Body.", PK, "post", title=f"Title {NSEC}",
                            extra_tags=[["alt", f"see {NSEC}"]])
    assert refs.secret_keys_in_event(article) == [NSEC]


def test_replacing_keys_builds_the_event_again_with_an_id_that_fits():
    unsigned = build_article(f"[backup](nostr:{NSEC})", PK, "post", title=f"Mine {NSEC}")
    public = without_secret_keys(unsigned)
    assert public["content"] == f"[backup]({OWNER})"
    assert ["title", f"Mine {OWNER}"] in public["tags"]
    assert refs.secret_keys_in_event(public) == []
    assert public["id"] == compute_event_id(public["pubkey"], public["created_at"],
                                            public["kind"], public["tags"], public["content"])
    assert public["created_at"] == unsigned["created_at"] and public["kind"] == 30023
    assert without_secret_keys(build_note("ncryptsec1" + "q" * 40, PK)) is None


def test_an_event_with_a_key_in_it_is_never_signed():
    with pytest.raises(ValueError):
        make_job(build_note(f"oops {NSEC}", PK))


@pytest.fixture
def answers(monkeypatch):
    """The alerts of the last check, answered from a list; what they
    asked, in order."""
    queue, asked = [], []

    def ask(parent, **kw):
        asked.append([b.label for b in kw["buttons"]])
        return queue.pop(0)

    monkeypatch.setattr(secret_key_check, "ask", ask)
    monkeypatch.setattr(secret_key_check, "inform",
                        lambda parent, **kw: asked.append(["OK"]))
    return queue, asked


def test_a_key_typed_into_the_note_dialog_is_not_published(tmp_path, answers):
    queue, asked = answers
    dialog = _dialog("note", tmp_path, None)
    dialog._text_edit.setPlainText(f"A harmless note {NSEC}")
    queue.append(False)                                        # Cancel
    dialog._on_publish()
    assert asked == [["Cancel", "Replace with Public Key"]]
    assert dialog._job is None and dialog._publish_btn.isEnabled()
    queue.append(True)                                         # Replace with Public Key
    dialog._on_publish()
    settle()
    published = dialog._relay_pool.published
    assert published and published[-1][1]["content"] == f"A harmless note {OWNER}"


def test_a_key_in_any_field_of_the_article_dialog_is_not_published(tmp_path, answers):
    queue, asked = answers
    dialog = _dialog("article", tmp_path, None)
    dialog._title_edit.setText(f"About {NSEC}")
    dialog._summary_edit.setText("ncryptsec1" + "q" * 40)
    dialog._publish("post", "Body.", 1_800_000_000)
    assert asked == [["OK"]] and dialog._job is None              # encrypted: remove it
    dialog._summary_edit.setText("A summary.")
    queue.append(True)
    dialog._publish("post", f"[backup](nostr:{NSEC})", 1_800_000_000)
    settle()
    event = dialog._relay_pool.published[-1][1]
    assert refs.secret_keys_in_event(event) == []
    assert event["content"] == f"[backup]({OWNER})"
    assert ["title", f"About {OWNER}"] in event["tags"]
