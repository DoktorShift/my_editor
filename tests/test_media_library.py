# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Media Library window: a familiar library, with privacy handled.

What must hold:

  The grid shows names (the one a file was added with, a private
  file's own, else the day it was added), never a hash, a type or a
  server. Public files, the normal case, carry no mark; a private one,
  the exception, carries a lock, and one nobody could check carries
  none. The window says once, at its foot, who can see its files. A
  public copy of a private original is not a second tile.

  An empty library, a loading one, a search with no match and a
  library that could not be listed each say one plain sentence, with
  the one action that helps.

  A file being added is a tile with its own progress, Cancel, and on
  failure the plain reason with Retry; a server that did not keep a
  copy is said once, when the file is added.

  Delete asks first, from the keys and the menus alike, and never says
  a file is gone while a server still has it.

  The picker inserts with Return, and refuses a file nobody could check,
  saying so under the grid with Try Again.

  Thumbnails are asked for as tiles come into view; a private picture
  is opened in memory and nothing decrypted reaches the blob cache.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import alerts  # noqa: E402
import nostr.ui.media_library_dialog as dialog_module  # noqa: E402
from nostr.blossom.store import MediaFile, ServerListing  # noqa: E402
from nostr.media.filecrypto import encrypt_file  # noqa: E402
from nostr.media.media_visibility import MediaVisibility  # noqa: E402
from nostr.media.visibility import PrivateBlob, PublicBlob, PublicLedger  # noqa: E402
from nostr.ui.media_grid import FAILED, SENDING, TILE_ROLE, WAITING  # noqa: E402
from nostr.ui.media_library_dialog import MediaLibraryDialog, storage_text  # noqa: E402
from tests.media_fakes import PNG_BYTES, fake_alert, sha_of  # noqa: E402

KEY = "5c" * 32
SERVER = "https://one.example"
MEMBERS = "https://members.example"
GB = 1024 ** 3


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def close_windows():
    """Every window a test opens is closed after it, so no later test
    finds it still the active window."""
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MediaLibraryDialog) and widget.isVisible():
            widget.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeStore(QObject):
    """MediaStore's surface, recording what the window asks of it."""

    library_changed = Signal()
    fetch_started = Signal()
    fetch_finished = Signal()
    fetch_error = Signal(str)
    upload_started = Signal(str)
    upload_progress = Signal(str, int, int)
    upload_status = Signal(str, str)
    upload_finished = Signal(str, object)
    upload_failed = Signal(str, str)
    upload_cancelled = Signal(str)
    server_skipped = Signal(str, str, str)
    mirror_failed = Signal(str, str, str)
    listings_changed = Signal()
    file_deleted = Signal(str)
    delete_failed = Signal(str, str)

    def __init__(self, records=(), *, names=None, quota=None, used=0, listing_ok=True):
        super().__init__()
        self.files = {m.hash: m for m in records}
        self.names = dict(names or {})
        self.quota, self.used, self.listing_ok = quota, used, listing_ok
        self.calls = []
        self.refuse_on_add = ""

    def target_servers(self):
        return [SERVER, MEMBERS]

    def server_listings(self):
        return {s: ServerListing(origin=s, ok=self.listing_ok) for s in self.target_servers()}

    def quota_for(self, origin):
        return self.quota if origin == MEMBERS else None

    def bytes_on(self, origin):
        return self.used

    def name_of(self, sha):
        return self.names.get(sha, "")

    def file_list(self, *, filter_type="all", sort_by="newest"):
        files = list(self.files.values())
        if sort_by == "largest":
            return sorted(files, key=lambda m: -m.size)
        return sorted(files, key=lambda m: -m.uploaded_at_ms)

    def fetch(self, force=False):
        self.calls.append(("fetch", force))

    def upload_file(self, path):
        name = os.path.basename(path)
        self.calls.append(("upload_file", name))
        if self.refuse_on_add:
            self.upload_failed.emit(name, self.refuse_on_add)
        return name

    def add_bytes(self, body, *, label, mime_type):
        self.calls.append(("add_bytes", label, mime_type))
        return label

    def upload_job(self, name):
        return None

    def added_uploads(self):
        return list(getattr(self, "added", []))

    def can_retry(self, name):
        return True

    def cancel_upload(self, name):
        self.calls.append(("cancel", name))
        return True

    def retry_upload(self, name):
        self.calls.append(("retry", name))

    def dismiss_upload(self, name):
        self.calls.append(("dismiss", name))

    def delete_file(self, sha):
        self.calls.append(("delete", sha))


class FakeLoader(QObject):
    ready = Signal(str, str, object)
    failed = Signal(str, str)
    url_ready = Signal(str, str, object)
    url_failed = Signal(str, str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.loads = []

    def cache_path(self, sha):
        return self.root / sha.lower()

    def load(self, sha, url):
        self.loads.append(sha)


class FakeLibrary(QObject):
    library_changed = Signal()
    status_changed = Signal(str)

    def __init__(self, blobs=(), *, vouches=True, loading=False):
        super().__init__()
        self.blobs = {b.sha256: b for b in blobs}
        self.vouches, self.loading = vouches, loading
        self.refreshed = 0

    @property
    def settled(self):
        return self.vouches and not self.loading

    def get(self, sha):
        return self.blobs.get(sha)

    def vouches_for(self, sha):
        return self.vouches

    def refresh(self):
        self.refreshed += 1


def media(n, *, mime="image/png", size=1000, day=0):
    sha = f"{n:064x}"
    return MediaFile(hash=sha, url=f"{SERVER}/{sha}.png", urls=[{"server": SERVER, "url": ""}],
                     mime_type=mime, size=size,
                     uploaded_at_ms=1_780_000_000_000 - day * 86_400_000)


def build(tmp_path, monkeypatch, store=None, *, pick=False, visibility=None, library=None,
          size=(860, 560)):
    cache = tmp_path / "cache"
    loaders = []

    def make_loader(parent=None):
        loader = FakeLoader(cache, parent=parent)
        loaders.append(loader)
        return loader

    monkeypatch.setattr(dialog_module, "ThumbnailLoader", make_loader)
    store = store if store is not None else FakeStore()
    dialog = MediaLibraryDialog(store=store, is_dark=False, pick_mode=pick,
                                visibility=visibility)
    if library is not None:
        dialog.bind_private_library(library)
    dialog.resize(*size)
    dialog.show()
    QCoreApplication.processEvents()
    return SimpleNamespace(dialog=dialog, store=store, loader=loaders[0], grid=dialog._grid,
                           model=dialog._model)


def tiles(world):
    return world.model.tiles


def names(world):
    return [tile.name for tile in tiles(world)]


def select(world, key):
    row = world.model.row_of(key)
    world.grid.setCurrentIndex(world.model.index(row))


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def sealed(data=PNG_BYTES, key=KEY):
    return encrypt_file(data, key_hex=key).envelope


# --------------------------------------------------------------------------- #
# Names, and what a tile says
# --------------------------------------------------------------------------- #

def test_a_tile_is_named_by_the_name_it_was_added_with(tmp_path, monkeypatch):
    one, two = media(1, day=1), media(2, day=2)
    world = build(tmp_path, monkeypatch, FakeStore([one, two], names={one.hash: "Harbour.jpg"}))
    harbour, unnamed = tiles(world)
    assert harbour.name == "Harbour.jpg"
    assert harbour.detail == "May 27, 2026"
    # No name: the day it was added, and its size beneath.
    assert unnamed.name == "May 26, 2026"
    assert unnamed.detail == "1000 B"


def test_no_hash_type_or_server_is_on_a_tile(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, FakeStore([media(1)]))
    tile = tiles(world)[0]
    for text in (tile.name, tile.detail, tile.tooltip(), tile.accessible_text()):
        assert tile.key not in text and "image/png" not in text and "one.example" not in text


def test_a_public_file_the_normal_case_carries_no_badge(tmp_path, monkeypatch):
    """Everything MyEditor uploads is public, so a mark on it would be on
    nearly every tile and say nothing. The exception is marked instead."""
    world = build(tmp_path, monkeypatch, FakeStore([media(1)], names={f"{1:064x}": "Garden.jpg"}))
    tile = tiles(world)[0]
    assert not tile.private
    assert tile.accessible_text() == "Garden.jpg, image"
    assert tile.tooltip() == "Garden.jpg"


def test_a_private_file_the_exception_carries_the_lock(tmp_path, monkeypatch):
    envelope = sealed()
    private = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}",
                        mime_type="application/octet-stream", size=10)
    blob = PrivateBlob(sha256=private.hash, key_hex=KEY, mime="image/png", name="Secret.png")
    visibility = MediaVisibility(library=FakeLibrary([blob]))
    world = build(tmp_path, monkeypatch, FakeStore([private]), visibility=visibility)
    tile = tiles(world)[0]
    assert (tile.name, tile.kind, tile.private) == ("Secret.png", "image", True)
    assert tile.accessible_text() == "Secret.png, image, private"
    assert tile.tooltip() == "Secret.png\nOnly you can see this"


def test_a_file_nobody_could_check_carries_no_badge(tmp_path, monkeypatch):
    unchecked = MediaVisibility(library=FakeLibrary(vouches=False))
    world = build(tmp_path, monkeypatch, FakeStore([media(1)]), visibility=unchecked)
    assert not tiles(world)[0].private


def test_the_window_says_once_who_can_see_its_files(tmp_path, monkeypatch):
    """Truthful about the normal case without marking every tile: one
    quiet line at the foot, where Finder and Photos say what is true of
    the whole window, and in the empty state."""
    world = build(tmp_path, monkeypatch)
    line = "Anyone with the link can see the files you add here."
    assert world.dialog._status.text() == line
    assert world.dialog._empty_text.text() == (
        "Images, videos and audio you add appear here. Anyone with the link can see them.")
    world.dialog._say("“Garden.jpg” is no longer shared.")
    assert world.dialog._status.text() == "“Garden.jpg” is no longer shared."
    world.dialog._status_timer.timeout.emit()
    assert world.dialog._status.text() == line
    # A problem stays until something else is said.
    world.dialog._say("“Garden.jpg” couldn’t be saved. Try again.", error=True)
    assert not world.dialog._status_timer.isActive()


def test_a_public_copy_of_a_private_original_is_one_tile(tmp_path, monkeypatch):
    envelope = sealed()
    original = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}", size=10,
                         uploaded_at_ms=2)
    copy = media(7)
    blob = PrivateBlob(sha256=original.hash, key_hex=KEY, mime="image/png", name="Beach.png")
    ledger = PublicLedger(path=tmp_path / "public.json")
    ledger.record(PublicBlob(sha256=copy.hash, url=copy.url, source_hash=original.hash))
    visibility = MediaVisibility(library=FakeLibrary([blob]), ledger=ledger)
    world = build(tmp_path, monkeypatch, FakeStore([original, copy]), visibility=visibility)
    assert names(world) == ["Beach.png"]
    assert not tiles(world)[0].private            # its copy is out there


# --------------------------------------------------------------------------- #
# Finding things
# --------------------------------------------------------------------------- #

def test_the_kind_pop_up_shows_images_second(tmp_path, monkeypatch):
    """The article cover's picker chooses Images by index (cover_field)."""
    store = FakeStore([media(1), media(2, mime="video/mp4")])
    world = build(tmp_path, monkeypatch, store)
    world.dialog._filter_combo.setCurrentIndex(1)
    assert [t.kind for t in tiles(world)] == ["image"]


def test_search_and_name_order(tmp_path, monkeypatch):
    files = [media(1, day=1), media(2, day=2), media(3, day=3)]
    store = FakeStore(files, names={files[0].hash: "Zebra.png", files[1].hash: "apple.png",
                                    files[2].hash: "Mango.png"})
    world = build(tmp_path, monkeypatch, store)
    world.dialog._sort_combo.setCurrentIndex(world.dialog._sort_combo.findData("name"))
    assert names(world) == ["apple.png", "Mango.png", "Zebra.png"]
    world.dialog._search.setText("an")
    assert names(world) == ["Mango.png"]
    world.dialog._search.setText("nothing like it")
    assert world.dialog._stack.currentWidget() is world.dialog._empty
    assert world.dialog._empty_text.text() == "No items match “nothing like it”."


def test_an_empty_library_says_one_sentence_and_offers_add(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    assert world.dialog._empty_text.text() == (
        "Images, videos and audio you add appear here. Anyone with the link can see them.")
    assert world.dialog._empty_button.text() == "Add…"


def test_a_loading_library_says_so(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    world.store.fetch_started.emit()
    assert world.dialog._empty_text.text() == "Loading…"
    assert world.dialog._empty_button.isHidden()


def test_a_library_that_could_not_be_listed_says_why_and_offers_try_again(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    world.store.fetch_error.emit("NETWORK_UNAVAILABLE")
    assert world.dialog._empty_text.text() == (
        "Couldn’t reach your media servers. Check your internet connection.")
    assert world.dialog._empty_button.text() == "Try Again"
    world.dialog._empty_button.click()
    assert ("fetch", True) in world.store.calls

    world = build(tmp_path, monkeypatch)
    world.store.fetch_error.emit("SIGNER_REJECTED")
    assert "signer app" in world.dialog._empty_text.text()


def test_thumbnails_are_asked_for_as_tiles_come_into_view(tmp_path, monkeypatch):
    store = FakeStore([media(n) for n in range(200)])
    world = build(tmp_path, monkeypatch, store, size=(560, 420))
    world.dialog._load_visible()
    asked = len(world.loader.loads)
    assert 0 < asked < 60
    world.grid.verticalScrollBar().setValue(world.grid.verticalScrollBar().maximum())
    world.dialog._load_visible()
    assert f"{0:064x}" in world.loader.loads and len(world.loader.loads) > asked


# --------------------------------------------------------------------------- #
# Adding files
# --------------------------------------------------------------------------- #

def test_an_added_file_is_a_tile_with_its_own_progress(tmp_path, monkeypatch):
    path = tmp_path / "Beach day.png"
    path.write_bytes(PNG_BYTES)
    world = build(tmp_path, monkeypatch)
    world.dialog._add_paths([str(path)])
    tile = world.model.tile("Beach day.png")
    assert (tile.state, tile.name) == (WAITING, "Beach day.png")
    world.store.upload_started.emit("Beach day.png")
    world.store.upload_progress.emit("Beach day.png", 420, 1000)
    assert (tile.state, tile.progress) == (SENDING, 42)
    assert tile.accessible_text() == "Beach day.png, image, uploading"

    landed = media(9)
    world.store.files[landed.hash] = landed
    world.store.upload_finished.emit("Beach day.png", landed)
    assert world.model.tile("Beach day.png") is None
    assert world.grid.currentIndex().data(TILE_ROLE).key == landed.hash


def test_a_failed_upload_says_why_on_the_tile_and_can_be_retried(tmp_path, monkeypatch):
    path = tmp_path / "Scan.png"
    path.write_bytes(PNG_BYTES)
    world = build(tmp_path, monkeypatch)
    world.dialog._add_paths([str(path)])
    reason = "Couldn’t reach your media server. Check your internet connection."
    world.store.upload_failed.emit("Scan.png", reason)
    tile = world.model.tile("Scan.png")
    assert (tile.state, tile.detail) == (FAILED, reason)
    assert world.dialog._status.text() == f"“Scan.png” wasn’t added. {reason}"
    world.grid.retry_requested.emit("Scan.png")
    assert ("retry", "Scan.png") in world.store.calls
    assert tile.state == WAITING


def test_a_refusal_before_the_upload_starts_reaches_the_tile(tmp_path, monkeypatch):
    path = tmp_path / "Huge.png"
    path.write_bytes(PNG_BYTES)
    store = FakeStore()
    store.refuse_on_add = "This file is larger than your media servers take (100.0 MB)."
    world = build(tmp_path, monkeypatch, store)
    world.dialog._add_paths([str(path)])
    tile = world.model.tile("Huge.png")
    assert (tile.state, tile.detail) == (FAILED, store.refuse_on_add)


def test_cancel_and_remove(tmp_path, monkeypatch):
    path = tmp_path / "Clip.mp4"
    path.write_bytes(b"x")
    world = build(tmp_path, monkeypatch)
    world.dialog._add_paths([str(path)])
    world.grid.cancel_requested.emit("Clip.mp4")
    assert ("cancel", "Clip.mp4") in world.store.calls
    world.store.upload_cancelled.emit("Clip.mp4")
    assert world.model.tile("Clip.mp4") is None


def test_a_file_that_is_no_media_is_not_added_and_it_says_so(tmp_path, monkeypatch):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    world = build(tmp_path, monkeypatch)
    world.dialog._add_paths([str(path)])
    assert world.store.calls == [("fetch", False)]
    assert world.dialog._status.text() == (
        "“notes.txt” wasn’t added. The Media Library takes images, videos and audio.")


def test_a_server_that_did_not_keep_a_copy_is_said_once_when_the_file_lands(
        tmp_path, monkeypatch):
    path = tmp_path / "Garden.png"
    path.write_bytes(PNG_BYTES)
    world = build(tmp_path, monkeypatch)
    world.dialog._add_paths([str(path)])
    world.store.mirror_failed.emit("Garden.png", "members.example", "NETWORK_UNAVAILABLE")
    assert world.dialog._status.text() == "Anyone with the link can see the files you add here."
    world.store.upload_finished.emit("Garden.png", media(3))
    assert world.dialog._status.text() == (
        "“Garden.png” is added, but one of your media servers didn’t keep a copy.")


# --------------------------------------------------------------------------- #
# Delete, copy, save
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("how", ["key", "command_backspace", "menu"])
def test_delete_asks_first_however_it_is_asked_for(tmp_path, monkeypatch, how):
    one = media(1)
    world = build(tmp_path, monkeypatch, FakeStore([one], names={one.hash: "Garden.jpg"}))
    alert, shown = fake_alert(click="Cancel")
    monkeypatch.setattr(alerts, "Alert", alert)
    select(world, one.hash)
    if how == "key":
        press(world.grid, Qt.Key.Key_Delete)
    elif how == "command_backspace":
        press(world.grid, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
    else:
        world.dialog._delete()
    assert shown[0].title == "Delete “Garden.jpg”?"
    assert shown[0].message == (
        "Anything you published with it will no longer show it. You can’t undo this action.")
    assert ("delete", one.hash) not in world.store.calls

    alert, shown = fake_alert(click="Delete")
    monkeypatch.setattr(alerts, "Alert", alert)
    world.dialog._delete()
    assert ("delete", one.hash) in world.store.calls


def test_a_file_a_server_still_has_is_never_called_gone(tmp_path, monkeypatch):
    one = media(1)
    world = build(tmp_path, monkeypatch, FakeStore([one], names={one.hash: "Garden.jpg"}))
    world.store.delete_failed.emit(one.hash, "NETWORK_UNAVAILABLE")
    assert world.dialog._status.text() == (
        "“Garden.jpg” is still on one of your media servers. Couldn’t reach your media "
        "server. Check your internet connection.")


def test_copy_link_copies_every_selected_address(tmp_path, monkeypatch):
    one, two = media(1, day=1), media(2, day=2)
    world = build(tmp_path, monkeypatch, FakeStore([one, two]))
    world.grid.selectAll()
    press(world.grid, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == f"{one.url}\n{two.url}"


def test_save_as_saves_a_video_that_was_not_downloaded_yet(tmp_path, monkeypatch):
    """The loader caches what it downloads before it tries to show it, so
    a video it cannot show is still there to save."""
    video = media(4, mime="video/mp4")
    world = build(tmp_path, monkeypatch, FakeStore([video], names={video.hash: "Talk.mp4"}))
    target = tmp_path / "out" / "Talk.mp4"
    target.parent.mkdir()
    monkeypatch.setattr(dialog_module.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    select(world, video.hash)
    world.dialog._save_as()
    assert world.loader.loads == [video.hash]
    world.loader.cache_path(video.hash).write_bytes(b"video bytes")
    world.loader.failed.emit(video.hash, "not an image")
    assert target.read_bytes() == b"video bytes"


def test_saving_a_private_file_saves_the_file_not_its_envelope(tmp_path, monkeypatch):
    envelope = sealed(b"the real picture")
    private = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}", size=10)
    blob = PrivateBlob(sha256=private.hash, key_hex=KEY, mime="image/png", name="Secret.png")
    world = build(tmp_path, monkeypatch, FakeStore([private]),
                  visibility=MediaVisibility(library=FakeLibrary([blob])))
    world.loader.cache_path(private.hash).write_bytes(envelope)
    target = tmp_path / "Secret.png"
    monkeypatch.setattr(dialog_module.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    select(world, private.hash)
    world.dialog._save_as()
    assert target.read_bytes() == b"the real picture"


# --------------------------------------------------------------------------- #
# Picking
# --------------------------------------------------------------------------- #

def test_return_inserts_the_chosen_file_with_its_alt_text(tmp_path, monkeypatch):
    one = media(1)
    world = build(tmp_path, monkeypatch, FakeStore([one]), pick=True)
    picked = []
    world.dialog.file_picked.connect(lambda m, alt: picked.append((m.hash, alt)))
    select(world, one.hash)
    world.dialog._alt_edit.setText("A garden")
    press(world.grid, Qt.Key.Key_Return)
    assert picked == [(one.hash, "A garden")]


def test_a_file_nobody_could_check_is_not_inserted_and_it_says_why(tmp_path, monkeypatch):
    one = media(1)
    library = FakeLibrary(vouches=False)
    world = build(tmp_path, monkeypatch, FakeStore([one]), pick=True,
                  visibility=MediaVisibility(library=library), library=library)
    picked = []
    world.dialog.file_picked.connect(lambda *a: picked.append(a))
    select(world, one.hash)
    assert not world.dialog._insert_button.isEnabled()
    assert not world.dialog._pick_notice.isHidden()
    assert world.dialog._pick_notice_text.text() == (
        "This image can’t be used until your signer app lets MyEditor check whether "
        "it is private.")
    press(world.grid, Qt.Key.Key_Return)
    assert picked == []
    world.dialog._pick_notice_button.click()
    assert library.refreshed == 1


def test_while_the_check_runs_the_picker_says_one_moment(tmp_path, monkeypatch):
    one = media(1)
    library = FakeLibrary(vouches=False, loading=True)
    world = build(tmp_path, monkeypatch, FakeStore([one]), pick=True,
                  visibility=MediaVisibility(library=library), library=library)
    select(world, one.hash)
    assert world.dialog._pick_notice_text.text() == (
        "MyEditor is still checking whether this image is private. One moment.")
    assert world.dialog._pick_notice_button.isHidden()


def test_the_library_itself_says_nothing_about_privacy(tmp_path, monkeypatch):
    library = FakeLibrary(vouches=False)
    world = build(tmp_path, monkeypatch, FakeStore([media(1)]),
                  visibility=MediaVisibility(library=library), library=library)
    select(world, f"{1:064x}")
    assert world.dialog._pick_notice.isHidden()


# --------------------------------------------------------------------------- #
# Private pictures
# --------------------------------------------------------------------------- #

def test_a_private_picture_is_fetched_despite_its_type_and_shown(tmp_path, monkeypatch):
    envelope = sealed()
    private = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}",
                        mime_type="application/octet-stream", size=10)
    blob = PrivateBlob(sha256=private.hash, key_hex=KEY, mime="image/png", name="Secret.png")
    world = build(tmp_path, monkeypatch, FakeStore([private]),
                  visibility=MediaVisibility(library=FakeLibrary([blob])))
    world.dialog._load_visible()
    assert world.loader.loads == [private.hash]
    world.loader.cache_path(private.hash).write_bytes(envelope)
    before = sorted(p.name for p in world.loader.root.iterdir())
    world.loader.failed.emit(private.hash, "not an image")
    assert tiles(world)[0].pixmap is not None
    # Nothing decrypted is written where an export would find it.
    assert sorted(p.name for p in world.loader.root.iterdir()) == before
    assert world.loader.cache_path(private.hash).read_bytes() == envelope


def test_a_private_video_is_not_downloaded_to_be_refused(tmp_path, monkeypatch):
    envelope = sealed()
    private = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}", size=10)
    blob = PrivateBlob(sha256=private.hash, key_hex=KEY, mime="video/mp4", name="Clip.mp4")
    world = build(tmp_path, monkeypatch, FakeStore([private]),
                  visibility=MediaVisibility(library=FakeLibrary([blob])))
    world.dialog._load_visible()
    assert world.loader.loads == []


def test_an_opaque_public_file_is_not_downloaded_on_a_hunch(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, FakeStore([media(1, mime="application/pdf")]))
    world.dialog._load_visible()
    assert world.loader.loads == []


# --------------------------------------------------------------------------- #
# Storage, servers, and the window itself
# --------------------------------------------------------------------------- #

def test_storage_is_one_quiet_line_when_it_is_known(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, FakeStore(quota=5 * GB, used=int(1.2 * GB)))
    assert world.dialog._storage.text() == "1.2 GB of 5 GB used"
    world = build(tmp_path, monkeypatch, FakeStore(quota=5 * GB, used=GB, listing_ok=False))
    assert world.dialog._storage.isHidden()
    world = build(tmp_path, monkeypatch, FakeStore())
    assert world.dialog._storage.isHidden()


def test_storage_text_says_nothing_it_does_not_know():
    assert storage_text(GB, 5 * GB, None) == ""
    assert storage_text(GB, 5 * GB, ServerListing(origin=SERVER, ok=False)) == ""
    almost = storage_text(int(4.6 * GB), 5 * GB, ServerListing(origin=SERVER))
    assert almost == "Almost full: 4.6 GB of 5 GB used"


def test_media_servers_is_in_the_action_menu(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    asked = []
    world.dialog.servers_requested.connect(lambda: asked.append(True))
    world.dialog._fill_actions_menu()
    action = [a for a in world.dialog._actions_menu.actions() if a.text() == "Media Servers…"][0]
    action.trigger()
    assert asked == [True]


def test_space_opens_quick_look_and_space_closes_it(tmp_path, monkeypatch):
    one = media(1)
    world = build(tmp_path, monkeypatch, FakeStore([one], names={one.hash: "Garden.jpg"}))
    select(world, one.hash)
    press(world.grid, Qt.Key.Key_Space)
    look = world.dialog._quick_look
    assert look.isVisible() and look.name.text() == "Garden.jpg"
    assert look.detail.text() == "Image  ·  1000 B  ·  May 28, 2026"
    press(look, Qt.Key.Key_Space)
    assert not look.isVisible()


def test_every_control_has_a_name(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, pick=True)
    d = world.dialog
    for widget in (d._filter_combo, d._sort_combo, d._search, d._actions_button, d._grid,
                   d._alt_edit):
        assert widget.accessibleName(), widget


def test_a_closed_library_leaves_nothing_connected_to_the_store(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc[1]))
    world = build(tmp_path, monkeypatch)
    world.dialog.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(world.dialog)
    for signal in (world.store.fetch_started, world.store.fetch_finished,
                   world.store.listings_changed, world.store.library_changed):
        signal.emit()
    world.store.fetch_error.emit("NETWORK_UNAVAILABLE")
    world.store.upload_failed.emit("x", "y")
    assert errors == []


@pytest.mark.parametrize("module", ["media_library_dialog", "media_grid", "media_preview",
                                    "media_servers_dialog"])
def test_no_colour_is_written_into_the_media_windows(module):
    """Every colour comes from the palette, theme.py or constants.py."""
    source = (Path(__file__).parent.parent / "nostr" / "ui" / f"{module}.py").read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9A-Fa-f]{6}\b", source)


# --------------------------------------------------------------------------- #
# Stop Sharing
# --------------------------------------------------------------------------- #

def _shared_original(tmp_path, monkeypatch):
    envelope = sealed()
    original = MediaFile(hash=sha_of(envelope), url=f"{SERVER}/{sha_of(envelope)}", size=10)
    copy_sha = "c" * 64
    blob = PrivateBlob(sha256=original.hash, key_hex=KEY, mime="image/png", name="Beach.png")
    ledger = PublicLedger(path=tmp_path / "public.json")
    ledger.record(PublicBlob(sha256=copy_sha, url=f"{MEMBERS}/{copy_sha}.png",
                             servers=[MEMBERS], source_hash=original.hash))
    # The copy's record names MEMBERS only; SERVER is configured.
    visibility = MediaVisibility(library=FakeLibrary([blob]), ledger=ledger)
    world = build(tmp_path, monkeypatch, FakeStore([original]), visibility=visibility)
    world.store.delete_file = lambda sha, servers=(): world.store.calls.append(
        ("delete", sha, tuple(servers)))
    return world, original, copy_sha, ledger


def _menu_texts(world):
    world.dialog._fill_actions_menu()
    return [a.text() for a in world.dialog._actions_menu.actions() if a.text()]


def test_stop_sharing_is_offered_only_for_a_shared_copy(tmp_path, monkeypatch):
    world, original, _copy, _ledger = _shared_original(tmp_path, monkeypatch)
    select(world, original.hash)
    assert "Stop Sharing" in _menu_texts(world)
    plain = build(tmp_path, monkeypatch, FakeStore([media(1)]))
    select(plain, f"{1:064x}")
    assert "Stop Sharing" not in _menu_texts(plain)


def test_stop_sharing_asks_then_deletes_the_copy_where_it_is(tmp_path, monkeypatch):
    world, original, copy_sha, ledger = _shared_original(tmp_path, monkeypatch)
    select(world, original.hash)
    alert, shown = fake_alert(click="Stop Sharing")
    monkeypatch.setattr(alerts, "Alert", alert)
    world.dialog._stop_sharing()
    assert shown[0].title == "Stop sharing “Beach.png”?"
    assert shown[0].message == (
        "Anyone with the link will no longer see it, and anything you published with it "
        "will no longer show it.")
    assert world.store.calls[-1][:2] == ("delete", copy_sha)
    # Every server the copy was put on (its record) and every server in
    # use now: a copy is not "no longer shared" while one of them has it.
    assert {MEMBERS, SERVER} <= set(world.store.calls[-1][2])

    world.store.file_deleted.emit(copy_sha)
    world.store.library_changed.emit()
    assert ledger.get(copy_sha) is None
    assert tiles(world)[0].private                # only the person again
    assert world.dialog._status.text() == "“Beach.png” is no longer shared."


def test_a_copy_a_server_still_has_is_still_shared_and_it_says_so(tmp_path, monkeypatch):
    world, original, copy_sha, ledger = _shared_original(tmp_path, monkeypatch)
    select(world, original.hash)
    monkeypatch.setattr(alerts, "Alert", fake_alert(click="Stop Sharing")[0])
    world.dialog._stop_sharing()
    world.store.delete_failed.emit(copy_sha, "NETWORK_UNAVAILABLE")
    assert ledger.get(copy_sha) is not None
    assert not tiles(world)[0].private
    assert world.dialog._status.text() == (
        "“Beach.png” is still shared. Couldn’t reach your media server. Check your "
        "internet connection.")


def test_deleting_a_shared_original_deletes_its_copy_too(tmp_path, monkeypatch):
    """Otherwise the copy stays public with no tile left to reach it."""
    world, original, copy_sha, _ledger = _shared_original(tmp_path, monkeypatch)
    select(world, original.hash)
    monkeypatch.setattr(alerts, "Alert", fake_alert(click="Delete")[0])
    world.dialog._delete()
    deleted = [call[1] for call in world.store.calls if call[0] == "delete"]
    assert deleted == [copy_sha, original.hash]


def test_a_start_or_cancel_before_the_tile_exists_is_kept(tmp_path, monkeypatch):
    """A small file can start (and be cancelled) inside upload_file,
    before the window has made its tile."""
    path = tmp_path / "Quick.png"
    path.write_bytes(PNG_BYTES)
    store = FakeStore()
    world = build(tmp_path, monkeypatch, store)
    store.upload_file = lambda p: (store.upload_started.emit("Quick.png"), "Quick.png")[1]
    world.dialog._add_paths([str(path)])
    assert world.model.tile("Quick.png").state == SENDING

    other = tmp_path / "Gone.png"
    other.write_bytes(PNG_BYTES)
    store.upload_file = lambda p: (store.upload_started.emit("Gone.png"),
                                   store.upload_cancelled.emit("Gone.png"), "Gone.png")[2]
    world.dialog._add_paths([str(other)])
    assert world.model.tile("Gone.png") is None


def test_the_smallest_window_cuts_no_toolbar_control(tmp_path, monkeypatch):
    """A longer translation widens the smallest window instead of
    cutting a button's or a pop-up's words."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(dialog_module, "ThumbnailLoader",
                        lambda parent=None: FakeLoader(cache, parent=parent))
    dialog = MediaLibraryDialog(store=FakeStore(), is_dark=False)
    dialog._add_button.setText("Hinzufügen zur Mediathek …")
    dialog._sort_combo.setItemText(0, "Neueste Objekte zuerst anzeigen")
    dialog.show()
    dialog.resize(100, 100)
    QCoreApplication.processEvents()
    for widget in (dialog._add_button, dialog._filter_combo, dialog._sort_combo,
                   dialog._actions_button):
        assert widget.width() >= widget.sizeHint().width(), widget


def test_a_file_the_editor_uploads_appears_in_an_open_library_at_once(tmp_path, monkeypatch):
    """A picture pasted into a document goes through the same store, so
    it is in an open library as soon as a server has it, with no
    listing in between. (Its progress shows in the document, not here.)"""
    from nostr.blossom.client import BlossomClient
    from nostr.blossom.settings import BlossomSettings
    from nostr.blossom.store import MediaStore
    from tests.blossom_fakes import (SERVER as BLOSSOM, FakeBlossomServer, FakeNam,
                                     FakeProfile, FakeSessionPool)
    settings = BlossomSettings(tmp_path / "blossom_servers.json")
    settings.set_custom_servers([BLOSSOM])
    nam = FakeNam(None, responder=FakeBlossomServer(BLOSSOM, require_auth=False))
    store = MediaStore(session_pool=FakeSessionPool(), profile_provider=FakeProfile,
                       settings=settings, client=BlossomClient(nam=nam))
    world = build(tmp_path, monkeypatch, store)
    nam.settle()                                   # the window's own listing: empty
    assert tiles(world) == []

    sha = sha_of(PNG_BYTES)
    store.upload_bytes(PNG_BYTES, name=f"asset:{sha}", mime_type="image/png")
    assert tiles(world) == []                      # its progress is the document's
    nam.settle()
    assert [tile.key for tile in tiles(world)] == [sha]
    assert len([c for c in nam.calls if c[0] == "get"]) == 1      # no second listing


def test_a_library_opened_later_shows_the_files_still_being_added(tmp_path, monkeypatch):
    """The store owns the uploads, not the window that started them: one
    that failed after its window closed is shown with its reason and
    Retry when the library opens again."""
    from nostr.blossom.store import AddedUpload
    path = tmp_path / "Holiday.png"
    path.write_bytes(PNG_BYTES)
    store = FakeStore()
    reason = "Couldn’t reach your media server. Check your internet connection."
    store.added = [AddedUpload(name="Holiday.png", mime="image/png", state="failed",
                               error=reason, path=str(path)),
                   AddedUpload(name="Clip.mp4", mime="video/mp4", state="sending", progress=40)]
    world = build(tmp_path, monkeypatch, store)
    failed, sending = world.model.tile("Holiday.png"), world.model.tile("Clip.mp4")
    assert (failed.state, failed.detail) == (FAILED, reason)
    assert failed.pixmap is not None
    assert (sending.state, sending.progress) == (SENDING, 40)
    world.grid.retry_requested.emit("Holiday.png")
    assert ("retry", "Holiday.png") in store.calls


def test_a_picker_does_not_offer_media_servers(tmp_path, monkeypatch):
    """Choosing an image is not where servers are managed, and the
    article cover's picker has nobody to open the sheet: the item would
    be dead there."""
    world = build(tmp_path, monkeypatch, FakeStore([media(1)]), pick=True)
    world.dialog._fill_actions_menu()
    texts = [a.text() for a in world.dialog._actions_menu.actions() if a.text()]
    assert "Media Servers…" not in texts and "Refresh" in texts
