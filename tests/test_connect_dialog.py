# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the pairing dialog as Create Account uses it.

What must hold:

  The dialog shows the code to scan right away (most signer apps connect
  that way), dark on white whatever the theme, and pasting a link is the
  other way. Opened from Create Account it offers no way off to the
  windows it is part of, and saves nothing: the paired profile goes to the
  account window, which checks it first. Opened from Connect Signer App,
  it saves the paired profile as before.

  It speaks the person's words: no URI, pubkey, hex, NIP or relay on
  screen, and a link that cannot be used is explained in one sentence.

  A code that runs out says so and offers a new one, without an error.

  Closing the dialog, for any reason, closes its pairing channel.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from nostr.profiles import Profile, ProfileStore  # noqa: E402
from nostr.ui.connect_dialog import PASTE, SCAN, ConnectDialog  # noqa: E402

PK = "ab" * 32


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class FakeSubscription(QObject):
    event = Signal(dict)

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


class FakePool:
    def __init__(self):
        self.subscriptions = []

    def subscribe(self, urls, filters, sub_id=None):
        sub = FakeSubscription()
        self.subscriptions.append(sub)
        return sub


class PairedClient:
    bunker_pubkey = "b" * 64
    relays = ["wss://relay.example"]
    local_secret_hex = "c" * 64

    def __init__(self):
        self.closed = False

    def close(self, reason=""):
        self.closed = True


def pair(dialog):
    dialog._client = PairedClient()
    got = []
    dialog.profile_connected.connect(got.append)
    dialog._on_pair_success(PK)
    return got


def test_create_account_pairing_saves_nothing(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    existing = Profile(user_pubkey=PK, bunker_pubkey="d" * 64, bunker_relays=[],
                       local_secret_hex="e" * 64, display_name="Already here")
    store.upsert(existing)
    dialog = ConnectDialog(FakePool(), store, persist=False)
    got = pair(dialog)
    assert got and got[0].bunker_pubkey == "b" * 64
    assert store.get(PK).display_name == "Already here"
    assert ProfileStore(tmp_path / "profiles.json").get(PK).bunker_pubkey == "d" * 64


def test_connect_signer_pairing_saves_the_profile(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    dialog = ConnectDialog(FakePool(), store)
    pair(dialog)
    assert store.get(PK).bunker_pubkey == "b" * 64


def test_create_account_opens_on_the_code_with_no_way_off(tmp_path):
    pool = FakePool()
    dialog = ConnectDialog(pool, ProfileStore(tmp_path / "p.json"),
                           offer_alternatives=False)
    assert dialog._tabs.currentIndex() == SCAN
    assert pool.subscriptions and not dialog._qr_label.pixmap().isNull()
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    assert "Create an Account" not in labels and "Restore from Backup" not in labels
    dialog.reject()
    assert pool.subscriptions[0].closed and dialog._client is None


def test_the_alternatives_are_offered_from_connect_signer(tmp_path):
    dialog = ConnectDialog(FakePool(), ProfileStore(tmp_path / "p.json"))
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    assert {"Create an Account", "Restore from Backup"} <= labels
    assert dialog._tabs.currentIndex() == SCAN      # the code first, here too


def test_a_paired_dialog_closes_its_channel_too(tmp_path):
    dialog = ConnectDialog(FakePool(), ProfileStore(tmp_path / "p.json"), persist=False)
    dialog._client = client = PairedClient()
    dialog.profile_connected.connect(lambda _profile: None)
    dialog._on_pair_success(PK)
    assert client.closed and dialog._client is None


def visible_words(dialog):
    from PySide6.QtWidgets import QLabel, QLineEdit, QTabWidget
    words = [w.text() for w in dialog.findChildren(QLabel) + dialog.findChildren(QPushButton)]
    words += [w.placeholderText() for w in dialog.findChildren(QLineEdit)]
    tabs = dialog.findChildren(QTabWidget)[0]
    words += [tabs.tabText(i) for i in range(tabs.count())]
    return " ".join(words + [dialog.windowTitle()])


def test_the_words_are_the_persons_not_the_protocols(tmp_path):
    dialog = ConnectDialog(FakePool(), ProfileStore(tmp_path / "p.json"))
    dialog._tabs.setCurrentIndex(PASTE)
    shown = visible_words(dialog).lower()
    for jargon in ("uri", "pubkey", "hex", "nip-46", "relay", "nostrconnect", "qr"):
        assert jargon not in shown, jargon
    assert dialog.windowTitle() == "Connect Signer App"
    assert [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())] == [
        "Scan Code", "Paste Link"]


def test_the_code_is_dark_on_white_in_either_theme(tmp_path):
    for dark in (True, False):
        dialog = ConnectDialog(FakePool(), ProfileStore(tmp_path / "p.json"), is_dark=dark)
        image = dialog._qr_label.pixmap().toImage()
        corner = image.pixelColor(2, 2)                  # the quiet zone
        assert corner.lightness() > 240, dark
        dialog.reject()


def test_a_link_that_cannot_be_used_is_explained_in_one_sentence(tmp_path):
    dialog = ConnectDialog(FakePool(), ProfileStore(tmp_path / "p.json"))
    dialog._tabs.setCurrentIndex(PASTE)
    dialog._paste_edit.setText("bunker://not-a-key?relay=wss://a.example")
    assert dialog._paste_btn.isEnabled() and dialog._paste_btn.isDefault()
    dialog._paste_btn.click()
    message = dialog._status.text()
    assert "bunker://" in message and "hex" not in message and "pubkey" not in message
    assert dialog._client is None


def test_a_code_that_runs_out_offers_a_new_one_without_an_error(tmp_path):
    pool = FakePool()
    dialog = ConnectDialog(pool, ProfileStore(tmp_path / "p.json"))
    dialog._qr_seconds_left = 1
    dialog._tick_countdown()
    assert not dialog._regen_btn.isHidden()
    assert dialog._countdown_label.text() == "This code has expired."
    assert dialog._status.text() == "" and dialog._client is None
    dialog._on_pair_failure("timed out waiting for a signer to scan the QR")
    assert dialog._status.text() == ""                 # running out is not an error
    dialog._regen_btn.click()
    assert dialog._regen_btn.isHidden() and dialog._client is not None
    assert len(pool.subscriptions) == 2
    dialog.reject()
