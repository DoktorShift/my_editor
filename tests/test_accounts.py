# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins creating a Nostr account and restoring one from its backup.

What must hold:

  The backup file opens again here and in other apps: it carries the
  public key and the protected key, and the key reads back with its
  password. An unprotected file is a separate, explicit choice.

  A key is found wherever it sits in a backup or a paste; a public key,
  a damaged key or a file with no key is explained in plain words.

  Create Account makes the key only after the first step, shows only the
  public key, asks for a backup before going on (or an explicit skip),
  and keeps the key on this computer or hands it to Amber, as chosen.
  Amber pairing as a different account is refused and undone.

  Restore Account reads a backup file or a pasted key, asks for the
  password of a protected one, refuses a wrong password in words, and
  signs the account in on this computer.

The private key never appears as text in any window.
"""

import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton  # noqa: E402

from nostr import bech32, crypto, nip49  # noqa: E402
from nostr import account_backup as ab  # noqa: E402
from nostr.key_vault import KeyVault  # noqa: E402
from nostr.profiles import Profile, ProfileStore  # noqa: E402
from nostr.ui import account_windows as aw  # noqa: E402
from nostr.ui.account_windows import (  # noqa: E402
    AMBER_CONNECT, AMBER_GET, AMBER_IMPORT, BACKUP, CHOOSE, DONE, NAME, OPEN, PASSWORD,
    SETUP, STEP_PROFILE, STEP_RELAYS, CreateAccountWindow, RestoreAccountWindow,
)

SK = bytes.fromhex("1f" * 32)
PK = crypto.get_public_key(SK).hex()
NSEC = bech32.encode_nsec(SK.hex())


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def alerts(monkeypatch):
    answers = []
    monkeypatch.setattr(aw, "ask", lambda parent, **kw: answers.pop(0) if answers else False)
    return answers


def settle():
    QThreadPool.globalInstance().waitForDone(10_000)
    for _ in range(5):
        QApplication.processEvents()


def all_text(window) -> str:
    parts = [w.text() for w in window.findChildren(QLabel)]
    parts += [w.text() for w in window.findChildren(QPushButton)]
    parts += [w.toolTip() for w in window.findChildren(QLabel)]
    return "\n".join(parts)


# -- the backup file -----------------------------------------------------------------

def test_a_protected_backup_names_the_account_and_reopens_with_its_password():
    text = ab.backup_text(SK, password="correct horse", today=datetime.date(2026, 10, 5))
    assert bech32.encode_npub(PK) in text
    assert NSEC not in text and "ncryptsec1" in text
    assert "Saved 2026-10-05" in text and "\u2014" not in text
    found = ab.find_key(text)
    assert found.protected and found.open_with("correct horse") == SK


def test_an_unprotected_backup_says_so():
    text = ab.backup_text(SK, password=None)
    assert NSEC in text and "not protected" in text
    found = ab.find_key(text)
    assert not found.protected and found.secret == SK


def test_a_key_is_found_wherever_it_sits():
    assert ab.find_key(f"my key, saved by hand:\n\n   {NSEC.upper()}  \n").secret == SK
    assert ab.find_key(SK.hex(), allow_hex=True).secret == SK


def test_hex_is_only_accepted_from_a_paste():
    with pytest.raises(ab.BackupError):
        ab.find_key(SK.hex())


def test_what_is_not_a_private_key_is_explained():
    with pytest.raises(ab.BackupError, match="public key"):
        ab.find_key(bech32.encode_npub(PK))
    with pytest.raises(ab.BackupError, match="No private key"):
        ab.find_key("a shopping list")
    with pytest.raises(ab.BackupError, match="incomplete or mistyped"):
        ab.find_key(NSEC[:-1] + ("q" if NSEC[-1] != "q" else "p"))


def test_backup_passwords_need_length_and_agreement():
    assert ab.password_problem("short", "short") == "Use at least 8 characters."
    assert "match" in ab.password_problem("long enough", "long enougj")
    assert ab.password_problem("long enough", "long enough") is None


def test_the_suggested_file_name_says_whose_account_it_is():
    name = ab.suggested_file_name(PK)
    assert name.startswith("nostr-account-") and name.endswith(".txt")
    assert name[14:22] == bech32.encode_npub(PK)[5:13]


# -- Create Account ------------------------------------------------------------------

def make_create(tmp_path, *, paired=None):
    store = ProfileStore(tmp_path / "profiles.json")
    vault = KeyVault(tmp_path / "keys.json")
    saved = []

    def save_backup(parent, secret, *, password):
        saved.append((secret, password))
        return str(tmp_path / "backup.txt")

    def connect_signer(on_profile):
        on_profile(paired)

    win = CreateAccountWindow(store=store, vault=vault, connect_signer=connect_signer,
                              generate=lambda: SK, save_backup=save_backup, is_dark=False)
    ready = []
    win.account_ready.connect(lambda p, n, r: ready.append((p, n, r)))
    return win, store, vault, saved, ready


def test_the_key_is_made_only_after_the_first_step(tmp_path):
    win, *_ = make_create(tmp_path)
    assert win.page == NAME and win.pubkey == ""
    win._name_edit.setText("Satoshi")
    win.buttons["continue"].click()
    assert win.page == BACKUP and win.pubkey == PK


def test_only_the_public_key_is_ever_shown(tmp_path):
    win, *_ = make_create(tmp_path)
    win.buttons["continue"].click()
    text = all_text(win)
    assert NSEC not in text and SK.hex() not in text
    assert bech32.encode_npub(PK)[:12] in text


def test_continue_waits_for_a_backup_or_an_explicit_skip(tmp_path, alerts):
    win, *_ = make_create(tmp_path)
    win.buttons["continue"].click()
    assert not win.buttons["continue"].isEnabled()
    alerts.append(False)                      # "Save a Backup"
    win.buttons["skip"].click()
    assert win.page == BACKUP
    alerts.append(True)                       # "Continue Without Backup"
    win.buttons["skip"].click()
    assert win.page == CHOOSE


def test_a_protected_backup_file_needs_a_good_password(tmp_path):
    win, _store, _vault, saved, _ready = make_create(tmp_path)
    win.buttons["continue"].click()
    win.backup_form.password.setText("short")
    win.backup_form.confirm.setText("short")
    assert not win.backup_form.save_button.isEnabled()
    win.backup_form.password.setText("correct horse")
    win.backup_form.confirm.setText("correct horse")
    win.backup_form.save_button.click()
    assert saved == [(SK, "correct horse")]
    assert win.buttons["continue"].isEnabled()
    assert win.buttons["skip"].isHidden()


def test_an_unprotected_backup_needs_a_second_yes(tmp_path, alerts):
    win, _store, _vault, saved, _ready = make_create(tmp_path)
    win.buttons["continue"].click()
    alerts.append(False)
    win.backup_form.save_plain()
    assert saved == []
    alerts.append(True)
    win.backup_form.save_plain()
    assert saved == [(SK, None)]


def test_copying_the_key_counts_as_a_backup_and_leaves_the_clipboard_later(tmp_path, qt_app):
    win, *_ = make_create(tmp_path)
    win.buttons["continue"].click()
    win.backup_form.copy_private_key()
    assert qt_app.clipboard().text() == NSEC
    assert win.buttons["continue"].isEnabled()
    qt_app.clipboard().setText("")


def test_keeping_the_key_here_stores_it_and_signs_in_locally(tmp_path):
    win, store, vault, _saved, ready = make_create(tmp_path)
    win._name_edit.setText("Satoshi")
    win.buttons["continue"].click()
    win.backup_form.copy_private_key()
    win.buttons["continue"].click()
    assert win.page == CHOOSE
    win.buttons["continue"].click()           # "Keep the key on this computer"
    assert vault.load(PK) == SK
    profile = store.get(PK)
    assert profile.is_local and profile.display_name == "Satoshi"
    assert win.page == SETUP
    (got, name, report), = ready
    assert got.user_pubkey == PK and name == "Satoshi"
    report.step.emit(STEP_PROFILE, "done", "")
    report.step.emit(STEP_RELAYS, "done", "")
    report.finished.emit(True, "")
    assert win.page == DONE and "Satoshi is ready" in win._done_text.text()


def test_a_failed_setup_can_be_finished_later(tmp_path):
    win, _store, _vault, _saved, ready = make_create(tmp_path)
    win.buttons["continue"].click()
    win.backup_form.copy_private_key()
    win.buttons["continue"].click()
    win.buttons["continue"].click()
    ready[0][2].finished.emit(False, "")
    assert set(win.buttons) == {"later", "retry"}
    assert "saved" in win._setup_note.text()


def test_the_amber_path_never_keeps_the_key_here(tmp_path):
    paired = Profile(user_pubkey=PK, bunker_pubkey="b" * 64,
                     bunker_relays=["wss://relay.nsec.app"], local_secret_hex="c" * 64)
    win, store, vault, _saved, ready = make_create(tmp_path, paired=paired)
    win.buttons["continue"].click()
    win.backup_form.copy_private_key()
    win.buttons["continue"].click()
    win._choice_amber.setChecked(True)
    win.buttons["continue"].click()
    assert win.page == AMBER_GET
    win.buttons["continue"].click()
    assert win.page == AMBER_IMPORT
    assert win._qr.isHidden()                 # only on request
    win._qr_button.click()
    assert not win._qr.isHidden()
    win.buttons["continue"].click()
    assert win.page == AMBER_CONNECT and win._qr.pixmap().isNull()
    win.buttons["connect"].click()
    assert vault.load(PK) is None
    assert not store.get(PK).is_local
    assert win.page == SETUP and ready


def test_amber_signing_as_someone_else_is_refused_and_undone(tmp_path):
    other = Profile(user_pubkey="ee" * 32, bunker_pubkey="b" * 64,
                    bunker_relays=[], local_secret_hex="c" * 64)
    win, store, _vault, _saved, ready = make_create(tmp_path, paired=other)
    store.upsert(other)                       # the pairing dialog saved it
    win.buttons["continue"].click()
    win.backup_form.copy_private_key()
    win.buttons["continue"].click()
    win._choice_amber.setChecked(True)
    win.buttons["continue"].click()
    win.buttons["continue"].click()
    win.buttons["continue"].click()
    win.buttons["connect"].click()
    assert win.page == AMBER_CONNECT
    assert "different account" in win._connect_error.text()
    assert store.get("ee" * 32) is None and ready == []


# -- Restore Account -----------------------------------------------------------------

def make_restore(tmp_path, file_text=None):
    store = ProfileStore(tmp_path / "profiles.json")
    vault = KeyVault(tmp_path / "keys.json")
    win = RestoreAccountWindow(store=store, vault=vault, is_dark=True,
                               open_file=lambda: file_text)
    ready = []
    win.account_ready.connect(lambda p, n, r: ready.append((p, n, r)))
    return win, store, vault, ready


def test_a_protected_backup_file_restores_with_its_password(tmp_path):
    text = ab.backup_text(SK, password="correct horse")
    win, store, vault, ready = make_restore(tmp_path, text)
    assert win.page == OPEN
    win.buttons["choose"].click()
    assert win.page == PASSWORD
    win._backup_password.setText("wrong guess")
    win.buttons["restore"].click()
    settle()
    assert win.page == PASSWORD and "wrong" in win._password_error.text()
    win._backup_password.setText("correct horse")
    win.buttons["restore"].click()
    settle()
    assert win.page == SETUP
    assert vault.load(PK) == SK and store.get(PK).is_local
    assert ready and ready[0][0].user_pubkey == PK


def test_a_pasted_key_restores_after_the_warning_is_shown(tmp_path):
    win, store, vault, ready = make_restore(tmp_path)
    win._paste_link.click()
    assert "apps you trust" in all_text(win)
    assert win._paste_edit.echoMode() == QLineEdit.Password
    win._paste_edit.setText(NSEC)
    win.buttons["continue"].click()
    assert vault.load(PK) == SK and win._paste_edit.text() == ""


def test_a_file_without_a_key_is_explained(tmp_path):
    win, *_ = make_restore(tmp_path, "nothing to see here")
    win.buttons["choose"].click()
    assert win.page == OPEN and "No private key" in win._open_error.text()


def test_restoring_an_account_already_here_keeps_its_name(tmp_path):
    win, store, _vault, _ready = make_restore(tmp_path)
    store.upsert(Profile(user_pubkey=PK, bunker_pubkey="b" * 64, bunker_relays=[],
                         local_secret_hex="c" * 64, display_name="Satoshi"))
    win._paste_link.click()
    win._paste_edit.setText(NSEC)
    win.buttons["continue"].click()
    assert store.get(PK).display_name == "Satoshi" and store.get(PK).is_local


def test_pasting_can_be_undone_back_to_the_backup_file(tmp_path):
    win, *_ = make_restore(tmp_path)
    win._paste_link.click()
    win._paste_edit.setText(NSEC)
    win._hide_paste()
    assert win._paste_box.isHidden() and win._paste_edit.text() == ""
    assert "choose" in win.buttons


def test_an_account_kept_here_can_be_backed_up_any_time(tmp_path):
    saved = []
    win = aw.BackupAccountWindow(secret=SK, is_dark=False,
                                 save_backup=lambda parent, secret, *, password:
                                 saved.append((secret, password)) or "/tmp/b.txt")
    assert bech32.encode_npub(PK)[:12] in all_text(win) and NSEC not in all_text(win)
    win.backup_form.password.setText("correct horse")
    win.backup_form.confirm.setText("correct horse")
    win.backup_form.save_button.click()
    assert saved == [(SK, "correct horse")]
    assert "Backup saved" in win.backup_form.note.text()
