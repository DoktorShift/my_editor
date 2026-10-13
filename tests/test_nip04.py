# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading NIP-04, the older encryption: never writing it.

EINUNDZWANZIG STANDUP's list of sources may be tagged "nip04"; MyEditor
reads it with a key kept here or through a signer app (NIP-46
``nip04_decrypt``) and writes the list back with NIP-44 only.

The vectors come from nak 0.20.7 (an independent implementation, in Go):
``nak encrypt --nip04 --sec <sender> -p <recipient> <text>``.
"""

from __future__ import annotations

import base64

import pytest
from PySide6.QtCore import QCoreApplication

from nostr import crypto
from nostr.local_signer import LocalSigner
from tests.outbox_fakes import settle

SK1 = "7f7ff03d123792d6ac594bfa67bf6d0c0ab55b6b1fdb6249303fe861f1ccba9a"
SK2 = "c15d739894c81a2fcfd3a2df85a0d2c0dbc47a280d092799f144d73d7ae78add"
PK1 = "17162c921dc4d2518f9a101db33695df1afb56ab82f5ff3e5da6eec3ca5cd917"
PK2 = "d41b22899549e1f3d335a31002cfd382174006e166d3e658e3a5eecdb6463573"

TO_SELF = ("vxyiVpf+Z28fHyNMCud1V2/Lgd1iBQirtxFp3MNCxeS7A+ARnBIs7EuyzSwyG+2fXYn/QjHBJF4UCW64"
           "qbwe+bfyyoZS4F/dta2RH9XILgMq9ja1y0L6ezKpZLqx8lfl?iv=SoL/obIMEOTN9VFeejNiqw==")
TO_SELF_TEXT = ('{"feeds": [{"url": "https://blog.example/feed", "title": "Ein Blog über '
                'Bäume"}]}')
TO_TWO = "bQ5dxxkyZ9QsmWfZWeolxQ==?iv=5ib9fBGNf54n3OfAmfyM5Q=="
SIXTEEN = "zYhEmZI30m/0dHJ4xQ/dFlYZYRJTaGv/74pFK4+7l04=?iv=YqBr3McXWFhx+PCT+9zn/g=="


def key(hex_text: str) -> bytes:
    return bytes.fromhex(hex_text)


class TestTheVectors:
    def test_a_message_to_oneself(self):
        assert crypto.nip04_decrypt(TO_SELF, key(SK1), key(PK1)) == TO_SELF_TEXT

    def test_either_side_reads_a_message_between_two(self):
        assert crypto.nip04_decrypt(TO_TWO, key(SK2), key(PK1)) == "hello, two"
        assert crypto.nip04_decrypt(TO_TWO, key(SK1), key(PK2)) == "hello, two"

    def test_sixteen_bytes_take_a_whole_padding_block(self):
        assert crypto.nip04_decrypt(SIXTEEN, key(SK1), key(PK1)) == "exactly sixteen!"

    def test_nothing_writes_nip04(self):
        assert not [name for name in dir(crypto) if "nip04" in name and "encrypt" in name]


class TestWhatIsRefused:
    def test_a_wrong_key(self):
        with pytest.raises(ValueError):
            crypto.nip04_decrypt(TO_TWO, key(SK2), key(PK2))

    @pytest.mark.parametrize("content", [
        "no separator", "bQ5dxxkyZ9QsmWfZWeolxQ==?iv=", "bQ5dxxkyZ9QsmWfZWeolxQ==?iv=AAAA",
        "bQ5dxxkyZ9QsmWfZWeolxQ==?iv=not base64!", "?iv=5ib9fBGNf54n3OfAmfyM5Q==",
        "bQ5dxxkyZ9Qs?iv=5ib9fBGNf54n3OfAmfyM5Q==",           # not a whole block
        "",
    ])
    def test_a_malformed_message(self, content):
        with pytest.raises(ValueError):
            crypto.nip04_decrypt(content, key(SK2), key(PK1))

    def test_damaged_padding(self):
        # The last byte of the first block shifts the padding byte of the
        # second (CBC): 0x10 becomes 0x11, which no padding is.
        data, iv = SIXTEEN.split("?iv=")
        raw = bytearray(base64.b64decode(data))
        raw[15] ^= 0x01
        damaged = base64.b64encode(bytes(raw)).decode() + "?iv=" + iv
        with pytest.raises(ValueError, match="padding"):
            crypto.nip04_decrypt(damaged, key(SK1), key(PK1))


class TestTheSigners:
    def test_a_key_kept_here_reads_it(self):
        app = QCoreApplication.instance()
        assert app is not None
        signer = LocalSigner(key(SK1))
        out = {}
        signer.nip04_decrypt_self(TO_SELF, lambda text: out.update(text=text),
                                  lambda reason: out.update(failed=reason))
        settle()
        assert out == {"text": TO_SELF_TEXT}
        signer.nip04_decrypt(PK2, TO_TWO, lambda text: out.update(two=text),
                             lambda reason: out.update(failed=reason))
        settle()
        assert out["two"] == "hello, two"
        signer.nip04_decrypt_self("broken?iv=", lambda text: out.update(bad=text),
                                  lambda reason: out.update(failed=reason))
        settle()
        assert "failed" in out and "bad" not in out
        signer.close()

    @pytest.fixture
    def remote(self):
        """A signer app over NIP-46 (the handshake tests' fake), holding
        the account's key."""
        from tests.test_bunker_handshake import _bunker_client
        _pool, signer, client, outcome, start = _bunker_client()
        signer.user_sk = key(SK1)
        signer.user_pk = PK1
        handled = signer._on_event

        def answer(event):
            import json
            if event.get("pubkey") == signer.pk:
                return
            try:
                payload = json.loads(crypto.decrypt(event["content"], signer.conv))
            except ValueError:
                return
            if payload.get("method") != "nip04_decrypt":
                handled(event)
                return
            signer.seen.append(payload)
            if getattr(signer, "refuse", False):
                signer.send({"id": payload["id"], "error": "permission denied"})
                return
            peer, content = payload["params"]
            signer.send({"id": payload["id"], "result": crypto.nip04_decrypt(
                content, signer.user_sk, key(peer))})
        signer._sub.event.disconnect()
        signer._sub.event.connect(answer)
        start()
        assert outcome.get("ok")
        return signer, client

    def test_a_signer_app_reads_it(self, remote):
        signer, client = remote
        out = {}
        client.nip04_decrypt_self(TO_SELF, on_success=lambda text: out.update(text=text),
                                  on_failure=lambda reason: out.update(failed=reason))
        settle()
        assert out == {"text": TO_SELF_TEXT}
        asked = signer.seen[-1]
        assert asked["method"] == "nip04_decrypt" and asked["params"] == [PK1, TO_SELF]

    def test_a_signer_app_that_refuses(self, remote):
        signer, client = remote
        signer.refuse = True
        out = {}
        client.nip04_decrypt_self(TO_SELF, on_success=lambda text: out.update(text=text),
                                  on_failure=lambda reason: out.update(failed=reason))
        settle()
        assert "permission denied" in out.get("failed", "")
        assert "text" not in out

    def test_it_is_asked_for_when_pairing(self):
        from nostr.bunker import DEFAULT_PERMS
        assert "nip04_decrypt" in DEFAULT_PERMS.split(",")
        assert "nip04_encrypt" not in DEFAULT_PERMS
