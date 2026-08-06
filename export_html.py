#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Handrolled HTML5 exporter for the editor's QTextDocuments.

Qt's own toHtml() emits HTML 4.0 with proprietary -qt-* attributes and an
inline style on every paragraph; bullets stay literal "• " text and images
point at machine-local cache paths. This module replaces it with clean,
semantic, fully self-contained HTML:

- bullets become real nested <ul><li> lists,
- bold/italic/underline become <strong>/<em>/<u>,
- colors stay inline styles (Qt ignores <style> blocks on setHtml, so
  inline is the only representation that survives reopening the file),
- images are embedded as base64 data URIs so a shared file carries its
  media with it; the original upload URL rides along in data-source-url.

normalize_lists_after_set_html() is the inverse half of the round-trip:
after loading any HTML file, real QTextList items are converted back to
the editor's literal "• " bullet convention so the bullet key handlers
keep working.
"""

import base64
import html

from PySide6.QtGui import (
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)

from constants import DARK_BG, DARK_FG, LIGHT_BG, LIGHT_FG, MONO_FONT
from doc_walk import (
    INDENT_STEP,
    bullet_depth,
    iter_block_runs,
    iter_blocks,
    parse_bullet_line,
    skip_prefix,
)

GENERATOR = "minimal texteditor"

# Object-replacement character marking an inline image fragment.
_OBJ = "\ufffc"

# Qt uses U+2028 for Shift+Enter line breaks inside a block.
_LINE_SEP = "\u2028"

_MAGIC_MIMES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image_mime(data: bytes) -> str | None:
    """Detect an image MIME type from magic bytes.

    Needed because Blossom cache files are named by bare sha256 with no
    extension. Returns None for unrecognized data.
    """
    for magic, mime in _MAGIC_MIMES:
        if data.startswith(magic):
            return mime
    # WebP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # SVG is text; look for an <svg root near the start.
    head = data[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        if b"<svg" in data[:2048]:
            return "image/svg+xml"
    return None


_EXT_FOR_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
}


def sniff_image_ext(data: bytes) -> str | None:
    """File extension for image data, or None when unrecognized."""
    mime = sniff_image_mime(data)
    return _EXT_FOR_MIME.get(mime) if mime else None


def _image_data_uri(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    mime = sniff_image_mime(data)
    if mime is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _font_stack() -> str:
    """Monospace stack led by the app's font, without duplicate entries."""
    fonts = ["ui-monospace", f"'{MONO_FONT}'", "Menlo", "Consolas",
             "'Noto Sans Mono'", "monospace"]
    seen = set()
    unique = []
    for f in fonts:
        key = f.strip("'").lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return ", ".join(unique)


def _head_css() -> str:
    # The page CSS is for browsers only; Qt ignores <style> blocks when the
    # file is reopened, which is fine because everything that must round-trip
    # (colors, pre-wrap paragraphs) is also emitted inline.
    return f"""    :root {{ color-scheme: light dark; }}
    body {{
      margin: 2rem auto;
      max-width: 48rem;
      padding: 0 1rem;
      font-family: {_font_stack()};
      font-size: 14px;
      line-height: 1.5;
      background: {LIGHT_BG};
      color: {LIGHT_FG};
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: {DARK_BG}; color: {DARK_FG}; }}
    }}
    p {{ margin: 0; }}
    ul {{ margin: 0; padding-left: 1.5em; }}
    ul ul {{ list-style-type: circle; }}
    ul ul ul {{ list-style-type: square; }}
    img {{ max-width: 100%; height: auto; }}"""


def _render_image(img_fmt, source_url_for) -> str:
    path = img_fmt.name()
    source_url = source_url_for(path) if source_url_for else None
    attrs = ""
    if source_url:
        attrs += f' data-source-url="{html.escape(source_url, quote=True)}"'

    uri = _image_data_uri(path)
    if uri is not None:
        return f'<img src="{uri}" alt=""{attrs}>'
    if source_url:
        # Bytes are gone from the cache; the original URL is better than
        # nothing even though it breaks strict self-containment.
        return f'<img src="{html.escape(source_url, quote=True)}" alt=""{attrs}>'
    return "<em>[image unavailable]</em>"


def _render_text_run(text: str, fmt: QTextCharFormat) -> str:
    out = html.escape(text).replace(_LINE_SEP, "<br>")
    if fmt.fontUnderline():
        out = f"<u>{out}</u>"
    if fmt.fontItalic():
        out = f"<em>{out}</em>"
    if fmt.fontWeight() > 400:
        out = f"<strong>{out}</strong>"
    if fmt.hasProperty(QTextCharFormat.ForegroundBrush):
        color = fmt.foreground().color().name()
        out = f'<span style="color:{color}">{out}</span>'
    return out


def _render_runs(runs, source_url_for) -> str:
    parts = []
    for text, fmt in runs:
        if fmt.isImageFormat():
            parts.append(_render_image(fmt.toImageFormat(), source_url_for))
            continue
        # Defensive: strip stray object-replacement chars from plain runs.
        text = text.replace(_OBJ, "")
        if text:
            parts.append(_render_text_run(text, fmt))
    return "".join(parts)


def _needs_pre_wrap(text: str) -> bool:
    """Leading or consecutive spaces would collapse in normal HTML flow."""
    return text.startswith(" ") or "  " in text


def document_to_html(doc, title: str = "", source_url_for=None) -> str:
    """Serialize a QTextDocument to a self-contained semantic HTML5 page.

    source_url_for: optional callback mapping a local image path to its
    original https URL, used only for the data-source-url provenance
    attribute and as a last-resort src when the cached bytes are gone.
    """
    body: list[str] = []
    # List markup is emitted compactly (no newlines): whitespace text nodes
    # inside <li> would be re-parsed by Qt as trailing spaces on the item.
    list_buf: list[str] = []
    depth = 0             # how many <ul> levels are open
    li_open: list[bool] = [False]  # index = level, [0] unused

    def close_to(target: int):
        nonlocal depth
        while depth > target:
            if li_open[depth]:
                list_buf.append("</li>")
                li_open[depth] = False
            list_buf.append("</ul>")
            depth -= 1
        if target == 0 and list_buf:
            body.append("".join(list_buf))
            list_buf.clear()

    for block in iter_blocks(doc):
        text = block.text()
        spaces, has_bullet = parse_bullet_line(text)

        if has_bullet:
            d = bullet_depth(spaces)
            close_to(d)
            while depth < d:
                list_buf.append("<ul>")
                depth += 1
                if len(li_open) <= depth:
                    li_open.append(False)
                if depth < d:
                    # Intermediate level of a depth jump: its item exists
                    # only to hold the nested list.
                    list_buf.append("<li>")
                    li_open[depth] = True
            if li_open[d]:
                list_buf.append("</li>")
            list_buf.append("<li>")
            li_open[d] = True

            runs = skip_prefix(list(iter_block_runs(block)), spaces + len("• "))
            list_buf.append(_render_runs(runs, source_url_for))
            continue

        close_to(0)
        if not text and block.begin().atEnd():
            # &nbsp; is the only form Qt re-parses as exactly one (visually
            # blank) paragraph; normalize_lists_after_set_html turns it back
            # into a truly empty block on load.
            body.append("<p>&nbsp;</p>")
            continue
        content = _render_runs(list(iter_block_runs(block)), source_url_for)
        if _needs_pre_wrap(text):
            body.append(f'<p style="white-space:pre-wrap">{content}</p>')
        else:
            body.append(f"<p>{content}</p>")

    close_to(0)

    page_title = html.escape(title) if title else "Untitled"
    body_html = "\n".join(body)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="{GENERATOR}">
  <title>{page_title}</title>
  <style>
{_head_css()}
  </style>
</head>
<body>
{body_html}</body>
</html>
"""


def normalize_lists_after_set_html(doc) -> None:
    """Convert QTextList items back to the editor's literal bullet lines.

    Qt parses <ul><li> into QTextList objects, but the editor's bullet
    behavior (Tab, Enter, Backspace handlers) operates on literal "• "
    lines. Called after every setHtml so both our own exports and foreign
    HTML files edit consistently. Depth N becomes N*4 leading spaces.
    """
    NBSP = "\u00a0"
    list_targets = []
    nbsp_targets = []
    for block in iter_blocks(doc):
        lst = block.textList()
        if lst is not None:
            list_targets.append((block.position(), max(1, lst.format().indent())))
        elif block.text() == NBSP:
            # Our own exports encode empty lines as <p>&nbsp;</p> because Qt
            # drops <p></p> and doubles <p><br></p>; restore true emptiness.
            nbsp_targets.append(block.position())

    # Process bottom-up so earlier positions stay valid while we mutate.
    for pos in reversed(nbsp_targets):
        block = doc.findBlock(pos)
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

    for pos, depth in reversed(list_targets):
        block = doc.findBlock(pos)
        lst = block.textList()
        if lst is not None:
            lst.remove(block)

        # Pretty-printed foreign HTML often leaves "item\n" whitespace that
        # Qt parses into trailing spaces; trim them from the item.
        stripped_len = len(block.text().rstrip())
        cursor = QTextCursor(block)
        cursor.setBlockFormat(QTextBlockFormat())
        if stripped_len < len(block.text()):
            cursor.setPosition(block.position() + stripped_len)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()

        marker_fmt = QTextCharFormat()
        marker_fmt.setFontWeight(400)
        marker_fmt.setFontItalic(False)
        marker_fmt.setFontUnderline(False)
        marker_fmt.clearForeground()
        cursor.setPosition(block.position())
        cursor.insertText(" " * (depth * INDENT_STEP) + "• ", marker_fmt)

    # Qt folds whitespace after </html> (e.g. the file's final newline) into
    # the last paragraph as a trailing space; trim it.
    last = doc.lastBlock()
    text = last.text()
    trimmed = text.rstrip(" " + NBSP)
    if len(trimmed) < len(text):
        cursor = QTextCursor(last)
        cursor.setPosition(last.position() + len(trimmed))
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
