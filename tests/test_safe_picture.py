# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""safe_picture: files read only when they are regular files, and every
picture from the disk decoded one safe way.

The defect classes guarded here:
- a read that never ends or never starts: a picture that is a symlink to
  a device (``/dev/zero`` states a size of 0 and never ends), a named
  pipe with a picture's name,
- a byte limit trusted from the stated size instead of counted,
- an SVG that reads a local file through any of its hrefs, or a few
  bytes of nested ``<use>`` elements that make billions of shapes,
- a thumbnail or a picture size read by a path-based QImageReader, which
  guesses the format and hands SVG to QtSvg unsanitized.
"""

import base64
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import file_list  # noqa: E402
import regular_files  # noqa: E402
import safe_picture  # noqa: E402

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="pipes and /dev/zero")

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv[:1])


def finishes(work, seconds=3.0):
    """``(done, result)``: whether ``work()`` returned within ``seconds``
    on a thread of its own (a read that blocks must not hang the tests)."""
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", work()), daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), box.get("result")


def write_picture(path, width=40, height=30, color="#0000FF"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return str(path)


def center(image):
    color = image.pixelColor(image.width() // 2, image.height() // 2)
    return color.red(), color.green(), color.blue()


# --------------------------------------------------------------------------- #
# Regular files                                                               #
# --------------------------------------------------------------------------- #

@posix_only
def test_a_picture_that_is_a_link_to_a_device_is_not_read(tmp_path):
    os.symlink("/dev/zero", tmp_path / "photo.png")
    done, result = finishes(lambda: safe_picture.read_picture(str(tmp_path / "photo.png")))
    assert done and result is None
    assert regular_files.read_regular_file(str(tmp_path / "photo.png"), 1024) is None
    assert not regular_files.is_regular_file(str(tmp_path / "photo.png"))


@posix_only
def test_a_pipe_with_a_pictures_name_is_never_opened(tmp_path):
    os.mkfifo(tmp_path / "photo.png")
    for work in (lambda: safe_picture.read_picture(str(tmp_path / "photo.png")),
                 lambda: safe_picture.picture_size(str(tmp_path / "photo.png")),
                 lambda: file_list.scaled_picture(str(tmp_path / "photo.png")),
                 lambda: file_list.picture_size(str(tmp_path / "photo.png"))):
        done, result = finishes(work)
        assert done and not result


@posix_only
def test_a_note_or_text_that_is_a_pipe_gives_no_first_line(tmp_path):
    os.mkfifo(tmp_path / "Plan.md")
    done, result = finishes(lambda: file_list.note_snippet(str(tmp_path / "Plan.md")))
    assert done and result == ""


def test_the_byte_limit_is_counted_not_taken_from_the_stated_size(tmp_path, monkeypatch):
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 101)
    assert regular_files.read_regular_file(str(path), 100) is None
    assert regular_files.read_regular_file(str(path), 101) == b"x" * 101
    # A size the file system gets wrong changes nothing.
    monkeypatch.setattr(os.path, "getsize", lambda _path: 0)
    assert regular_files.read_regular_file(str(path), 100) is None


def test_a_folder_is_no_regular_file(tmp_path):
    assert regular_files.read_regular_file(str(tmp_path), 100) is None
    with pytest.raises(OSError):
        regular_files.open_regular_file(str(tmp_path))


def test_a_file_being_read_can_be_renamed_and_removed_meanwhile(tmp_path):
    # As on every system: the app reading a picture on a worker thread
    # never stands in the way of the person renaming or deleting it.
    path = tmp_path / "photo.png"
    path.write_bytes(b"x" * 10)
    with regular_files.open_regular_file(str(path)) as stream:
        os.rename(path, tmp_path / "beach.png")
        assert stream.read() == b"x" * 10
        os.remove(tmp_path / "beach.png")
    assert os.listdir(tmp_path) == []


def test_a_folder_waits_only_for_the_reads_of_files_in_it(tmp_path):
    (tmp_path / "Album" / "Trip").mkdir(parents=True)
    (tmp_path / "Albums").mkdir()
    inside = tmp_path / "Album" / "Trip" / "photo.png"
    beside = tmp_path / "Albums" / "photo.png"
    for path in (inside, beside):
        path.write_bytes(b"x")
    with regular_files.open_regular_file(str(beside)):
        assert regular_files.wait_for_reads(str(tmp_path / "Album"), 0)
        with regular_files.open_regular_file(str(inside)):
            assert not regular_files.wait_for_reads(str(tmp_path / "Album"), 0.05)
        assert regular_files.wait_for_reads(str(tmp_path / "Album"), 0)
    closed = []

    def close_soon(stream):
        time.sleep(0.2)
        closed.append(True)
        stream.close()

    stream = regular_files.open_regular_file(str(inside))
    threading.Thread(target=close_soon, args=(stream,)).start()
    assert regular_files.wait_for_reads(str(tmp_path / "Album"), 10)
    assert closed == [True]


def test_a_missing_file_is_said_to_be_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        regular_files.open_regular_file(str(tmp_path / "gone.png"))


# --------------------------------------------------------------------------- #
# Pictures                                                                    #
# --------------------------------------------------------------------------- #

def test_a_picture_is_decoded_and_can_be_decoded_small(tmp_path):
    path = write_picture(tmp_path / "a.png", 800, 400)
    assert safe_picture.read_picture(path).size() == QSize(800, 400)
    small = safe_picture.read_picture(path, QSize(100, 100))
    assert small.height() == 100 and small.width() == 200
    assert safe_picture.picture_size(path) == QSize(800, 400)


def test_a_picture_with_another_formats_name_is_read_as_what_it_is(tmp_path):
    (tmp_path / "fake.png").write_bytes(f'<svg {NS} width="5" height="5"/>'.encode())
    assert safe_picture.read_picture(str(tmp_path / "fake.png")) is None
    assert file_list.scaled_picture(str(tmp_path / "fake.png")) is None


@pytest.mark.parametrize("hrefs", [
    'xlink:href="{secret}"',
    'href="{secret_uri}"',
    'xlink:href="{red}" href="{secret_uri}"',
    'href="{red}" xlink:href="{secret_uri}"',
    'xlink:href="secret.png"',
])
def test_no_svg_picture_reads_a_local_file_whichever_href_names_it(tmp_path, hrefs):
    secret = write_picture(tmp_path / "secret.png", 50, 50, "#0000FF")
    red = write_picture(tmp_path / "red.png", 50, 50, "#FF0000")
    red_uri = "data:image/png;base64," + base64.b64encode(open(red, "rb").read()).decode()
    attrs = hrefs.format(secret=secret, secret_uri="file://" + secret, red=red_uri)
    svg = tmp_path / "Drawing.svg"
    svg.write_text(f'<svg {NS} width="50" height="50"><rect width="50" height="50" fill="#00FF00"/>'
                   f'<image width="50" height="50" {attrs}/></svg>', encoding="utf-8")
    for image in (safe_picture.read_picture(str(svg)), file_list.scaled_picture(str(svg), 40)):
        assert image is not None
        assert center(image)[2] < 128, "the local picture was read"


def nested_uses(levels, fan=10):
    defs = ['<rect id="l0" width="1" height="1" fill="red"/>']
    for i in range(1, levels + 1):
        defs.append(f'<g id="l{i}">' + "".join(
            f'<use xlink:href="#l{i - 1}" x="{j % 3}"/>' for j in range(fan)) + "</g>")
    return (f'<svg {NS} width="100" height="100"><defs>{"".join(defs)}</defs>'
            f'<use xlink:href="#l{levels}"/></svg>').encode()


def test_an_svg_of_nested_uses_is_refused_at_once(tmp_path):
    path = tmp_path / "fan.svg"
    path.write_bytes(nested_uses(8))                   # a hundred million squares
    assert len(path.read_bytes()) < 4096
    started = time.monotonic()
    assert safe_picture.read_picture(str(path)) is None
    assert file_list.scaled_picture(str(path)) is None
    assert time.monotonic() - started < 1.0


def test_an_svg_with_a_few_uses_is_drawn(tmp_path):
    path = tmp_path / "few.svg"
    path.write_bytes(nested_uses(2))
    assert safe_picture.read_picture(str(path)) is not None


def test_an_svg_whose_uses_lead_round_in_a_loop_is_refused():
    svg = (f'<svg {NS} width="10" height="10"><g id="a"><use xlink:href="#b"/></g>'
           f'<g id="b"><use xlink:href="#a"/></g></svg>').encode()
    assert safe_picture.safe_svg(svg) is None


def test_a_use_that_names_nothing_in_the_file_is_taken_out():
    svg = f'<svg {NS} width="10" height="10"><use xlink:href="#missing"/></svg>'.encode()
    assert b"use" not in safe_picture.safe_svg(svg)


def test_an_svg_is_drawn_no_larger_than_the_render_side(tmp_path):
    path = tmp_path / "huge.svg"
    path.write_text(f'<svg {NS} width="6000" height="3000"><rect width="6000" height="3000"/></svg>',
                    encoding="utf-8")
    image = safe_picture.read_picture(str(path))
    assert max(image.width(), image.height()) <= safe_picture.SVG_RENDER_SIDE
    # It still measures its own size.
    assert round(image.width() / image.devicePixelRatio()) == 6000
    assert safe_picture.picture_size(str(path)) == QSize(6000, 3000)
