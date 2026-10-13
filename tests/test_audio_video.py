# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins audio and video in the text (Insert > Audio or Video, a dropped
file: rich_text's upload placeholders, the window's upload through the
media store, the writers, imeta, the preview).

What must hold:

  A file goes to the media servers the Media Library's own way, and a
  paragraph of its own stands for it meanwhile ("Uploading name…"), one
  step to undo. Undone or removed, the upload ends in the media library
  and nothing is put in its place. Done, the paragraph becomes the file's
  address (with its extension, so readers can tell what it is), where
  readers show a player; failed, it says so, and the person chooses Try
  Again or Remove. A file larger than every media server takes is refused
  at once, saying the limit.

  A placeholder is never saved or published as text: the writers leave it
  out, and nothing is published or saved as a draft while one is there.
  Published, the file is described (imeta: its type, hash and size) from
  what the media servers confirmed. The preview shows "▶ Audio: name"
  where readers put the player.

  The command is Nostr's (D-1): in the Insert menu only while an account
  is in use.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from doc_walk import serialize_plain_with_images  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, document_to_note_text, read_markdown  # noqa: E402,E501
from nostr import preview  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

SHA = hashlib.sha256(b"sound").hexdigest()
URL = f"https://cdn.example/{SHA}.mp3"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def with_placeholder(markdown="Listen:\n"):
    ed = HtmlEditor()
    read_markdown(ed.document(), markdown)
    cursor = ed.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    rich_text.insert_upload_placeholder(cursor, "id-1", "Uploading song.mp3…")
    ed.setTextCursor(cursor)
    return ed


def test_a_placeholder_stands_alone_and_is_one_undo_step():
    ed = with_placeholder()
    found = rich_text.upload_placeholders(ed.document())
    assert [upload_id for upload_id, _block in found] == ["id-1"]
    assert found[0][1].text() == "Uploading song.mp3…"
    ed.undo()
    assert rich_text.upload_placeholders(ed.document()) == []
    assert ed.toPlainText() == "Listen:"


def test_a_placeholder_is_never_written_as_text():
    ed = with_placeholder()
    for written in (document_to_markdown(ed.document()), document_to_note_text(ed.document()),
                    serialize_plain_with_images(ed.document(), lambda fmt: None)):
        assert "Uploading" not in written and "Listen:" in written


def test_done_it_becomes_the_address_written_bare():
    ed = with_placeholder()
    _id, block = rich_text.upload_placeholders(ed.document())[0]
    rich_text.finish_upload(block, URL)
    assert rich_text.upload_placeholders(ed.document()) == []
    assert URL in document_to_markdown(ed.document())
    assert "Uploading" not in ed.toPlainText()


def test_removed_its_paragraph_goes_and_the_text_around_it_stays():
    ed = with_placeholder("Before.\n\nAfter.\n")
    blocks = rich_text.upload_placeholders(ed.document())
    rich_text.set_upload_words(blocks[0][1], "Couldn’t upload song.mp3.")
    assert blocks[0][1].text() == "Couldn’t upload song.mp3."
    rich_text.remove_upload(blocks[0][1])
    assert rich_text.upload_placeholders(ed.document()) == []
    assert ed.toPlainText().split("\n")[:2] == ["Before.", "After."]


def test_an_address_without_an_extension_gets_the_files():
    from main_window import _with_extension
    assert _with_extension(f"https://cdn.example/{SHA}", "/x/Song.MP3") == \
        f"https://cdn.example/{SHA}.mp3"
    assert _with_extension(URL, "/x/other.wav") == URL


def test_the_preview_shows_where_readers_put_the_player():
    markdown = preview.markdown_for_preview(f"Listen:\n\n{URL}\n\nInline {URL} stays.")
    assert f"[▶ Audio: {SHA}.mp3]({URL})" in markdown
    assert f"Inline {URL} stays." in markdown
    assert "▶ Video: clip.mp4" in preview.note_html("See https://cdn.example/clip.mp4")


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, rich_text
from nostr.blossom.store import MediaFile
from nostr.publisher import build_imeta_tags

path, big = sys.argv[2], sys.argv[3]
told, asked = [], []
main_window.inform = lambda parent, **kw: told.append(kw.get("title", ""))
answers = [True, False]
main_window.ask = lambda parent, **kw: (asked.append(kw.get("title", "")), answers.pop(0))[1]
w = main_window.MainWindow()
w.show()
ed = w.new_tab()
sent = []
w._media_store.upload_bytes = lambda body, name, mime_type: sent.append((name, mime_type, len(body)))
r = {}
w._fill_insert_menu()
r["menu_without"] = "nostr.insert_audio_video" in [a.objectName() for a in w.m_insert.actions()]
w.nostr_state._active = True
w._update_nostr_commands()
w._fill_insert_menu()
r["menu_with"] = "nostr.insert_audio_video" in [a.objectName() for a in w.m_insert.actions()]
w._upload_limit = lambda: 100
w._upload_into(ed, big)
r["too_large"] = told[-1] if told else ""
r["no_placeholder"] = rich_text.upload_placeholders(ed.document()) == []
w._upload_limit = lambda: 10**9
w._upload_into(ed, path)
r["sent"] = sent
r["placeholder"] = [b.text() for _i, b in rich_text.upload_placeholders(ed.document())]
r["gate"] = w._confirm_images_uploaded(ed)
r["gate_said"] = told[-1]
name = sent[0][0]
w._media_store.upload_failed.emit(name, "The server is busy.")
r["asked"] = asked[-1]
r["retried"] = len(sent)
r["after_retry"] = [b.text() for _i, b in rich_text.upload_placeholders(ed.document())]
sha = sys.argv[4]
media = MediaFile(hash=sha, url=f"https://cdn.example/{sha}", mime_type="audio/mpeg", size=5,
                  urls=[{"server": "https://cdn.example", "url": f"https://cdn.example/{sha}"}])
w._media_store._files[sha] = media          # the library knows it, as after an upload
w._media_store.upload_finished.emit(name, media)
r["text"] = ed.toPlainText()
r["gate_after"] = w._confirm_images_uploaded(ed)
content, records = w._publish_payload(ed, "markdown")
r["imeta"] = build_imeta_tags(records, content)
w._upload_into(ed, path)
name2 = sent[-1][0]
ed.undo()
w._media_store.upload_failed.emit(name2, "gone")
r["undone_asked"] = len(asked)
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_audio_in_the_window(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"sound")
    big = tmp_path / "big.mp3"
    big.write_bytes(b"x" * 200)
    r = run_window_script(SCRIPT, tmp_path, str(song), str(big), SHA)
    assert r["menu_without"] is False and r["menu_with"] is True                 # D-1
    assert r["too_large"] == "“big.mp3” is too large" and r["no_placeholder"]
    assert r["sent"][0] == ["song.mp3", "audio/mpeg", 5]
    assert r["placeholder"] == ["Uploading song.mp3…"]
    assert r["gate"] is False and r["gate_said"] == "Audio or video is still uploading"
    assert r["asked"] == "Couldn’t upload “song.mp3”" and r["retried"] == 2
    assert r["after_retry"] == ["Uploading song.mp3…"]
    assert r["text"] == f"https://cdn.example/{SHA}.mp3\n"
    assert r["gate_after"] is True
    assert r["imeta"] == [[f"imeta", f"url https://cdn.example/{SHA}.mp3", "m audio/mpeg",
                           f"x {SHA}", "size 5"]]
    assert r["undone_asked"] == 1                        # undone: nothing asked, nothing put in
