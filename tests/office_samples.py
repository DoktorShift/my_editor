# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small real office files for tests, built with zipfile and a few lines of
XML: a Word document, an OpenDocument text, files that carry a preview
picture, and pictures (one honest, one whose header claims a size no
decoder should ever allocate)."""

from __future__ import annotations

import os
import struct
import zipfile
import zlib

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def picture_bytes(width=40, height=30, color="#3A7", fmt="PNG") -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, fmt)
    buffer.close()
    return bytes(data)


def giant_png_header(width=30000, height=30000) -> bytes:
    """A PNG whose header claims ``width`` by ``height`` pixels (with a
    valid checksum) and carries no picture."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def make_docx(path, *, picture: bytes | None = None, extra_body: str = "",
              doctype: str = "") -> str:
    """A .docx with a heading (a style named "heading 1" under a German
    style id), a paragraph with a web link, a bulleted item, a table and,
    when given, a picture."""
    drawing = ""
    if picture is not None:
        drawing = (f'<w:p><w:r><w:drawing><a:graphic xmlns:a="{A}"><a:graphicData>'
                   f'<a:blip r:embed="rIdPic"/></a:graphicData></a:graphic></w:drawing>'
                   f'</w:r></w:p>')
    document = f"""<?xml version="1.0" encoding="UTF-8"?>{doctype}
<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
<w:p><w:pPr><w:pStyle w:val="berschrift1"/></w:pPr><w:r><w:t>Meeting notes</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Read the </w:t></w:r><w:hyperlink r:id="rIdWeb"><w:r><w:t>agenda</w:t></w:r></w:hyperlink><w:r><w:t xml:space="preserve"> first.</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Bring coffee</w:t></w:r></w:p>
<w:tbl>
<w:tr><w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Amount</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Coffee</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
{drawing}{extra_body}
</w:body></w:document>"""
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="berschrift1">'
              f'<w:name w:val="heading 1"/></w:style></w:styles>')
    rels = (f'<Relationships xmlns="{PKG}">'
            f'<Relationship Id="rIdWeb" Type="{R}/hyperlink" Target="https://example.org/agenda" '
            f'TargetMode="External"/>'
            f'<Relationship Id="rIdPic" Type="{R}/image" Target="media/image1.png"/>'
            f'</Relationships>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", rels)
        if picture is not None:
            archive.writestr("word/media/image1.png", picture)
    return str(path)


def make_odt(path, *, picture: bytes | None = None, thumbnail: bytes | None = None) -> str:
    frame = ""
    if picture is not None:
        frame = ('<text:p><draw:frame><draw:image xlink:href="Pictures/photo.png"/>'
                 '</draw:frame></text:p>')
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink">
<office:body><office:text>
<text:h text:outline-level="2">Trip plan</text:h>
<text:p>Leave at<text:s text:c="3"/>eight.</text:p>
<text:list><text:list-item><text:p>Tickets</text:p></text:list-item></text:list>
<table:table>
<table:table-row><table:table-cell><text:p>Day</text:p></table:table-cell><table:table-cell><text:p>Town</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell><text:p>Monday</text:p></table:table-cell><table:table-cell><text:p>Lyon</text:p></table:table-cell><table:table-cell table:number-columns-repeated="1000"/></table:table-row>
</table:table>
{frame}
</office:text></office:body></office:document-content>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", content)
        if picture is not None:
            archive.writestr("Pictures/photo.png", picture)
        if thumbnail is not None:
            archive.writestr("Thumbnails/thumbnail.png", thumbnail)
    return str(path)


def make_zip(path, members: dict) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return str(path)


def make_package(folder, members: dict) -> str:
    """A package folder (a Numbers, Pages or Keynote document saved as one)."""
    os.makedirs(folder, exist_ok=True)
    for name, data in members.items():
        target = os.path.join(folder, *name.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as stream:
            stream.write(data)
    return str(folder)
