# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A note or an article, shown the way Nostr apps will show it.

The preview renders exactly what is published (the text the publish
dialogs and drafts send, from markdown_writer.py), never the editor's own
document, so what it shows is what readers get. It follows what the
widely used apps do, as surveyed in October 2026:

Articles (kind 30023) are Markdown, rendered like the long-form readers
(Habla, Highlighter, YakiHonne, Primal, noStrudel): a cover image, the
title, the summary in muted text, a byline ("Name · date · N min read",
225 words a minute), then the body in a calm serif column about 680
pixels wide, headings in bold sans, quotes indented and muted, code on a
light background without colors, tags as "#tag" after the body. A single
line break inside a paragraph is a space, as everywhere; raw HTML is
shown as text.

Notes (kind 1) are plain text in every app that matters: line breaks are
kept, ``**`` stays literal, links, hashtags and mentions are colored, and
images (by file extension) show below the text, their address taken out
of it, as Damus and Primal do.

In both, a ``nostr:`` reference to a person shows as "@Name" (their
name when it is known, a shortened key otherwise); one to a note or an
article shows as a labelled link, since cards differ between apps.

:func:`article_document` and :func:`note_document` build a
QTextDocument; :class:`NostrPreview` shows one in a QTextBrowser, with
images from an injected loader (no loader, no network).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

import shiboken6
from PySide6.QtCore import QDate, QLocale, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
)
from PySide6.QtWidgets import QTextBrowser

import i18n
import word_count
from i18n import _, ngettext
from markdown_writer import READ_FEATURES, read_markdown
from nostr import bech32

# person's pubkey (hex) -> the name to show, or None
NameLookup = Callable[[str], Optional[str]]
# image address -> the image, when it is at hand (no network here)
ImageLookup = Callable[[str], Optional[QImage]]

COLUMN_WIDTH = 680
WORDS_PER_MINUTE = word_count.WORDS_PER_MINUTE
SERIF = ["Charter", "Iowan Old Style", "Source Serif Pro", "Georgia", "serif"]
MEDIA_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

_REFERENCE = re.compile(r"nostr:(npub1|nprofile1|note1|nevent1|naddr1)[02-9ac-hj-np-z]+")
_URL = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]]")
_HASHTAG = re.compile(r"(?<![\w#])#(\w[\w-]*)")


@dataclass(frozen=True)
class Palette:
    text: str
    muted: str
    link: str
    code_background: str
    hairline: str
    background: str


LIGHT = Palette("#222222", "#6B6B6B", "#0A66C2", "#F6F8FA", "#E6E6E6", "#FFFFFF")
DARK = Palette("#E8E8E8", "#9A9A9A", "#4AA3FF", "#26282B", "#2C2C2E", "#161618")


def palette(dark: bool) -> Palette:
    return DARK if dark else LIGHT


@dataclass(frozen=True)
class Article:
    """What an article carries besides its body."""

    title: str = ""
    summary: str = ""
    image: str = ""
    tags: Sequence[str] = field(default_factory=tuple)
    author: str = ""
    published_at: int = 0          # unix seconds; 0 = now


# --------------------------------------------------------------------------- #
# References                                                                   #
# --------------------------------------------------------------------------- #

def short_key(npub: str) -> str:
    """``npub1abcd…wxyz``: a key short enough to read."""
    return f"{npub[:9]}…{npub[-4:]}" if len(npub) > 16 else npub


def reference_label(reference: str, names: Optional[NameLookup]) -> str:
    """How a ``nostr:`` reference reads in the preview."""
    entity = reference[len("nostr:"):]
    try:
        if entity.startswith("npub1"):
            pubkey = bech32.decode_npub(entity)
        elif entity.startswith("nprofile1"):
            pubkey = bech32.decode_nprofile(entity)[0]
        elif entity.startswith("naddr1"):
            return _("Article")
        else:
            return _("Quoted note")
    except (ValueError, IndexError):
        return entity
    name = names(pubkey) if names else None
    if not name:
        name = short_key(bech32.encode_npub(pubkey))
    return "@" + name


def _outside_code(markdown: str, change: Callable[[str], str]) -> str:
    """Apply ``change`` to the Markdown outside fenced code blocks and
    inline code, which show references as they are."""
    out = []
    fence = None
    for line in markdown.split("\n"):
        stripped = line.lstrip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            out.append(line)
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append(line)
            continue
        parts = re.split(r"(`+[^`]*`+)", line)
        out.append("".join(p if p.startswith("`") else change(p) for p in parts))
    return "\n".join(out)


def markdown_for_preview(markdown: str, names: Optional[NameLookup] = None) -> str:
    """The article's Markdown with every ``nostr:`` reference turned into
    a link that reads like the apps show it."""
    def replace(text: str) -> str:
        return _REFERENCE.sub(
            lambda m: f"[{reference_label(m.group(0), names)}]({m.group(0)})", text)
    return _outside_code(markdown, replace)


# --------------------------------------------------------------------------- #
# Articles                                                                     #
# --------------------------------------------------------------------------- #

def reading_minutes(markdown: str) -> int:
    """Minutes to read the article, by the one rule (word_count.py); at
    least one, since a byline never says "0 min read"."""
    return max(1, word_count.reading_minutes(word_count.count_words(markdown)))


def _date_text(published_at: int) -> str:
    from PySide6.QtCore import QDateTime
    when = (QDateTime.fromSecsSinceEpoch(published_at).date() if published_at
            else QDate.currentDate())
    return QLocale(i18n.language()).toString(when, QLocale.FormatType.ShortFormat)


def _char(*, size: float = 0, bold: bool = False, color: str = "",
          family: Optional[Sequence[str]] = None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if size:
        fmt.setFontPointSize(size)
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if color:
        fmt.setForeground(QColor(color))
    if family:
        fmt.setFontFamilies(list(family))
    return fmt


def _style_body(first_block, colors: Palette) -> None:
    """Give the rendered Markdown the readers' look, block by block, from
    ``first_block`` to the end."""
    heading_sizes = {1: 22.0, 2: 18.0, 3: 15.0}
    block = first_block
    while block.isValid():
        fmt = block.blockFormat()
        cursor = QTextCursor(block)
        heading = fmt.headingLevel()
        fence = fmt.property(QTextFormat.Property.BlockCodeFence)
        quote = fmt.property(QTextFormat.Property.BlockQuoteLevel)
        in_table = cursor.currentTable() is not None
        if heading:
            fmt.setTopMargin(22)
            fmt.setBottomMargin(8)
            char = _char(size=heading_sizes.get(heading, 13.5), bold=True, color=colors.text)
        elif fence:
            fmt.setBackground(QColor(colors.code_background))
            fmt.setLeftMargin(12)
            fmt.setRightMargin(12)
            fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            char = _char(size=12, color=colors.text)
        elif in_table:
            fmt.setBottomMargin(0)
            char = _char(size=12.5, color=colors.text, family=SERIF)
        else:
            fmt.setLineHeight(160, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            # List items sit closer together than paragraphs.
            fmt.setBottomMargin(4 if block.textList() is not None else 12)
            color = colors.muted if isinstance(quote, int) and quote > 0 else colors.text
            char = _char(size=13.5, color=color, family=SERIF)
            if isinstance(quote, int) and quote > 0:
                fmt.setLeftMargin(16 * quote)
        cursor.setBlockFormat(fmt)
        _merge_fragments(block, char, colors)
        block = block.next()


def _tidy_tables(doc: QTextDocument) -> None:
    """Undo a quirk of Qt's Markdown import: a table right after a code
    block gets an empty line in its first cell, and that cell loses the
    header's weight. Header cells are bold, as the apps show them."""
    seen = set()
    block = doc.begin()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is not None and id(table) not in seen and table.rows():
            seen.add(id(table))
            for column in range(table.columns()):
                cell = table.cellAt(0, column)
                first = doc.findBlock(cell.firstPosition())
                if not first.text() and first.next().position() <= cell.lastPosition():
                    cursor = QTextCursor(first)
                    cursor.deleteChar()
                cursor = cell.firstCursorPosition()
                cursor.setPosition(cell.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
                bold = QTextCharFormat()
                bold.setFontWeight(QFont.Weight.Bold)
                cursor.mergeCharFormat(bold)
        block = block.next()


def _merge_fragments(block, char: QTextCharFormat, colors: Palette) -> None:
    """Apply ``char`` to every piece of the block, keeping inline code in
    its fixed-width font and links in the link color."""
    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        if fragment.isValid():
            cursor = QTextCursor(block.document())
            cursor.setPosition(fragment.position())
            cursor.setPosition(fragment.position() + fragment.length(),
                               QTextCursor.MoveMode.KeepAnchor)
            own = fragment.charFormat()
            if not own.isImageFormat():
                fmt = QTextCharFormat(own)
                fmt.merge(char)
                # Qt sizes headings relative to the text; the size given wins.
                fmt.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                if own.fontFixedPitch():
                    fmt.setFontFamilies(own.fontFamilies() or ["monospace"])
                    fmt.setBackground(QColor(colors.code_background))
                if own.isAnchor():
                    fmt.setForeground(QColor(colors.link))
                cursor.setCharFormat(fmt)
        it += 1


# Read the way the editor reads it (``_word_`` is italic, as in every
# reader), with raw HTML shown as text: NIP-23 rules HTML out, and the
# apps that clean it up show it as text or drop it.
MARKDOWN_FEATURES = QTextDocument.MarkdownFeature(
    READ_FEATURES.value | QTextDocument.MarkdownFeature.MarkdownNoHTML.value)


def article_document(markdown: str, article: Article, *, names: Optional[NameLookup] = None,
                     dark: bool = False, parent=None) -> QTextDocument:
    """The article as readers will see it."""
    colors = palette(dark)
    doc = QTextDocument(parent)
    doc.setDocumentMargin(0)
    # The body, rendered from exactly the published Markdown.
    read_markdown(doc, markdown_for_preview(markdown, names), MARKDOWN_FEATURES)
    _tidy_tables(doc)
    _style_body(doc.begin(), colors)

    # The header goes in front: cover, title, summary, byline, a rule. A
    # new block is opened before the body's first one, which keeps its own
    # format (a heading stays a heading).
    cursor = QTextCursor(doc)
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertBlock(cursor.blockFormat(), cursor.charFormat())
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    head = QTextBlockFormat()
    head.setBottomMargin(10)
    cursor.setBlockFormat(head)
    if article.image:
        image = QTextImageFormat()
        image.setName(article.image)
        image.setWidth(COLUMN_WIDTH)
        cursor.insertImage(image)
        cursor.insertBlock(head)
    cursor.insertText(article.title or _("Untitled"),
                      _char(size=24, bold=True, color=colors.text))
    if article.summary:
        cursor.insertBlock(head)
        cursor.insertText(article.summary, _char(size=14, color=colors.muted))
    minutes = reading_minutes(markdown)
    byline = [part for part in (
        article.author, _date_text(article.published_at),
        ngettext("{n} min read", "{n} min read", minutes).format(n=minutes)) if part]
    cursor.insertBlock(head)
    cursor.insertText(" · ".join(byline), _char(size=11, color=colors.muted))
    rule = QTextBlockFormat()
    rule.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth, 1)
    rule.setBottomMargin(16)
    cursor.insertBlock(rule, QTextCharFormat())

    if article.tags:
        end = QTextCursor(doc)
        end.movePosition(QTextCursor.MoveOperation.End)
        tags = QTextBlockFormat()
        tags.setTopMargin(16)
        end.insertBlock(tags)
        end.insertText("   ".join(f"#{t}" for t in article.tags),
                       _char(size=11, color=colors.muted))
    return doc


# --------------------------------------------------------------------------- #
# Notes                                                                        #
# --------------------------------------------------------------------------- #

def is_media(url: str) -> bool:
    path = QUrl(url).path().lower()
    return path.endswith(MEDIA_EXTENSIONS)


def split_media(text: str) -> tuple:
    """``(text, media)``: the note's text with the image addresses that end
    it taken out, and every image address in it, in order."""
    media = [m.group(0) for m in _URL.finditer(text) if is_media(m.group(0))]
    trimmed = text.rstrip()
    while True:
        last = trimmed.split()[-1] if trimmed.split() else ""
        if last and last in media:
            trimmed = trimmed[: len(trimmed) - len(last)].rstrip()
        else:
            break
    return trimmed, media


def note_html(text: str, *, author: str = "", names: Optional[NameLookup] = None,
              dark: bool = False) -> str:
    """The note as HTML for a QTextBrowser: plain text, links colored."""
    colors = palette(dark)
    body, media = split_media(text)
    # Three or more line breaks show as two in every app.
    body = re.sub(r"\n{3,}", "\n\n", body)
    pieces = []
    last = 0
    pattern = re.compile(f"{_REFERENCE.pattern}|{_URL.pattern}|{_HASHTAG.pattern}")
    for match in pattern.finditer(body):
        pieces.append(html.escape(body[last:match.start()]))
        token = match.group(0)
        if token.startswith("nostr:"):
            label = reference_label(token, names)
            pieces.append(f'<span style="color:{colors.link};">{html.escape(label)}</span>')
        elif token.startswith("#"):
            pieces.append(f'<span style="color:{colors.link};">{html.escape(token)}</span>')
        else:
            pieces.append(f'<a href="{html.escape(token, quote=True)}" '
                          f'style="color:{colors.link};">{html.escape(token)}</a>')
        last = match.end()
    pieces.append(html.escape(body[last:]))
    images = "".join(
        f'<p><img src="{html.escape(url, quote=True)}" width="{COLUMN_WIDTH}"></p>'
        for url in media)
    name = (f'<p style="margin-bottom:6px;"><b>{html.escape(author)}</b></p>'
            if author else "")
    return (f'<div style="color:{colors.text}; font-size:13.5pt;">{name}'
            f'<p style="white-space:pre-wrap; line-height:140%;">{"".join(pieces)}</p>'
            f'{images}</div>')


def note_document(text: str, *, author: str = "", names: Optional[NameLookup] = None,
                  dark: bool = False, parent=None) -> QTextDocument:
    doc = QTextDocument(parent)
    doc.setDocumentMargin(0)
    doc.setHtml(note_html(text, author=author, names=names, dark=dark))
    return doc


# --------------------------------------------------------------------------- #
# The widget                                                                   #
# --------------------------------------------------------------------------- #

class NostrPreview(QTextBrowser):
    """A read-only view of a note or an article as readers will see it.

    The text sits in a column at most COLUMN_WIDTH wide, centered. Web
    links open in the browser; images come from ``images`` (a lookup that
    answers from what is already at hand, never the network) and show as
    an empty frame otherwise.
    """

    def __init__(self, *, images: Optional[ImageLookup] = None, dark: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._images = images
        self._dark = dark
        self.setOpenExternalLinks(True)
        self.setOpenLinks(True)
        self.setAccessibleName(_("Preview"))
        self._apply_colors()

    def _apply_colors(self) -> None:
        colors = palette(self._dark)
        self.setStyleSheet(f"QTextBrowser {{ background: {colors.background}; "
                           f"color: {colors.text}; border: none; }}")

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self._apply_colors()

    def show_article(self, markdown: str, article: Article, *,
                     names: Optional[NameLookup] = None) -> None:
        self._show(article_document(markdown, article, names=names, dark=self._dark,
                                    parent=self))

    def show_note(self, text: str, *, author: str = "",
                  names: Optional[NameLookup] = None) -> None:
        self._show(note_document(text, author=author, names=names, dark=self._dark,
                                 parent=self))

    def _show(self, doc: QTextDocument) -> None:
        # The document's parent is this view, so its images are asked for
        # through loadResource below; the one it replaces goes.
        previous = self.document()
        self.setDocument(doc)
        # Qt deletes a document the view made itself; one made here goes now.
        if (previous is not doc and shiboken6.isValid(previous)
                and previous.parent() is self):
            previous.deleteLater()
        self._center_column()

    def loadResource(self, kind: int, url: QUrl):
        if kind == QTextDocument.ResourceType.ImageResource.value and self._images:
            image = self._images(url.toString())
            if image is not None and not image.isNull():
                return image
        if kind == QTextDocument.ResourceType.ImageResource.value:
            placeholder = QImage(COLUMN_WIDTH, COLUMN_WIDTH * 9 // 16, QImage.Format.Format_RGB32)
            placeholder.fill(QColor(palette(self._dark).hairline))
            return placeholder
        return super().loadResource(kind, url)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_column()

    def _center_column(self) -> None:
        """Keep the text in a readable column, as the reading apps do, and
        no image wider than it."""
        spare = max(0, self.width() - COLUMN_WIDTH - 32)
        side = spare // 2 + 16
        self.setViewportMargins(side, 16, side, 16)
        column = max(120, min(COLUMN_WIDTH, self.width() - 2 * side - 24))
        self._fit_images(column)

    def _fit_images(self, width: int) -> None:
        doc = self.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                fmt = fragment.charFormat()
                if fragment.isValid() and fmt.isImageFormat():
                    image = fmt.toImageFormat()
                    if int(image.width()) != width:
                        image.setWidth(width)
                        image.clearProperty(QTextFormat.Property.ImageHeight)
                        cursor = QTextCursor(doc)
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(),
                                           QTextCursor.MoveMode.KeepAnchor)
                        cursor.setCharFormat(image)
                it += 1
            block = block.next()
