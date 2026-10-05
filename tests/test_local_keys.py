# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins keeping a private key on this computer and signing with it.

What must hold:

  A password-protected key (NIP-49 ncryptsec) opens in other Nostr apps
  and theirs open here: the published test vectors decode, a wrong
  password is told apart from a damaged key, and weak settings are
  refused.

  The key file is readable by this account only, and a key filed under
  the wrong public key is never handed out.

  The local signer answers exactly like the remote one, later rather than
  inside the call, and its signatures verify.

  A profile that signs locally gets a local signer from the session pool;
  without its key it gets a plain explanation, not a crash.
"""

import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import crypto, events, nip49  # noqa: E402
from nostr.bunker import BunkerSessionPool  # noqa: E402
from nostr.key_vault import KeyVault  # noqa: E402
from nostr.local_signer import LocalSigner  # noqa: E402
from nostr.profiles import Profile  # noqa: E402

SPEC_NCRYPTSEC = (
    "ncryptsec1qgg9947rlpvqu76pj5ecreduf9jxhselq2nae2kghhvd5g7dgjtcxfqtd67p9m0w57lspw8gsq6"
    "yphnm8623nsl8xn9j4jdzz84zm3frztj3z7s35vpzmqf6ksu8r89qk5z2zxfmu5gv8th8wclt0h4p")
SPEC_SECRET = "3501454135014541350145413501453fefb02227e449e57cf4d3a3ce05378683"
SK = bytes.fromhex("6e" * 32)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def settle():
    for _ in range(3):
        QApplication.processEvents()


# -- NIP-49 ---------------------------------------------------------------------------

def test_hchacha20_matches_the_xchacha_draft_vector():
    out = nip49.hchacha20(bytes(range(32)), bytes.fromhex("000000090000004a0000000031415927"))
    assert out.hex() == "82413b4227b27bfed30e42508a877d73a0f9e4d58a74a853c12ec41326d3ecdc"


def test_the_spec_vector_opens_with_its_password():
    assert nip49.decrypt(SPEC_NCRYPTSEC, "nostr").hex() == SPEC_SECRET


def test_a_wrong_password_is_told_apart_from_a_damaged_key():
    with pytest.raises(nip49.Nip49Error) as wrong:
        nip49.decrypt(SPEC_NCRYPTSEC, "not it")
    assert wrong.value.wrong_password
    with pytest.raises(nip49.Nip49Error) as damaged:
        nip49.decrypt(SPEC_NCRYPTSEC[:-5] + "qqqqq", "nostr")
    assert not damaged.value.wrong_password


def test_a_key_survives_the_round_trip_and_records_how_it_was_kept():
    sealed = nip49.encrypt(SK, "correct horse", key_security=nip49.KEY_SECURE)
    assert sealed.startswith("ncryptsec1") and nip49.is_ncryptsec(sealed)
    assert nip49.decrypt(sealed, "correct horse") == SK
    assert nip49.key_security_of(sealed) == nip49.KEY_SECURE


def test_passwords_are_normalized_so_the_same_word_opens_everywhere():
    sealed = nip49.encrypt(SK, "Å")            # precomposed A-ring
    assert nip49.decrypt(sealed, "Å") == SK   # A + combining ring


def test_weak_settings_are_refused_on_import():
    weak = nip49.encrypt(SK, "pw", log_n=10)
    with pytest.raises(nip49.Nip49Error):
        nip49.decrypt(weak, "pw")


# -- the key file ------------------------------------------------------------------------

def test_the_key_file_is_private_and_keys_come_back(tmp_path):
    vault = KeyVault(tmp_path / "cfg" / "nostr_keys.json")
    pubkey = vault.store(SK)
    assert pubkey == crypto.get_public_key(SK).hex()
    assert vault.load(pubkey) == SK and vault.has(pubkey)
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(tmp_path / "cfg" / "nostr_keys.json").st_mode)
        assert mode == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "cfg").st_mode) == 0o700


def test_a_key_filed_under_the_wrong_public_key_is_not_handed_out(tmp_path):
    path = tmp_path / "nostr_keys.json"
    vault = KeyVault(path)
    other = "ab" * 32
    path.write_text('{"version": 1, "keys": {"%s": "%s"}}' % (other, SK.hex()))
    assert vault.load(other) is None


def test_forgetting_a_key_removes_it(tmp_path):
    vault = KeyVault(tmp_path / "nostr_keys.json")
    pubkey = vault.store(SK)
    vault.forget(pubkey)
    assert vault.load(pubkey) is None


def test_a_missing_or_broken_file_means_no_keys(tmp_path):
    path = tmp_path / "nostr_keys.json"
    assert KeyVault(path).load("ab" * 32) is None
    path.write_text("{not json")
    assert KeyVault(path).load("ab" * 32) is None


# -- the local signer ----------------------------------------------------------------------

def test_signing_answers_later_and_the_signature_verifies():
    signer = LocalSigner(SK)
    got = []
    signer.sign_event({"kind": 1, "content": "hi", "tags": [], "created_at": 1},
                      got.append, lambda r: got.append(("fail", r)))
    assert got == []          # never inside the call
    settle()
    assert events.verify_event(got[0])
    assert got[0]["pubkey"] == signer.user_pubkey


def test_self_encryption_round_trips():
    signer = LocalSigner(SK)
    out = {}
    signer.nip44_encrypt_self("secret draft", lambda c: out.setdefault("c", c), print)
    settle()
    signer.nip44_decrypt_self(out["c"], lambda p: out.setdefault("p", p), print)
    settle()
    assert out["p"] == "secret draft"


def test_a_closed_signer_refuses():
    signer = LocalSigner(SK)
    signer.close()
    failed = []
    signer.sign_event({"kind": 1, "content": "", "tags": [], "created_at": 1},
                      print, failed.append)
    settle()
    assert failed == ["not connected"]


# -- the session pool --------------------------------------------------------------------------

def local_profile(pubkey):
    return Profile(user_pubkey=pubkey, bunker_pubkey="", bunker_relays=["wss://nos.lol"],
                   local_secret_hex="", signer="local")


def test_a_local_profile_gets_a_local_signer(tmp_path):
    vault = KeyVault(tmp_path / "nostr_keys.json")
    pubkey = vault.store(SK)
    pool = BunkerSessionPool(pool=None, vault=vault)
    got = []
    pool.get(local_profile(pubkey), got.append, lambda r: got.append(("fail", r)))
    assert isinstance(got[0], LocalSigner) and got[0].user_pubkey == pubkey
    again = []
    pool.get(local_profile(pubkey), again.append, print)
    assert again[0] is got[0]


def test_a_local_profile_without_its_key_is_explained(tmp_path):
    pool = BunkerSessionPool(pool=None, vault=KeyVault(tmp_path / "nostr_keys.json"))
    failed = []
    pool.get(local_profile("cd" * 32), print, failed.append)
    assert failed and "not on this computer" in failed[0]


def test_old_profile_files_load_as_signer_app_accounts():
    profile = Profile(user_pubkey="a" * 64, bunker_pubkey="b" * 64,
                      bunker_relays=[], local_secret_hex="c" * 64)
    assert profile.signer == "remote" and not profile.is_local
