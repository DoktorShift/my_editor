# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The viewer tabs (viewer_tabs.py, media_tabs.py) for every file that is
not a note, a text or a PDF.

The defect classes guarded here:
- the wrong tab for a file (or a viewer for a file another tab owns),
- a button that does not say what it asks for, or a control a screen
  reader cannot name,
- "Open in {app}" naming no app, or a menu without its place on screen,
- a lock file of Excel, Word or LibreOffice going unnoticed,
- a picture not fitted at first, zoom keys doing nothing, an SVG reading
  a local file, a picture past the pixel budget decoded,
- playback going on after the tab closed,
- the reading view loading anything but its own pictures, or leaving its
  temporary folder behind.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextDocument  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import file_kinds  # noqa: E402
import rmd_toolchain  # noqa: E402
import safe_picture  # noqa: E402
from media_tabs import ImageViewerTab, MediaPlayerTab  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.office_samples import (  # noqa: E402
    giant_png_header, make_docx, make_odt, make_package, make_zip, picture_bytes,
)
from tests.waiting import wait_until  # noqa: E402
from viewer_tabs import (  # noqa: E402
    DocumentReadingTab, FileInfoTab, FileViewerTab, PackagePreviewTab, viewer_for,
)

VIDEO = os.path.join(os.path.dirname(__file__), "fixtures", "media-preview.mp4")


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture(autouse=True)
def no_pandoc(monkeypatch):
    monkeypatch.setattr(rmd_toolchain, "find_pandoc", lambda: (None, "missing"))


@pytest.fixture
def tabs():
    """Tabs a test opened, released and deleted after it as the window does."""
    made = []
    yield made
    for tab in made:
        tab.release()
        tab.deleteLater()
    wait_until(lambda: False, 0.03)


def open_tab(tabs, path, *, is_dark=False):
    """A viewer for ``path``, shown, once it has read its file (pictures
    and preview pictures are read on a worker thread)."""
    tab = viewer_for(str(path), is_dark)
    assert tab is not None
    tabs.append(tab)
    tab.resize(900, 600)
    tab.show()
    assert wait_until(lambda: not tab.is_loading())
    return tab


def write_picture(path, width=800, height=500, color="#3A7"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return str(path)


def write_wav(path):
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 16000)
    with open(path, "wb") as stream:
        stream.write(output.getvalue())
    return str(path)


@pytest.fixture
def every_tab(tmp_path, tabs):
    """One of each viewer, for checks every one must pass."""
    package = make_zip(tmp_path / "Deck.odp", {"Thumbnails/thumbnail.png": picture_bytes(64, 48)})
    files = [write_picture(tmp_path / "Photo.png"), write_wav(tmp_path / "Sound.wav"),
             make_docx(tmp_path / "Report.docx"), package,
             make_zip(tmp_path / "Plain.pptx", {"x": "y"}),
             make_zip(tmp_path / "Archive.zip", {"a.txt": "a"})]
    shutil.copy(VIDEO, tmp_path / "Clip.mp4")
    files.append(str(tmp_path / "Clip.mp4"))
    return [open_tab(tabs, path) for path in files]


# --------------------------------------------------------------------------- #
# Which tab                                                                   #
# --------------------------------------------------------------------------- #

def test_each_kind_of_file_gets_its_own_viewer(tmp_path, tabs):
    cases = {
        write_picture(tmp_path / "a.png"): ImageViewerTab,
        write_wav(tmp_path / "a.wav"): MediaPlayerTab,
        make_docx(tmp_path / "a.docx"): DocumentReadingTab,
        make_odt(tmp_path / "a.odt"): DocumentReadingTab,
        make_zip(tmp_path / "a.pptx", {"x": "y"}): PackagePreviewTab,
        make_package(tmp_path / "a.numbers", {"preview.jpg": b"x"}): PackagePreviewTab,
        make_zip(tmp_path / "a.zip", {"x": "y"}): FileInfoTab,
    }
    for path, kind in cases.items():
        tab = open_tab(tabs, path)
        assert type(tab) is kind, path
        assert tab.load_ok and tab.load_error == ""
        assert tab.document_title() == os.path.basename(path)
        assert not tab.is_modified()


def test_files_other_tabs_own_get_no_viewer(tmp_path):
    for name in ("a.md", "a.txt", "a.pdf", "a.xlsx", "a.ods", "a.csv"):
        (tmp_path / name).write_bytes(b"")
        assert viewer_for(str(tmp_path / name), False) is None, name


def test_the_picture_and_media_tabs_are_file_viewer_tabs():
    assert issubclass(ImageViewerTab, FileViewerTab) and issubclass(MediaPlayerTab, FileViewerTab)


def test_a_picture_this_qt_cannot_decode_gets_the_info_card(tmp_path, monkeypatch, tabs):
    (tmp_path / "Shot.heic").write_bytes(b"\0" * 32)
    monkeypatch.setattr(safe_picture, "qt_reads", lambda fmt: False)
    assert type(open_tab(tabs, tmp_path / "Shot.heic")) is FileInfoTab


def test_a_missing_file_says_so(tmp_path, tabs):
    tab = open_tab(tabs, tmp_path / "Gone.zip")
    assert not tab.load_ok and tab.load_error == "The file could not be found."


# --------------------------------------------------------------------------- #
# Every tab                                                                   #
# --------------------------------------------------------------------------- #

def test_every_viewer_names_every_control(every_tab):
    for tab in every_tab:
        for app in ("Numbers", None):
            tab.set_default_app(app)
            assert unnamed_controls(tab) == [], type(tab).__name__


def test_every_viewer_follows_the_theme(every_tab):
    for tab in every_tab:
        tab.update_theme(True)
        assert "#1E1E1E" in tab.body.styleSheet()
        tab.update_theme(False)
        assert "#E8E8E8" in tab.body.styleSheet()


def test_the_open_button_names_the_app_and_its_menu_says_where_it_is(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Archive.zip", {"a": "b"}))
    opened, menus = [], []
    tab.open_requested.connect(lambda: opened.append(True))
    tab.open_with_requested.connect(menus.append)

    tab.set_default_app("Archive Utility")
    assert tab._open_btn.text() == "Open in Archive Utility"
    assert tab._card_open.text() == "Open in Archive Utility"
    assert tab._open_menu_btn.isVisible() and tab._card_open_menu.isVisible()
    tab._open_btn.click()
    tab._card_open.click()
    assert opened == [True, True] and menus == []
    tab._open_menu_btn.click()
    tab._card_open_menu.click()
    assert len(menus) == 2 and all(isinstance(point, QPoint) for point in menus)
    assert menus[0] == tab._open_menu_btn.mapToGlobal(QPoint(0, tab._open_menu_btn.height()))

    tab.set_default_app(None)
    assert tab._open_btn.text() == "Open With…"
    assert not tab._open_menu_btn.isVisible() and not tab._card_open_menu.isVisible()
    tab._open_btn.click()
    tab._card_open.click()
    assert opened == [True, True] and len(menus) == 4


def test_the_reveal_buttons_say_where_and_ask_for_it(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Archive.zip", {"a": "b"}))
    asked = []
    tab.reveal_requested.connect(lambda: asked.append(True))
    assert tab._card_reveal.text() == "Show in Finder"
    tab.set_reveal_label("Show in Explorer")
    assert tab._card_reveal.text() == "Show in Explorer"
    assert tab._reveal_btn.accessibleName() == tab._reveal_btn.toolTip() == "Show in Explorer"
    tab._reveal_btn.click()
    tab._card_reveal.click()
    assert asked == [True, True]


def test_the_info_card_tells_what_the_file_is(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Archive.zip", {"a": "b"}))
    assert tab._card_name.text() == "Archive.zip"
    assert tab._card_facts.text().startswith("ZIP Archive · ")
    assert " · Modified " in tab._card_facts.text()


def test_only_pictures_films_and_sound_offer_insert_and_only_documents_import(every_tab):
    for tab in every_tab:
        inserts = type(tab) in (ImageViewerTab, MediaPlayerTab)
        assert tab._insert_btn.isVisible() == inserts, type(tab).__name__
        assert tab._import_btn.isVisible() == (type(tab) is DocumentReadingTab)


def test_insert_and_import_ask_with_their_signals(tmp_path, tabs):
    picture = open_tab(tabs, write_picture(tmp_path / "a.png"))
    document = open_tab(tabs, make_docx(tmp_path / "a.docx"))
    asked = []
    picture.insert_into_note_requested.connect(lambda: asked.append("insert"))
    document.import_as_note_requested.connect(lambda: asked.append("import"))
    picture._insert_btn.click()
    document._import_btn.click()
    assert asked == ["insert", "import"]


# --------------------------------------------------------------------------- #
# Lock files                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name, lock", [
    ("Plan.odt", ".~lock.Plan.odt#"),
    ("Budget.xlsx", "~$Budget.xlsx"),
    ("Letter7.docx", "~$etter7.docx"),
    ("Quarterly.docx", "~$arterly.docx"),
])
def test_a_lock_file_of_another_app_is_noticed(tmp_path, name, lock):
    path = tmp_path / name
    path.write_bytes(b"")
    assert file_kinds.lock_owner_file(str(path)) is None
    (tmp_path / lock).write_bytes(b"")
    assert file_kinds.lock_owner_file(str(path)) == str(tmp_path / lock)


def test_the_lock_notice_shows_and_goes_with_the_lock_file(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Deck.pptx", {"x": "y"}))
    assert not tab._notice.isVisible()
    (tmp_path / ".~lock.Deck.pptx#").write_text("me", encoding="utf-8")
    tab.reload()
    assert tab._notice.isVisible()
    assert tab._notice_text.text() == ("This file is open in another app. "
                                       "You are seeing its last saved version.")
    os.remove(tmp_path / ".~lock.Deck.pptx#")
    tab.schedule_reload()
    assert wait_until(lambda: not tab._notice.isVisible())


# --------------------------------------------------------------------------- #
# Pictures                                                                    #
# --------------------------------------------------------------------------- #

def test_a_picture_opens_fitted_and_zooms(tmp_path, tabs):
    tab = open_tab(tabs, write_picture(tmp_path / "Wide.png", 4000, 2000))
    assert wait_until(lambda: tab.zoom() < 0.3)
    assert tab.is_fitted() and tab._fit_btn.isChecked()
    assert tab._facts_label.full_text().startswith("4000 × 2000 · ")
    fitted = tab.zoom()
    tab.zoom_in()
    assert tab.zoom() == pytest.approx(fitted * 1.25) and not tab.is_fitted()
    tab.zoom_out()
    tab.zoom_out()
    assert tab.zoom() == pytest.approx(fitted / 1.25)
    tab.actual_size()
    assert tab.zoom() == 1.0 and tab.canvas.width() == 4000
    assert tab._zoom_label.text() == "100%"
    tab.zoom_to_fit()
    assert tab.zoom() == pytest.approx(fitted)


def test_a_small_picture_is_never_enlarged_to_fit(tmp_path, tabs):
    tab = open_tab(tabs, write_picture(tmp_path / "Icon.png", 40, 30))
    wait_until(lambda: False, 0.05)
    assert tab.zoom() == 1.0


def test_the_zoom_keys_are_the_tabs_actions(tmp_path, tabs):
    tab = open_tab(tabs, write_picture(tmp_path / "a.png", 4000, 2000))
    words = [action.text() for action in tab.zoom_actions]
    assert words == ["Zoom In", "Zoom Out", "Actual Size", "Zoom to Fit"]
    assert all(action in tab.actions() for action in tab.zoom_actions)
    assert tab.zoom_actions[2].shortcut().toString() == "Ctrl+0"
    tab.zoom_actions[2].trigger()
    assert tab.zoom() == 1.0
    tab.zoom_actions[3].trigger()
    assert tab.is_fitted()


def test_a_picture_past_the_pixel_budget_is_not_decoded(tmp_path, tabs):
    (tmp_path / "Bomb.png").write_bytes(giant_png_header())
    tab = viewer_for(str(tmp_path / "Bomb.png"), False)
    tabs.append(tab)
    assert wait_until(lambda: not tab.is_loading())
    assert not tab.load_ok and tab.load_error == "This picture could not be shown."


def test_a_picture_is_read_off_the_window_thread(tmp_path, tabs, monkeypatch):
    import threading
    threads = []
    real = safe_picture.read_picture
    monkeypatch.setattr(safe_picture, "read_picture",
                        lambda path: threads.append(threading.current_thread()) or real(path))
    tab = viewer_for(write_picture(tmp_path / "a.png", 40, 30), False)
    tabs.append(tab)
    assert wait_until(lambda: not tab.is_loading())
    assert threads and threads[0] is not threading.main_thread()
    assert tab.load_ok and tab._pixel_size.width() == 40


def test_a_picture_read_again_shows_only_the_newest_reading(tmp_path, tabs):
    path = write_picture(tmp_path / "a.png", 40, 30)
    tab = open_tab(tabs, path)
    write_picture(path, 60, 20)
    tab.reload()
    tab._loaded(tab._generation - 1, QImage(5, 5, QImage.Format.Format_RGB32))
    assert tab._pixel_size.width() == 40            # a reading from before is dropped
    assert wait_until(lambda: not tab.is_loading())
    assert tab._pixel_size.width() == 60


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes")
def test_a_pipe_named_like_a_picture_or_a_deck_does_not_hold_the_window(tmp_path, tabs):
    os.mkfifo(tmp_path / "photo.png")
    os.mkfifo(tmp_path / "Slides.pptx")
    picture = open_tab(tabs, tmp_path / "photo.png")
    assert not picture.load_ok and picture.load_error == "This picture could not be shown."
    deck = open_tab(tabs, tmp_path / "Slides.pptx")
    assert not deck.has_preview


def test_a_picture_with_another_formats_bytes_is_not_decoded(tmp_path):
    (tmp_path / "fake.png").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert safe_picture.read_picture(str(tmp_path / "fake.png")) is None


def test_an_svg_is_drawn_without_reading_the_files_it_names(tmp_path, tabs):
    red = tmp_path / "red.png"
    write_picture(red, 10, 10, "#FF0000")
    svg = tmp_path / "Drawing.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="10" height="10"><rect width="10" height="10" fill="#0000FF"/>'
        f'<image width="10" height="10" xlink:href="{red}"/>'
        f'<image width="10" height="10" href="{red.as_uri()}"/></svg>', encoding="utf-8")
    image = safe_picture.read_picture(str(svg))
    assert image is not None
    center = image.pixelColor(image.width() // 2, image.height() // 2)
    assert (center.red(), center.blue()) == (0, 255)
    tab = open_tab(tabs, svg)
    assert type(tab) is ImageViewerTab and tab._pixel_size.width() == 10


# --------------------------------------------------------------------------- #
# Films and sound                                                             #
# --------------------------------------------------------------------------- #

def test_sound_plays_on_a_card_and_stops_when_the_tab_closes(tmp_path, tabs):
    tab = open_tab(tabs, write_wav(tmp_path / "Voice memo.wav"))
    assert tab.kind == "audio" and tab._card_name.text() == "Voice memo.wav"
    assert tab.player.display.isHidden() and tab.player.full_screen.isHidden()
    assert wait_until(tab.player.play_button.isEnabled)
    assert wait_until(lambda: tab._facts_label.full_text().startswith("0:02 · "))
    assert tab.player.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    tab.player.toggle_playback()
    assert wait_until(lambda: tab.player.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)
    tab.release()
    assert tab.player.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert tab.player.player.source().isEmpty()


def test_a_film_shows_in_the_player_with_full_screen(tmp_path, tabs):
    shutil.copy(VIDEO, tmp_path / "Clip.mp4")
    tab = open_tab(tabs, tmp_path / "Clip.mp4")
    assert tab.kind == "video" and not tab.player.full_screen.isHidden()
    assert wait_until(tab.player.play_button.isEnabled)


def test_a_film_this_computer_cannot_play_says_so_calmly(tmp_path, tabs):
    (tmp_path / "Broken.mp4").write_bytes(b"not a film")
    tab = open_tab(tabs, tmp_path / "Broken.mp4")
    tab.player.failed.emit("x")
    assert tab._message.isVisible() and "can’t be played" in tab._message.text()


# --------------------------------------------------------------------------- #
# Reading view                                                                #
# --------------------------------------------------------------------------- #

def test_a_word_document_is_read_with_its_table_and_offers_pandoc(tmp_path, tabs):
    tab = open_tab(tabs, make_docx(tmp_path / "Report.docx", picture=picture_bytes()))
    assert wait_until(lambda: "Coffee" in tab.browser.toPlainText())
    assert "Meeting notes" in tab.browser.toPlainText()
    assert tab.pandoc_offered
    asked = []
    tab.pandoc_install_requested.connect(lambda: asked.append(True))
    tab._pandoc_btn.click()
    assert asked == [True]
    pictures = [name for folder, _d, names in os.walk(tab._temp_root) for name in names]
    assert pictures == ["picture-1.png"]


def test_the_pandoc_offer_stays_away_when_pandoc_is_there(tmp_path, monkeypatch, tabs):
    import office_preview
    monkeypatch.setattr(office_preview, "render_document",
                        lambda path, media_dir=None: office_preview.RenderedDocument(
                            "<p>by pandoc</p>", True, False))
    tab = open_tab(tabs, make_docx(tmp_path / "a.docx"))
    assert wait_until(lambda: "by pandoc" in tab.browser.toPlainText())
    assert not tab.pandoc_offered


def test_an_unreadable_document_shows_a_calm_sentence(tmp_path, tabs):
    (tmp_path / "Broken.docx").write_bytes(b"not a zip")
    tab = open_tab(tabs, tmp_path / "Broken.docx")
    assert tab.load_ok
    assert wait_until(lambda: "could not be opened" in tab.browser.toPlainText())


def test_the_reading_view_loads_only_its_own_pictures(tmp_path, tabs):
    tab = open_tab(tabs, make_odt(tmp_path / "a.odt"))
    browser = tab.browser
    image = QTextDocument.ResourceType.ImageResource
    outside = write_picture(tmp_path / "outside.png", 10, 10)
    inside = write_picture(os.path.join(tab._temp_root, "inside.png"), 10, 10)
    assert browser.loadResource(image, QUrl.fromLocalFile(outside)).isNull()
    assert browser.loadResource(image, QUrl(outside)).isNull()
    assert browser.loadResource(image, QUrl("https://example.org/a.png")).isNull()
    assert not browser.loadResource(image, QUrl.fromLocalFile(inside)).isNull()
    import base64
    data = "data:image/png;base64," + base64.b64encode(picture_bytes(8, 8)).decode()
    assert browser.loadResource(image, QUrl(data)).width() == 8
    svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    svg = "data:image/png;base64," + base64.b64encode(svg_bytes).decode()
    assert browser.loadResource(image, QUrl(svg)).isNull()
    assert browser.loadResource(QTextDocument.ResourceType.StyleSheetResource,
                                QUrl.fromLocalFile(outside)) == ""


def test_closing_the_reading_view_removes_its_temporary_folder(tmp_path, tabs):
    tab = open_tab(tabs, make_docx(tmp_path / "a.docx", picture=picture_bytes()))
    assert wait_until(lambda: "Coffee" in tab.browser.toPlainText())
    folder = tab._temp_root
    assert os.path.isdir(folder)
    tab.release()
    assert not os.path.exists(folder)


def test_web_links_open_outside_and_nothing_else_does(tmp_path, monkeypatch, tabs):
    import viewer_tabs as module
    opened = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url))
    tab = open_tab(tabs, make_docx(tmp_path / "a.docx"))
    tab.browser.anchorClicked.emit(QUrl("https://example.org/agenda"))
    tab.browser.anchorClicked.emit(QUrl("file:///etc/passwd"))
    tab.browser.anchorClicked.emit(QUrl("javascript:alert(1)"))
    assert [url.toString() for url in opened] == ["https://example.org/agenda"]


# --------------------------------------------------------------------------- #
# Preview pictures                                                            #
# --------------------------------------------------------------------------- #

def test_a_presentation_shows_its_first_slide_with_a_caption(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Deck.odp",
                                  {"Thumbnails/thumbnail.png": picture_bytes(64, 48)}))
    assert tab.has_preview and tab._pages.currentWidget() is tab._preview_page
    assert tab._caption.text().startswith("The first slide")


def test_a_preview_picture_is_read_off_the_window_thread(tmp_path, tabs, monkeypatch):
    import threading
    import office_preview
    threads = []
    real = office_preview.package_thumbnail
    monkeypatch.setattr(office_preview, "package_thumbnail",
                        lambda path: threads.append(threading.current_thread()) or real(path))
    tab = open_tab(tabs, make_zip(tmp_path / "Deck.odp",
                                  {"Thumbnails/thumbnail.png": picture_bytes(64, 48)}))
    assert tab.has_preview
    assert threads and threads[0] is not threading.main_thread()


def test_a_file_without_a_preview_falls_back_to_the_card(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "Plain.pptx", {"x": "y"}))
    assert not tab.has_preview and tab._pages.currentWidget() is not tab._preview_page
    assert tab._card_name.text() == "Plain.pptx"


def test_reload_is_debounced(tmp_path, tabs):
    tab = open_tab(tabs, make_zip(tmp_path / "a.zip", {"a": "b"}))
    calls = []
    tab.reload = lambda: calls.append(True)
    tab.schedule_reload()
    tab.schedule_reload()
    wait_until(lambda: bool(calls), 1.0)
    wait_until(lambda: False, 0.1)
    assert calls == [True]
