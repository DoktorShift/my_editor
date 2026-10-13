# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins audio and video in the text (Insert > Audio or Video, a dropped or
pasted file: media_files.py, the asset manager's adopt_media, the editor's
media paragraphs, the window's card, writers, gate and close questions).

What must hold (and the checkpoint 4 review's H1, M1 to M3, L3 to L6):

  The file is an object of the text, as a picture is: an asset named by
  the hash of its bytes, in a paragraph of its own, shown as a card that
  says how its upload is going ("Uploading… 42 %"). Its upload goes to the
  media servers through the asset manager's queue; finishing it changes
  nothing in the document and no step of Undo, so Undo takes back the
  person's own last change, and Undo or Redo of the insertion brings
  back the file itself, never an orphan placeholder. The same file twice
  is one upload.

  It stays a paragraph of its own: typing in its paragraph goes into a new
  one after it; Return after it starts a clean paragraph; Backspace from
  the paragraph below takes it away whole, never joining text to it.

  Until it is on a media server it is written nowhere (Markdown, a note,
  plain text, an HTML file, the preview) and nothing is published or saved
  as a draft; asking to publish uploads it again after a failure. Once it
  is, it is written as its address (with its extension), on a line of its
  own, and described by imeta from what this app knows of it. A crash
  record keeps the object, never words standing for it.

  Closing a document or quitting while an upload runs asks first, with
  Cancel the default. A pasted audio or video file goes in as a dropped
  one, while an account is in use. Types come from one table, whatever
  the computer's says.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import media_files  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from nostr import preview  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

BODY = b"ID3" + b"sound" * 50
SHA = hashlib.sha256(BODY).hexdigest()
URL = f"https://cdn.example/{SHA}"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def test_types_come_from_one_table_whatever_the_computer_says():
    assert media_files.type_of("/x/Song.M4A") == "audio/mp4"
    assert media_files.type_of("clip.ogg") == "audio/ogg"
    assert media_files.type_of("film.mov") == "video/quicktime"
    assert media_files.type_of("https://cdn.example/a.flac?x=1") == "audio/flac"
    assert media_files.type_of("notes.txt") is None
    assert media_files.extension_for("audio/mpeg") == ".mp3"


def test_the_preview_names_the_file_by_its_own_name():
    url = f"{URL}.mp3"
    named = {url: "Interview with Bob.mp3"}.get
    assert f"[▶ Audio: Interview with Bob.mp3]({url})" in preview.markdown_for_preview(
        f"Listen:\n\n{url}\n", media_names=named)
    assert "▶ Audio: Interview with Bob.mp3" in preview.note_html(
        f"Listen {url}", media_names=named)


def test_the_preview_shows_where_readers_put_the_player():
    url = f"{URL}.mp3"
    markdown = preview.markdown_for_preview(f"Listen:\n\n{url}\n\nInline {url} stays.")
    assert f"[▶ Audio: {SHA}.mp3]({url})" in markdown
    assert f"Inline {url} stays." in markdown
    assert "▶ Video: clip.mp4" in preview.note_html("See https://cdn.example/clip.mp4")


def test_a_pasted_audio_file_goes_in_as_a_dropped_one_only_with_an_account(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(song))])
    data.setText("song.mp3")
    for account, expected in ((True, 1), (False, 0)):
        ed = HtmlEditor()
        ed.set_nostr_check(lambda: account)
        dropped = []
        ed.urls_dropped.connect(dropped.append)
        ed.insertFromMimeData(data)
        assert len(dropped) == expected
        assert ("song.mp3" in ed.toPlainText()) is (not account)


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, recovery
from doc_walk import serialize_plain_with_images
from markdown_writer import document_to_markdown
from export_html import document_to_html
from nostr.blossom.store import MediaFile
from nostr.publisher import build_imeta_tags

path, big, sha = sys.argv[2], sys.argv[3], sys.argv[4]
told, asked = [], []
main_window.inform = lambda parent, **kw: told.append(kw.get("title", ""))
main_window.confirm_destructive = lambda parent, **kw: (asked.append(kw), False)[1]
w = main_window.MainWindow()
w.show()
sent = []
w._media_store.upload_bytes = lambda body, name, mime_type: sent.append((name, mime_type, len(body)))
w.nostr_state._active = True
w._update_nostr_commands()
ed = w.new_tab()
ed.setFocus()
r = {}
QTest.keyClicks(ed, "Listen:")
w._upload_limit = lambda: 100
w._upload_into(ed, big)
r["too_large"] = told[-1] if told else ""
w._upload_limit = lambda: 10**9
w._upload_into(ed, path)
doc = ed.document()
blocks = []
b = doc.begin()
while b.isValid():
    blocks.append(b.text()); b = b.next()
r["blocks"] = blocks
r["media_block"] = ed.is_media_block(doc.findBlockByNumber(1))
r["sent"] = sent
w._upload_into(ed, path)                 # the same file again: one upload
r["sent_twice"] = len(sent)
ed.undo()
w._media_progress[sha] = 42
w._refresh_asset_in_documents(sha)
r["card_words"] = w._media_card(w._asset_manager.get(sha)).isNull() is False
# Typing in the media paragraph goes into a new one after it.
c = ed.textCursor(); c.setPosition(doc.findBlockByNumber(1).position() + 1); ed.setTextCursor(c)
QTest.keyClicks(ed, "Notes")
r["after_typing"] = [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]
# While it uploads it is written nowhere, and nothing is published.
r["md_pending"] = document_to_markdown(doc)
r["publish_pending"] = w._publish_text(ed, "markdown")
r["note_pending"] = w._publish_text(ed, "note")
r["plain_pending"] = serialize_plain_with_images(doc, lambda fmt: None)
r["html_pending"] = "Uploading" in document_to_html(
    doc, asset_resolver=w._asset_manager.export_view)
r["gate_pending"] = w._confirm_images_uploaded(ed)
r["gate_said"] = told[-1]
snapshot = doc.toHtml()
r["crash_keeps_object"] = f"myeditor-asset:{sha}" in snapshot and "Uploading" not in snapshot
# Closing while it uploads asks, Cancel the default (the stand-in answers Cancel).
w.close_tab(w.tabs.currentIndex())
r["close_asked"] = bool(asked) and asked[-1]["action"] == "Close Anyway"
r["still_open"] = w._editor_widget_index(ed) >= 0
# The upload finishes: nothing in the document changes, no step of Undo.
undo_steps = doc.availableUndoSteps()
media = MediaFile(hash=sha, url=f"https://cdn.example/{sha}", mime_type="audio/mpeg",
                  size=len(open(path, "rb").read()),
                  urls=[{"server": "https://cdn.example", "url": f"https://cdn.example/{sha}"}])
w._media_store.upload_status.emit(f"myeditor-asset:{sha}", "uploading")
w._media_store.upload_finished.emit(f"myeditor-asset:{sha}", media)
r["undo_steps_unchanged"] = doc.availableUndoSteps() == undo_steps
content, records = w._publish_payload(ed, "markdown")
r["published"] = content
r["imeta"] = build_imeta_tags(records, content)
r["gate_done"] = w._confirm_images_uploaded(ed)
# Undo takes back the person's typing, not the finished upload.
ed.undo()
r["after_undo"] = [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]
# Return after it starts a clean paragraph; Backspace from below takes it whole.
c = ed.textCursor(); c.setPosition(doc.findBlockByNumber(1).position() + 1); ed.setTextCursor(c)
QTest.keyClick(ed, Qt.Key.Key_Return)
QTest.keyClicks(ed, "Below")
r["return_then_type"] = document_to_markdown(doc)
c = ed.textCursor(); c.setPosition(doc.findBlockByNumber(2).position()); ed.setTextCursor(c)
QTest.keyClick(ed, Qt.Key.Key_Backspace)
r["backspace_from_below"] = [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]
ed.undo()
r["undo_backspace_brings_it_back"] = ed.is_media_block(doc.findBlockByNumber(1))
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w._media_not_uploaded = lambda doc: []
w.close()
print("RESULT " + json.dumps(r))
"""


def test_audio_in_the_window(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    big = tmp_path / "big.mp3"
    big.write_bytes(b"x" * 200)
    r = run_window_script(SCRIPT, tmp_path, str(song), str(big), SHA)
    assert r["too_large"] == "“big.mp3” is too large"
    assert r["blocks"] == ["Listen:", "￼", ""] and r["media_block"] is True
    assert r["sent"] == [[f"myeditor-asset:{SHA}", "audio/mpeg", len(BODY)]]
    assert r["sent_twice"] == 1                                         # M1: one upload
    assert r["card_words"] is True
    assert r["after_typing"] == ["Listen:", "￼", "Notes", ""]      # H1
    for written in ("md_pending", "publish_pending", "note_pending", "plain_pending"):
        assert SHA not in r[written] and "Uploading" not in r[written]  # M3
        assert "Notes" in r[written]
    assert r["html_pending"] is False and r["crash_keeps_object"] is True
    assert r["gate_pending"] is False and r["gate_said"] == "Audio or video is still uploading"
    assert r["close_asked"] is True and r["still_open"] is True         # L4
    assert r["undo_steps_unchanged"] is True                            # M2
    assert f"Listen:\n\n{URL}.mp3\n\nNotes" in r["published"]
    assert r["imeta"] == [["imeta", f"url {URL}.mp3", "m audio/mpeg", f"x {SHA}",
                           f"size {len(BODY)}"]]
    assert r["gate_done"] is True
    # Undo takes back typing, not the finished upload: the file stays.
    assert r["after_undo"][:2] == ["Listen:", "\ufffc"] and "Notes" not in r["after_undo"]
    assert "Below" in r["return_then_type"] and "￼" not in r["return_then_type"]
    assert r["backspace_from_below"][:2] == ["Listen:", "Below"]        # H1
    assert r["undo_backspace_brings_it_back"] is True


RMD_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.blossom.store import MediaFile

song, sha, folder = sys.argv[2], sys.argv[3], sys.argv[4]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
r = {}
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Before.")
w._upload_into(ed, song)
QTest.keyClicks(ed, "After.")
w._save_to(os.path.join(folder, "during.rmd"))
r["during"] = open(os.path.join(folder, "during.rmd"), encoding="utf-8").read()
r["copied"] = os.path.exists(os.path.join(folder, "during_media", sha + ".mp3"))
url = f"https://cdn.example/{sha}"
w._media_store.upload_status.emit(f"myeditor-asset:{sha}", "uploading")
w._media_store.upload_finished.emit(f"myeditor-asset:{sha}", MediaFile(
    hash=sha, url=url, mime_type="audio/mpeg", size=1,
    urls=[{"server": "https://cdn.example", "url": url}]))
app.processEvents()
w._save_to(os.path.join(folder, "after.rmd"))
r["after"] = open(os.path.join(folder, "after.rmd"), encoding="utf-8").read()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_r_markdown_writes_audio_as_a_link_during_and_after_the_upload(tmp_path):
    """Review N4: R Markdown links to the file, a copy beside the document
    while it uploads and its address once uploaded; never "image
    unavailable"."""
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(RMD_SCRIPT, tmp_path, str(song), SHA, str(tmp_path))
    assert f"Before.\n\n[song.mp3](during_media/{SHA}.mp3)\n\nAfter." in r["during"]
    assert r["copied"] is True
    assert f"Before.\n\n[song.mp3]({URL}.mp3)\n\nAfter." in r["after"]
    assert "unavailable" not in r["during"] + r["after"]


SAVED_DURING_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.blossom.store import MediaFile

song, sha, folder = sys.argv[2], sys.argv[3], sys.argv[4]
asked = []
main_window._ask_save_changes = lambda parent, title, **kw: (asked.append(title), "cancel")[1]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
r = {}
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Before.")
w._upload_into(ed, song)
QTest.keyClicks(ed, "After.")
path = os.path.join(folder, "doc.md")
w._save_to(path)
r["saved during"] = open(path, encoding="utf-8").read()
r["modified after save"] = ed.document().isModified()
r["said at save"] = w.status.currentMessage()
url = f"https://cdn.example/{sha}"
w._media_store.upload_status.emit(f"myeditor-asset:{sha}", "uploading")
w._media_store.upload_finished.emit(f"myeditor-asset:{sha}", MediaFile(
    hash=sha, url=url, mime_type="audio/mpeg", size=1,
    urls=[{"server": "https://cdn.example", "url": url}]))
app.processEvents()
r["modified after upload"] = ed.document().isModified()
r["said after upload"] = w.status.currentMessage()
w.close_tab(w.tabs.currentIndex())
r["close asked"] = len(asked)
w._save_to(path)
r["saved after"] = open(path, encoding="utf-8").read()
r["modified after second save"] = ed.document().isModified()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_document_saved_while_its_audio_uploads_is_edited_again_when_it_is_done(tmp_path):
    """Review N1, as Pages does when an inserted item finishes loading after
    a save: the save writes what it can, says the file goes in once
    uploaded, and the document is edited again when it is, so Close and
    Quit ask."""
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(SAVED_DURING_SCRIPT, tmp_path, str(song), SHA, str(tmp_path))
    assert r["saved during"] == "Before.\n\nAfter.\n" and SHA not in r["saved during"]
    assert r["modified after save"] is False
    assert "“song.mp3” goes into the file once it is uploaded" in r["said at save"]
    assert r["modified after upload"] is True
    assert r["said after upload"] == "“song.mp3” is uploaded. Save the document to put it in."
    assert r["close asked"] == 1
    assert f"Before.\n\n{URL}.mp3\n\nAfter." in r["saved after"]
    assert r["modified after second save"] is False


def test_an_html_file_links_to_the_uploaded_file_with_its_extension():
    import types
    from PySide6.QtGui import QTextDocument, QTextImageFormat
    from export_html import document_to_html
    doc = QTextDocument()
    cursor = doc.rootFrame().firstCursorPosition()
    image = QTextImageFormat()
    image.setName(f"myeditor-asset:{SHA}")
    image.setProperty(QTextImageFormat.ImageAltText, "song.mp3")
    cursor.insertImage(image)
    uploaded = types.SimpleNamespace(mime="audio/mpeg", remote_url=URL, data=b"")
    out = document_to_html(doc, asset_resolver=lambda name: uploaded)
    assert f'<a href="{URL}.mp3">▶ Audio: song.mp3</a>' in out


@pytest.mark.parametrize("uploaded", [True, False])
def test_a_pdf_or_a_print_shows_audio_as_its_link_never_a_grey_box(uploaded, tmp_path):
    """A PDF and a printed page link to an uploaded audio or video file,
    as the HTML export does, and leave out one not uploaded yet: never the
    grey stand-in for a picture that can't be read."""
    import types
    from PySide6.QtGui import QPdfWriter, QTextDocument, QTextImageFormat
    from export_pdf import paginate
    doc = QTextDocument()
    cursor = doc.rootFrame().firstCursorPosition()
    cursor.insertText("Before.")
    cursor.insertBlock()
    image = QTextImageFormat()
    image.setName(f"myeditor-asset:{SHA}")
    image.setProperty(QTextImageFormat.ImageAltText, "song.mp3")
    cursor.insertImage(image)
    cursor.insertBlock()
    cursor.insertText("After.")
    asset = types.SimpleNamespace(mime="audio/mpeg", remote_url=URL if uploaded else "",
                                  data=BODY)
    writer = QPdfWriter(str(tmp_path / "out.pdf"))
    paged = paginate(doc, writer, writer.resolution(), asset_resolver=lambda name: asset)
    text = paged.clone.toPlainText()
    assert "￼" not in text and "Before." in text and "After." in text
    if uploaded:
        found = paged.clone.find("▶ Audio: song.mp3")
        assert found.charFormat().anchorHref() == f"{URL}.mp3"
    else:
        assert "song.mp3" not in text
    assert doc.toPlainText().count("￼") == 1          # the document is untouched


PASTE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.blossom.store import MediaFile

song, sha = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
url = f"https://cdn.example/{sha}"
r = {}
source = w.new_tab()
source.setFocus()
w._upload_into(source, song)
w._media_store.upload_status.emit(f"myeditor-asset:{sha}", "uploading")
w._media_store.upload_finished.emit(f"myeditor-asset:{sha}", MediaFile(
    hash=sha, url=url, mime_type="audio/mpeg", size=1,
    urls=[{"server": "https://cdn.example", "url": url}]))
app.processEvents()
c = source.textCursor()
c.setPosition(0)
c.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
source.setTextCursor(c)
copied = source.createMimeDataFromSelection()

def blocks(ed):
    doc = ed.document()
    return [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]

for case, words, at in (("inside a word", "Head", 2), ("end of a paragraph", "Other doc", 9),
                        ("start of a paragraph", "Start", 0)):
    ed = w.new_tab()
    ed.setFocus()
    QTest.keyClicks(ed, words)
    c = ed.textCursor()
    c.setPosition(at)
    ed.setTextCursor(c)
    ed.insertFromMimeData(copied)
    r[case] = {"blocks": blocks(ed), "published": w._publish_text(ed, "markdown"),
               "note": w._publish_text(ed, "note")}
    ed.undo()
    r[case]["undone"] = blocks(ed)
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_pasted_or_dropped_file_is_a_paragraph_of_its_own_wherever_it_lands(tmp_path):
    """Review N2: pasted (or dragged, the same way in) inside a sentence,
    the file splits the paragraph and goes between its parts; its address
    is never glued to words."""
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(PASTE_SCRIPT, tmp_path, str(song), SHA)
    inside = r["inside a word"]
    assert inside["blocks"] == ["He", "￼", "ad"]
    assert inside["published"] == f"He\n\n{URL}.mp3\n\nad"
    assert inside["undone"] == ["Head"]
    end = r["end of a paragraph"]
    assert end["blocks"][:2] == ["Other doc", "￼"]
    assert end["published"].startswith(f"Other doc\n\n{URL}.mp3")
    assert end["undone"] == ["Other doc"]
    start = r["start of a paragraph"]
    assert start["blocks"] == ["\ufffc", "Start"]
    assert start["published"] == f"{URL}.mp3\n\nStart"


THEME_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.ui import media_card

song, sha = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
ed = w.new_tab()
w._upload_into(ed, song)

def fill():
    image = ed.document().resource(QTextDocument.ImageResource, QUrl(f"myeditor-asset:{sha}"))
    ratio = image.devicePixelRatio()
    return image.pixelColor(int((media_card.WIDTH - 8) * ratio),
                            int(media_card.HEIGHT / 2 * ratio)).lightness()

r = {}
w._set_theme(False)
r["light"] = fill()
w._set_theme(True)
r["dark"] = fill()
ed.document().setModified(False)
w._media_not_uploaded = lambda doc: []
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_card_follows_the_appearance(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(THEME_SCRIPT, tmp_path, str(song), SHA)
    assert r["light"] > 200 and r["dark"] < 80


def test_the_card_shows_what_the_file_is_never_a_control_that_does_nothing():
    """The card plays nothing (Qt Multimedia is not in the app), so it
    shows no play button: a glyph for audio or for video, the name and how
    the upload goes. It keeps a little room above and below, as a
    picture's paragraph does."""
    from nostr.ui import media_card
    audio = media_card.render("Audio · a.mp3", "", dark=False, mime="audio/mpeg")
    video = media_card.render("Audio · a.mp3", "", dark=False, mime="video/mp4")
    ratio = audio.devicePixelRatio()

    def glyph(image):
        return [image.pixelColor(int(x * ratio), int(y * ratio)).name()
                for x in range(16, 43, 2)
                for y in range(int(media_card.HEIGHT / 2) - 11, int(media_card.HEIGHT / 2) + 11, 2)]

    assert glyph(audio) != glyph(video)                       # what the file is
    middle = int(media_card.WIDTH / 2 * ratio)
    for image in (audio, video):
        assert image.pixelColor(middle, int(media_card.MARGIN / 2 * ratio)).alpha() == 0
        assert image.pixelColor(
            middle, int((media_card.HEIGHT - media_card.MARGIN / 2) * ratio)).alpha() == 0


BEFORE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

song = sys.argv[2]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Top")
w._upload_into(ed, song)
doc = ed.document()

def blocks():
    return [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]

def caret_before_object():
    c = ed.textCursor()
    c.setPosition(next(doc.findBlockByNumber(i).position() for i in range(doc.blockCount())
                       if ed.is_media_block(doc.findBlockByNumber(i))))
    ed.setTextCursor(c)

r = {}
caret_before_object()
QTest.keyClick(ed, Qt.Key.Key_Return)
r["return"] = blocks()
r["caret on the object"] = ed.is_media_block(ed.textCursor().block())
ed.undo()
r["undone"] = blocks()
caret_before_object()
QTest.keyClicks(ed, "Intro")
r["typed"] = blocks()
r["caret after the words"] = ed.textCursor().block().text() == "Intro"
ed.document().setModified(False)
w._media_not_uploaded = lambda doc: []
w.close()
print("RESULT " + json.dumps(r))
"""


def test_with_the_caret_before_the_file_return_and_typing_go_before_it(tmp_path):
    """As TextEdit and Pages do with any block: Return before it opens a
    paragraph above it, and typing there writes in a paragraph above."""
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(BEFORE_SCRIPT, tmp_path, str(song))
    assert r["return"] == ["Top", "", "￼", ""] and r["caret on the object"] is True
    assert r["undone"] == ["Top", "￼", ""]
    assert r["typed"] == ["Top", "Intro", "￼", ""] and r["caret after the words"] is True


FAILED_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

song, sha = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
ed = w.new_tab()
w._upload_into(ed, song)
r = {}
for code in ("UPLOAD_FAILED", "NETWORK_UNAVAILABLE", "PAYMENT_REQUIRED"):
    w._on_asset_upload_failed(sha, code)
    r[code] = w.status.currentMessage()
ed.document().setModified(False)
w._media_not_uploaded = lambda doc: []
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_failed_audio_upload_does_not_speak_of_an_image(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(FAILED_SCRIPT, tmp_path, str(song), SHA)
    for said in r.values():
        assert said and "image" not in said.lower()


PREVIEW_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.blossom.store import MediaFile

song, sha = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.nostr_state._active = True
w._update_nostr_commands()
w._upload_limit = lambda: 10**9
ed = w.new_tab()
w._upload_into(ed, song)
r = {"checks while uploading": list(w._preview_content(ed, "article").checks)}
url = f"https://cdn.example/{sha}"
w._media_store.upload_status.emit(f"myeditor-asset:{sha}", "uploading")
w._media_store.upload_finished.emit(f"myeditor-asset:{sha}", MediaFile(
    hash=sha, url=url, mime_type="audio/mpeg", size=1,
    urls=[{"server": "https://cdn.example", "url": url}]))
app.processEvents()
r["name"] = w._media_name(url + ".mp3")
w.nostr_state._active = False
r["document preview"] = w._preview_content(ed, "article").text
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_preview_shows_the_file_as_readers_do_and_says_while_it_uploads(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(BODY)
    r = run_window_script(PREVIEW_SCRIPT, tmp_path, str(song), SHA)
    assert any("still uploading" in check for check in r["checks while uploading"])
    assert r["name"] == "song.mp3"
    assert r["document preview"] == f"{URL}.mp3"
