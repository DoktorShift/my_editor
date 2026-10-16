# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Library file kinds, verified sources, native playback and preview lifetime."""

import ast
import hashlib
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPdfWriter, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import media_files
from nostr.blossom.store import MediaFile
from nostr.media.filecrypto import encrypt_file
from nostr.media.media_visibility import MediaVisibility
from nostr.media.visibility import PrivateBlob
from nostr.ui import media_source
from nostr.ui.file_preview import FilePreview, TEXT_PREVIEW_BYTES
from nostr.ui import file_icons
from nostr.ui.media_player import MediaPlayer, time_label
from nostr.ui.media_picture import frame_pixmap
from nostr.ui.media_library_dialog import MediaLibraryDialog
from nostr.ui.media_preview import QuickLook
from nostr.ui.media_source import PreviewResolver, PreviewSource, THUMBNAIL_DOWNLOAD_LIMIT
from nostr.ui.media_thumbnails import MediaThumbnails
from tests.test_media_library import FakeLibrary, FakeLoader, FakeStore, build, media, select, tiles
from tests.test_media_save import wait_for

# A synthetic two-second, silent 64×48 H.264 clip generated with ffmpeg's
# color source. The tests need no ffmpeg executable or external media.
VIDEO = Path(__file__).parent / "fixtures" / "media-preview.mp4"
KEY = "5c" * 32


@pytest.fixture(autouse=True)
def close_preview_windows():
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, (MediaLibraryDialog, QuickLook)) and widget.isVisible():
            widget.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def record(body, mime="video/mp4"):
    sha = hashlib.sha256(body).hexdigest()
    return MediaFile(hash=sha, url=f"https://one.example/{sha}", size=len(body), mime_type=mime)


@pytest.fixture
def inline_worker(monkeypatch):
    def run(fn, done, failed, **_kwargs):
        try:
            result = fn()
        except Exception as error:
            failed(error)
        else:
            done(result)
    monkeypatch.setattr(media_source, "run_blocking", run)


@pytest.mark.parametrize("name,label", [
    ("report.pdf", "PDF Document"), ("notes.md", "Text File"), ("analysis.Rmd", "Text File"),
    ("letter.docx", "Text Document"), ("table.xlsx", "Spreadsheet"),
    ("table.csv", "Spreadsheet"), ("slides.pptx", "Presentation"),
    ("bundle.zip", "Archive"), ("book.epub", "EPUB Book"),
    ("report.pages", "Text Document"), ("table.numbers", "Spreadsheet"),
    ("slides.key", "Presentation"),
])
def test_common_files_have_stable_types_and_human_labels(name, label):
    mime = media_files.type_of(name.upper())
    assert mime and media_files.library_kind(mime) == "file"
    assert media_files.file_type_label(mime) == label
    assert not media_files.is_audio_or_video(name)


def test_library_accepts_every_format_the_editor_opens_or_saves():
    # Read the editor's advertised contracts without importing its main window.
    tree = ast.parse((Path(__file__).parents[1] / "main_window.py").read_text(encoding="utf-8"))
    formats = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in ("_SUPPORTED_EXTS", "_SAVE_EXTS")
                for target in node.targets):
            formats.update(ast.literal_eval(node.value))
    assert formats
    assert all(media_files.type_of("document" + ext) for ext in formats)


def test_file_icons_use_cached_system_associations_without_user_paths(tmp_path, monkeypatch):
    calls = []
    picture = QPixmap(32, 32)
    picture.fill(QColor("blue"))
    native_icon = QIcon(picture)

    class Provider:
        def icon(self, info):
            calls.append(info.filePath())
            assert info.exists() and info.size() == 0
            return native_icon if info.suffix() else QIcon()

    monkeypatch.setattr(file_icons, "QFileIconProvider", Provider)
    icons = file_icons.FileIcons()
    mime = media_files.type_of("Budget.xlsx")
    assert icons.icon(str(tmp_path / "Private Budget.xlsx"), mime).cacheKey() == native_icon.cacheKey()
    assert icons.icon("Another.xlsx", mime).cacheKey() == native_icon.cacheKey()
    assert [Path(path).name for path in calls] == ["file", "file.xlsx"]
    assert all(Path(path).parent == Path(icons._directory.path()) for path in calls)
    assert list(tmp_path.iterdir()) == []
    directory = Path(icons._directory.path())
    del icons
    assert not directory.exists()


@pytest.mark.parametrize("name,glyph", [
    ("Note.Rmd", "text"), ("Letter.odt", "document"), ("Table.ods", "spreadsheet"),
    ("Slides.pptx", "presentation"), ("Backup.7z", "archive"), ("Novel.epub", "book"),
])
def test_file_preview_keeps_recognizable_fallback_when_system_icon_is_missing(name, glyph, monkeypatch):
    monkeypatch.setattr(file_icons.FileIcons, "icon", lambda *_args: QIcon())
    grid = QWidget()
    preview = QuickLook(grid=grid, is_dark=False)
    mime = media_files.type_of(name)
    preview.show_file(name=name, detail="", url="", glyph=media_files.file_glyph(mime),
                      picture=None, mime=mime)
    assert preview.picture.icon.isNull()
    assert preview.picture.glyph == glyph
    assert not preview.picture.grab().isNull()
    preview.close()
    preview.deleteLater()
    grid.deleteLater()


def test_files_filter_includes_pdf_text_office_and_unknown_files(tmp_path, monkeypatch):
    records = [record(b"pdf", "application/pdf"), record(b"text", "text/plain"),
               record(b"sheet", media_files.type_of("a.xlsx")),
               record(b"unknown", "application/octet-stream"), record(b"image", "image/png")]
    world = build(tmp_path, monkeypatch, FakeStore(records))
    combo = world.dialog._filter_combo
    assert combo.itemData(1) == "image"     # existing cover pickers keep their index
    combo.setCurrentIndex(combo.findData("file"))
    assert {tile.key for tile in tiles(world)} == {item.hash for item in records[:-1]}
    assert all(tile.detail for tile in tiles(world))


def test_common_files_can_be_added_by_drop(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    for name in ("notes.txt", "analysis.Rmd", "sheet.xlsx", "slides.odp", "bundle.zip"):
        path = tmp_path / name
        path.write_bytes(b"file")
        world.dialog._add_paths([str(path)])
        assert ("upload_file", name) in world.store.calls


def test_file_picker_includes_both_r_markdown_casings(tmp_path, monkeypatch):
    from nostr.ui import media_library_dialog
    path = tmp_path / "Analysis.Rmd"
    path.write_text("# Analysis\n", encoding="utf-8")
    world = build(tmp_path, monkeypatch)

    def choose(_parent, _title, _directory, filters):
        assert "*.rmd" in filters and "*.Rmd" in filters
        return [str(path)], ""

    monkeypatch.setattr(media_library_dialog.QFileDialog, "getOpenFileNames", choose)
    world.dialog._choose_files()
    assert ("upload_file", "Analysis.Rmd") in world.store.calls


def test_missing_server_type_uses_remembered_file_name(tmp_path, monkeypatch):
    media = record(b"spreadsheet", "application/octet-stream")
    world = build(tmp_path, monkeypatch, FakeStore([media], names={media.hash: "Budget.xlsx"}))
    [tile] = tiles(world)
    assert tile.mime == media_files.type_of("Budget.xlsx")
    assert "Spreadsheet" in tile.detail


def test_cached_public_source_is_verified_before_it_reaches_a_viewer(tmp_path, inline_worker):
    body = b"the real file"
    media = record(body)
    loader = FakeLoader(tmp_path)
    loader.cache_path(media.hash).write_bytes(b"substituted")
    resolver = PreviewResolver(loader, MediaVisibility())
    ready, errors = [], []
    resolver.ready.connect(ready.append)
    resolver.failed.connect(errors.append)
    resolver.resolve(media)
    assert ready == [] and len(loader.downloads) == 1
    loader.downloads[0].finished.emit("wrong_file")
    assert errors and ready == []


def test_private_source_stays_in_memory_and_cache_stays_encrypted(tmp_path, inline_worker):
    envelope = encrypt_file(b"private movie", key_hex=KEY).envelope
    media = record(envelope, "application/octet-stream")
    blob = PrivateBlob(sha256=media.hash, key_hex=KEY, mime="video/mp4", name="Clip.mp4")
    visibility = MediaVisibility(library=FakeLibrary([blob]))
    loader = FakeLoader(tmp_path)
    cached = loader.cache_path(media.hash)
    cached.write_bytes(envelope)
    resolver = PreviewResolver(loader, visibility)
    sources = []
    resolver.ready.connect(sources.append)
    resolver.resolve(media)
    assert sources == [PreviewSource(data=b"private movie")]
    assert cached.read_bytes() == envelope
    assert list(tmp_path.iterdir()) == [cached]


def test_unknown_privacy_prevents_preview_download(tmp_path):
    loader = FakeLoader(tmp_path)
    resolver = PreviewResolver(loader, MediaVisibility(library=FakeLibrary(vouches=False)))
    errors = []
    resolver.failed.connect(errors.append)
    resolver.resolve(record(b"unknown"))
    assert errors and loader.downloads == []


def test_thumbnail_budget_does_not_download_a_large_movie(tmp_path):
    loader = FakeLoader(tmp_path)
    resolver = PreviewResolver(loader, MediaVisibility())
    media = record(b"movie")
    media.size = THUMBNAIL_DOWNLOAD_LIMIT + 1
    resolver.resolve(media, max_bytes=THUMBNAIL_DOWNLOAD_LIMIT)
    assert loader.downloads == []


def test_corrupt_large_cached_movie_does_not_bypass_thumbnail_download_budget(tmp_path, inline_worker):
    loader = FakeLoader(tmp_path)
    media = record(b"real movie")
    media.size = THUMBNAIL_DOWNLOAD_LIMIT + 1
    loader.cache_path(media.hash).write_bytes(b"corrupt")
    previews = MediaThumbnails(loader, MediaVisibility())
    previews.request(media, "video/mp4")
    assert loader.downloads == []
    previews.cancel()
    previews.deleteLater()


def test_wrong_private_key_never_retries_another_server(tmp_path, inline_worker):
    envelope = encrypt_file(b"secret", key_hex=KEY).envelope
    media = record(envelope)
    media.urls = [{"url": "https://one.example/secret"}, {"url": "https://two.example/secret"}]
    blob = PrivateBlob(sha256=media.hash, key_hex="aa" * 32, mime="video/mp4")
    loader = FakeLoader(tmp_path)
    loader.cache_path(media.hash).write_bytes(envelope)
    resolver = PreviewResolver(loader, MediaVisibility(library=FakeLibrary([blob])))
    errors = []
    resolver.failed.connect(errors.append)
    resolver.resolve(media)
    assert errors and loader.downloads == []
    assert KEY not in errors[0] and blob.key_hex not in errors[0]


def test_selection_change_cancels_download_and_discards_late_completion(tmp_path):
    loader = FakeLoader(tmp_path)
    resolver = PreviewResolver(loader, MediaVisibility())
    ready = []
    resolver.ready.connect(ready.append)
    resolver.resolve(record(b"first"))
    first = loader.downloads[-1]
    resolver.resolve(record(b"second"))
    assert first.cancelled
    first.finished.emit("")
    assert ready == []
    resolver.cancel()
    assert loader.downloads[-1].cancelled


def test_late_worker_result_cannot_replace_new_selection(tmp_path, monkeypatch):
    pending = []
    monkeypatch.setattr(media_source, "run_blocking", lambda fn, done, failed, **kw: pending.append((fn, done)))
    loader = FakeLoader(tmp_path)
    first, second = record(b"first"), record(b"second")
    for media, body in ((first, b"first"), (second, b"second")):
        loader.cache_path(media.hash).write_bytes(body)
    resolver = PreviewResolver(loader, MediaVisibility())
    ready = []
    resolver.ready.connect(ready.append)
    resolver.resolve(first)
    resolver.resolve(second)
    pending[0][1](pending[0][0]())
    assert ready == []
    pending[1][1](pending[1][0]())
    assert ready == [PreviewSource(path=loader.cache_path(second.hash))]


def test_preview_download_tries_another_mirror(tmp_path, inline_worker):
    loader = FakeLoader(tmp_path)
    body = b"movie"
    media = record(body)
    media.urls = [{"url": "https://one.example/clip"}, {"url": "https://two.example/clip"}]
    resolver = PreviewResolver(loader, MediaVisibility())
    ready = []
    resolver.ready.connect(ready.append)
    resolver.resolve(media)
    loader.downloads[-1].finished.emit("network")
    assert loader.downloads[-1].url == media.urls[1]["url"]
    loader.cache_path(media.hash).write_bytes(body)
    loader.downloads[-1].finished.emit("")
    assert ready == [PreviewSource(path=loader.cache_path(media.hash))]


def test_html_preview_is_inert_plain_text_and_large_text_is_bounded():
    view = FilePreview()
    try:
        view.show_source(PreviewSource(data=b"<script>alert(1)</script>"), "text/html")
        assert view.text.toPlainText() == "<script>alert(1)</script>"
        view.show_source(PreviewSource(data=b"a" * (TEXT_PREVIEW_BYTES + 10)), "text/plain")
        assert len(view.text.toPlainText()) == TEXT_PREVIEW_BYTES
        assert not view.note.isHidden()
        view.clear()
        assert view.text.toPlainText() == ""
    finally:
        view.deleteLater()


def pdf_bytes():
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.ReadWrite)
    writer = QPdfWriter(buffer)
    painter = QPainter(writer)
    painter.drawText(100, 100, "First page")
    writer.newPage()
    painter.drawText(100, 100, "Second page")
    painter.end()
    del writer
    return bytes(buffer.data())


def test_private_pdf_opens_from_memory_and_pages_work():
    view = FilePreview()
    try:
        view.show_source(PreviewSource(data=pdf_bytes()), "application/pdf")
        assert wait_for(lambda: view.document.pageCount() == 2)
        assert view.page_label.text() == "Page 1 of 2"
        view.next.click()
        assert view.page_label.text() == "Page 2 of 2"
        assert not view.next.isEnabled() and view.previous.isEnabled()
        view.clear()
        assert view.document.pageCount() == 0 and view._buffer is None
    finally:
        view.deleteLater()


@pytest.mark.parametrize("private", [False, True])
def test_real_video_poster_comes_from_verified_local_bytes(tmp_path, inline_worker, private):
    body = VIDEO.read_bytes()
    envelope = encrypt_file(body, key_hex=KEY).envelope if private else body
    media = record(envelope)
    loader = FakeLoader(tmp_path)
    loader.cache_path(media.hash).write_bytes(envelope)
    library = FakeLibrary([PrivateBlob(sha256=media.hash, key_hex=KEY, mime="video/mp4")]) if private else None
    previews = MediaThumbnails(loader, MediaVisibility(library=library))
    ready = []
    previews.ready.connect(lambda sha, pixmap: ready.append((sha, pixmap)))
    try:
        previews.request(media, "video/mp4")
        assert wait_for(lambda: ready, timeout=10)
        assert ready[0][0] == media.hash and not ready[0][1].isNull()
        assert loader.cache_path(media.hash).read_bytes() == envelope
        assert previews._player is None  # the silent decoder is released after its frame
    finally:
        previews.cancel()
        previews.deleteLater()


def test_initial_library_layout_loads_video_and_pdf_thumbnails_without_interaction(tmp_path, monkeypatch):
    body, pdf = VIDEO.read_bytes(), pdf_bytes()
    video, document = record(body), record(pdf, "application/pdf")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / video.hash).write_bytes(body)
    (cache / document.hash).write_bytes(pdf)
    world = build(tmp_path, monkeypatch, FakeStore([video, document]))
    assert wait_for(lambda: len(world.dialog._thumbs) == 2)
    assert world.loader.downloads == []
    page = world.dialog._thumbs[document.hash]
    assert page.height() > page.width()  # the full portrait page stays in proportion


def test_scrolling_requests_new_video_thumbnails_without_manual_loading(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch,
                  FakeStore([media(n, mime="video/mp4") for n in range(200)]), size=(560, 420))
    assert wait_for(lambda: world.dialog._preview_asked)
    first_screen = len(world.dialog._preview_asked)
    world.grid.verticalScrollBar().setValue(world.grid.verticalScrollBar().maximum())
    assert wait_for(lambda: len(world.dialog._preview_asked) > first_screen)


def test_changed_private_library_discards_decrypted_thumbnail_pixels(tmp_path, monkeypatch):
    from PySide6.QtGui import QPixmap
    media = record(b"public")
    world = build(tmp_path, monkeypatch, FakeStore([media]))
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.GlobalColor.blue)
    world.dialog._set_thumbnail(media.hash, pixmap)
    assert media.hash in world.dialog._thumbs
    world.dialog._on_private_library_changed()
    assert media.hash not in world.dialog._thumbs


def test_audio_uses_same_native_transport_without_video_controls():
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 8000)
    player = MediaPlayer()
    try:
        player.set_source(PreviewSource(data=output.getvalue()), "audio/wav")
        assert wait_for(player.play_button.isEnabled)
        assert player.full_screen.isHidden()
        assert player.volume.isEnabled() and player.mute_button.isEnabled()
        assert player.player.duration() == 1000
        assert player.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    finally:
        player.clear()
        player.deleteLater()


def test_stop_loading_is_cancellable_and_retry_keeps_the_selection(tmp_path):
    loader = FakeLoader(tmp_path)
    resolver = PreviewResolver(loader, MediaVisibility())
    grid = QWidget()
    look = QuickLook(grid=grid, is_dark=False, resolver=resolver)
    media = record(b"movie")
    try:
        look.show_file(name="Clip.mp4", detail="Video", url=media.url, glyph="video",
                       picture=None, media=media, mime="video/mp4")
        assert look.retry_button.text() == "Stop"
        look.retry_button.click()
        assert loader.downloads[0].cancelled and look._key == media.hash
        assert look.retry_button.text() == "Try Again"
        look.retry_button.click()
        assert len(loader.downloads) == 2 and look._loading
    finally:
        look.close()
        look.deleteLater()
        grid.deleteLater()


def test_player_does_not_autoplay_and_releases_private_buffer():
    player = MediaPlayer()
    try:
        player.set_source(PreviewSource(data=VIDEO.read_bytes()), "video/mp4")
        assert wait_for(player.play_button.isEnabled)
        assert player.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        player.toggle_playback()
        assert wait_for(lambda: player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        player.toggle_playback()
        assert player.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        player.clear()
        assert player._buffer is None and player.player.source().isEmpty()
        assert player.picture.pixmap is None and not player.video.videoSink().videoFrame().isValid()
    finally:
        player.deleteLater()


def test_timeline_arrow_keys_seek_a_visible_distance():
    player = MediaPlayer()
    try:
        player.set_source(PreviewSource(data=VIDEO.read_bytes()), "video/mp4")
        assert wait_for(player.play_button.isEnabled)
        QTest.keyClick(player.seek, Qt.Key.Key_Right)
        assert wait_for(lambda: player.player.position() >= 1000)
        QTest.keyClick(player.seek, Qt.Key.Key_Left)
        assert wait_for(lambda: player.player.position() == 0)
    finally:
        player.clear()
        player.deleteLater()


def test_space_controls_video_and_escape_closes_quick_look(tmp_path, inline_worker):
    loader = FakeLoader(tmp_path)
    media = record(VIDEO.read_bytes())
    loader.cache_path(media.hash).write_bytes(VIDEO.read_bytes())
    grid = QWidget()
    resolver = PreviewResolver(loader, MediaVisibility())
    look = QuickLook(grid=grid, is_dark=False, resolver=resolver)
    try:
        look.show_file(name="Clip.mp4", detail="Video", url=media.url, glyph="video",
                       picture=None, media=media, mime="video/mp4")
        look.show()
        assert wait_for(look._player.play_button.isEnabled)
        QTest.keyClick(look._player.play_button, Qt.Key.Key_Space)
        assert look.isVisible()
        assert wait_for(lambda: look._player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        look.show_file(name="Renamed.mp4", detail="Video", url=media.url, glyph="video",
                       picture=None, media=media, mime="video/mp4")
        assert look._player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        QTest.keyClick(look._player.seek, Qt.Key.Key_Escape)
        assert not look.isVisible() and look._player.player.source().isEmpty()
    finally:
        look.close()
        look.deleteLater()
        grid.deleteLater()


def test_file_preview_save_action_uses_existing_library_saver(tmp_path, monkeypatch):
    from nostr.ui import media_library_dialog
    picture = QPixmap(32, 32)
    picture.fill(QColor("blue"))
    icon = QIcon(picture)
    monkeypatch.setattr(file_icons.FileIcons, "icon", lambda *_args: icon)
    media = record(b"office", media_files.type_of("report.docx"))
    world = build(tmp_path, monkeypatch, FakeStore([media], names={media.hash: "Report.docx"}))
    select(world, media.hash)
    world.dialog._toggle_quick_look()
    look = world.dialog._quick_look
    assert look.save_button.isVisible() and look._file_preview is None
    assert "open it in an app" in look.message.text()
    assert look.picture.icon.cacheKey() == icon.cacheKey()
    target = tmp_path / "Report.docx"
    monkeypatch.setattr(media_library_dialog.QFileDialog, "getSaveFileName",
                        lambda *args, **kwargs: (str(target), ""))
    look.save_button.click()
    assert world.loader.downloads[-1].sha == media.hash
    assert Path(world.loader.downloads[-1].target) == target
    look.close()
    assert look.picture.icon is None


@pytest.mark.parametrize("milliseconds,expected", [(0, "0:00"), (61000, "1:01"), (3661000, "1:01:01")])
def test_player_time_labels(milliseconds, expected):
    assert time_label(milliseconds) == expected


def test_video_stills_apply_rotation_and_mirroring():
    from PySide6.QtGui import QImage
    from PySide6.QtMultimedia import QVideoFrame, QtVideo
    image = QImage(4, 2, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    image.setPixelColor(0, 0, Qt.GlobalColor.red)
    frame = QVideoFrame(image)
    frame.setRotation(QtVideo.Rotation.Clockwise90)
    frame.setMirrored(True)
    actual = frame_pixmap(frame).toImage()
    assert (actual.width(), actual.height()) == (2, 4)
    assert actual.pixelColor(0, 0) == image.pixelColor(0, 0)
    assert actual.pixelColor(1, 0) == image.pixelColor(0, 1)
