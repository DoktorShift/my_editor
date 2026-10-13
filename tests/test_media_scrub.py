# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what is stripped before a picture becomes public.

The failure this guards against is silent and permanent: a photo
published with the coordinates of where it was taken still embedded.
Nobody sees it, nothing errors, and once other people hold the bytes it
cannot be recalled. So the test that matters most is not that scrubbing
runs, it is that the metadata is genuinely gone from the output.
"""

from __future__ import annotations

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from nostr.media.scrub import ScrubError, can_scrub, scrub_for_publication, would_scrub

from tests.media_fakes import GIF_BYTES


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def encoded(fmt="PNG", color="red", size=(8, 6)):
    img = QImage(*size, QImage.Format_RGB32)
    img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    assert img.save(buf, fmt)
    buf.close()
    return bytes(buf.data())


def jpeg_with_gps():
    """A JPEG carrying a recognisable EXIF payload.

    Built by splicing an APP1 segment in after SOI, which is exactly how
    a camera writes one, so the marker really is in the file rather than
    merely appended where a decoder would ignore it.
    """
    base = encoded("JPEG")
    secret = b"GPSLatitude=51.5074;GPSLongitude=-0.1278;Make=SecretCamera"
    payload = b"Exif\x00\x00" + secret
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return base[:2] + app1 + base[2:], secret


# --------------------------------------------------------------------- #
# The guarantee                                                         #
# --------------------------------------------------------------------- #

def test_exif_and_gps_do_not_survive_publication():
    data, secret = jpeg_with_gps()
    assert secret in data, "fixture is wrong, the marker was never present"
    out = scrub_for_publication(data)
    assert out.scrubbed
    assert secret not in out.data
    assert b"GPSLatitude" not in out.data
    assert b"SecretCamera" not in out.data


def test_the_picture_itself_survives():
    # Stripping metadata must not cost the user their image.
    out = scrub_for_publication(encoded("PNG", "blue", (12, 9)))
    restored = QImage.fromData(out.data)
    assert not restored.isNull()
    assert restored.size().width() == 12 and restored.size().height() == 9
    assert QColor(restored.pixel(4, 4)).name() == QColor("blue").name()


def test_scrubbing_produces_different_bytes_and_therefore_a_new_blob():
    data, _ = jpeg_with_gps()
    out = scrub_for_publication(data)
    assert out.data != data


# --------------------------------------------------------------------- #
# Formats                                                               #
# --------------------------------------------------------------------- #

def test_a_jpeg_stays_a_jpeg():
    # A photo must not balloon into a lossless format on publication.
    out = scrub_for_publication(encoded("JPEG"))
    assert out.mime == "image/jpeg"
    assert out.data[:2] == b"\xff\xd8"


def test_a_png_stays_a_png():
    out = scrub_for_publication(encoded("PNG"))
    assert out.mime == "image/png"
    assert out.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_bmp_becomes_a_png():
    out = scrub_for_publication(encoded("BMP"))
    assert out.mime == "image/png" and out.scrubbed


# A two-frame GIF with a loop, a comment and an XMP packet holding a
# location. The frames are kept byte for byte; what is not a frame (the
# comment, XMP, any other application block) is not.
_GIF_HEAD = bytes.fromhex("474946383961" "0100" "0100" "80" "00" "00" "000000" "ffffff")
_LOOP = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
_FRAME = (bytes.fromhex("21f904" "04" "0a00" "00" "00")            # graphic control
          + bytes.fromhex("2c" "0000" "0000" "0100" "0100" "00")    # image descriptor
          + bytes.fromhex("02" "02" "4401" "00"))                    # pixels
_XMP = b'<x:xmpmeta><rdf:Description exif:GPSLatitude="52,31.0N"/></x:xmpmeta>'


def _blocks(data: bytes) -> bytes:
    return b"".join(bytes([len(data[i:i + 255])]) + data[i:i + 255]
                    for i in range(0, len(data), 255)) + b"\x00"


def _tagged_gif() -> bytes:
    return (_GIF_HEAD + _LOOP + b"\x21\xff\x0bXMP DataXMP" + _blocks(_XMP)
            + b"\x21\xfe" + _blocks(b"taken at home, 52.5N 13.4E")
            + _FRAME + _FRAME + b"\x3b")


def test_a_gif_loses_its_comment_and_xmp_and_keeps_every_frame():
    """GIF carries XMP (where a location can be) and comments in blocks of
    its own: they go, and the frames, loop included, stay byte for byte,
    so an animation is not flattened to its first frame."""
    out = scrub_for_publication(_tagged_gif())
    assert out.mime == "image/gif" and out.scrubbed
    assert b"GPSLatitude" not in out.data and b"52.5N" not in out.data
    assert out.data == _GIF_HEAD + _LOOP + _FRAME + _FRAME + b"\x3b"
    assert not QImage.fromData(out.data).isNull()


def test_a_gif_that_cannot_be_read_is_refused():
    with pytest.raises(ScrubError):
        scrub_for_publication(_GIF_HEAD + b"\x21\xfe\x05abc")   # cut short


def test_the_kinds_whose_details_can_be_removed():
    assert all(can_scrub(m) for m in ("image/jpeg", "image/png", "image/gif",
                                      "image/webp", "image/bmp"))
    assert not any(can_scrub(m) for m in ("image/heic", "image/avif", "video/mp4",
                                          "audio/mpeg", "application/pdf", ""))


# --------------------------------------------------------------------- #
# Refusals                                                              #
# --------------------------------------------------------------------- #

def test_an_svg_is_refused():
    # SVG can pull in external resources when rendered, so it is not
    # something this app hands to the public internet.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="/etc/passwd"/></svg>'
    with pytest.raises(ScrubError):
        scrub_for_publication(svg)


@pytest.mark.parametrize("junk", [b"", b"not an image at all", b"\x00" * 64])
def test_non_images_are_refused(junk):
    with pytest.raises(ScrubError):
        scrub_for_publication(junk)


def test_an_encrypted_blob_is_refused():
    # A private envelope reaching the publish path unencrypted would be
    # a serious bug; refusing it is the backstop.
    envelope = b"\x02" + os.urandom(80)
    with pytest.raises(ScrubError):
        scrub_for_publication(envelope)


def test_a_declared_mime_is_never_trusted_over_the_bytes():
    with pytest.raises(ScrubError):
        scrub_for_publication(b"still not an image", declared_mime="image/png")


def test_a_truncated_image_is_refused_rather_than_half_published():
    data = encoded("PNG")
    with pytest.raises(ScrubError):
        scrub_for_publication(data[: len(data) // 3])


# --------------------------------------------------------------------- #
# Telling the user in advance                                           #
# --------------------------------------------------------------------- #

def test_would_scrub_reports_what_publishing_will_do():
    assert would_scrub(encoded("JPEG")) is True
    assert would_scrub(encoded("PNG")) is True
    assert would_scrub(GIF_BYTES) is True


def test_would_scrub_separates_nothing_to_strip_from_cannot_publish():
    # None means "not publishable", which a confirm dialog must not
    # report as "nothing will be stripped".
    assert would_scrub(b"not an image") is None
    assert would_scrub(b"") is None


def _with_text(fmt: str) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("orange"))
    image.setText("Location", "GPS 47.3769 8.5417 secret")
    image.setText("Description", "home address secret")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(buffer.data())


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_text_hidden_in_the_file_does_not_survive(fmt):
    original = _with_text(fmt)
    assert b"secret" in original                      # the test file carries it
    result = scrub_for_publication(original)
    assert result.scrubbed
    assert b"secret" not in result.data
    assert b"GPS" not in result.data
