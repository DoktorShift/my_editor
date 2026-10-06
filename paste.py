# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a paste brings into a document that holds Markdown structure.

Copied from a web page, Google Docs, Word, Pages or Notes, text arrives as
HTML with its fonts, sizes and colors; copied from a Markdown editor, as
plain text full of Markdown. Either way only what Markdown can say comes
in: headings, bold, italic, strikethrough, code, links, lists and
checklists, quotes, code blocks, tables, dividers and pictures on the
web. Nothing else can come in, by construction: the HTML is read by Qt,
tidied (below), written by the one Markdown writer (markdown_writer.py)
and read back the way a .md file is opened. Colors, fonts and sizes have
no Markdown, so they never arrive.

Qt reads the HTML rather than an HTML-to-Markdown converter because Qt
applies the inline styles that Google Docs and Word use for bold and
italic (``<span style="font-weight:700">``, inside a ``<b>`` that turns
bold off), which a converter that looks only at tags gets backwards.

Tidying what Qt read:

- Word marks list paragraphs with a style of its own instead of HTML
  lists; they become lists.
- A ``<pre>`` block, and lines that keep their spacing (VS Code), are a
  code block, with the language the page names in its classes.
- A checkbox at the start of a list item makes it a checklist item.
- Words the page sets in a monospace font are code.
- A link that would not work in a published article (a path on the site
  it came from, a script) keeps its words without the link.
- A picture inside the clipboard or on a disk cannot come along; it is
  counted, so the person can be told.

Text copied in this editor carries its Markdown (``text/markdown``), so
copying and pasting here keeps everything exactly. VS Code says which
language it copied: Markdown is read as Markdown, code becomes code.

Plain text is read as Markdown only when it clearly is Markdown (two
signs of it, such as a "# " heading and a "- " list); otherwise the
editor inserts it as it is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import List, Optional, Tuple

from PySide6.QtCore import QMimeData
from PySide6.QtGui import (
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
    QTextImageFormat,
)

from doc_walk import iter_blocks
from link_url import normalize_link_input
from markdown_writer import READ_FEATURES, document_to_markdown, image_markdown
import rich_text

MARKDOWN_MIME = "text/markdown"
VSCODE_MIME = "vscode-editor-data"

# --------------------------------------------------------------------------- #
# Plain text that is Markdown                                                  #
# --------------------------------------------------------------------------- #

# Signs that plain text is Markdown: block marks at a line start, and
# inline marks anywhere.
_LINE_SIGNS = re.compile(
    r"^(?:#{1,6} \S|[-*+] \S|\d{1,9}[.)] \S|> |```|~~~|\|.*\|[ \t]*$)", re.MULTILINE)
_INLINE_SIGNS = (re.compile(r"\*\*[^*\n]+\*\*"), re.compile(r"\[[^\]\n]+\]\([^)\s]+\)"),
                 re.compile(r"`[^`\n]+`"), re.compile(r"~~[^~\n]+~~"))


def looks_like_markdown(text: str) -> bool:
    """Whether plain text carries at least two signs of Markdown."""
    text = text or ""
    signs = len(_LINE_SIGNS.findall(text))
    if signs >= 2:
        return True
    signs += sum(1 for pattern in _INLINE_SIGNS if pattern.search(text))
    return signs >= 2


# --------------------------------------------------------------------------- #
# Tidying the HTML before Qt reads it                                          #
# --------------------------------------------------------------------------- #

# Marks put into the HTML for what Qt would forget, found again in what it
# read (characters of Unicode's private use area, which no page contains).
_CODE_START = ""         # a code block starts here; its language follows
_CODE_END = ""           # ... up to here
_CHECKED = ""            # a ticked checkbox
_UNCHECKED = ""          # an empty checkbox
_MARKS = re.compile("[-]")

_PRE = re.compile(r"(<pre\b[^>]*>)(\s*<code\b[^>]*>)?", re.IGNORECASE)
_LANGUAGE = re.compile(
    r"(?:\b(?:language|lang)-|\bbrush:\s*|\bdata-lang(?:uage)?=[\"']?)([A-Za-z0-9_#+.-]+)",
    re.IGNORECASE)
# GitHub names the language on the box around the <pre>.
_GITHUB_LANGUAGE = re.compile(r"highlight-source-([A-Za-z0-9_#+-]+)[^<>]*>\s*$",
                              re.IGNORECASE)
_CHECKBOX = re.compile(r"<input\b[^>]*\btype=[\"']?checkbox\b[^>]*>", re.IGNORECASE)
_CHECKED_ATTRIBUTE = re.compile(r"\bchecked\b", re.IGNORECASE)

# Word: <p style='mso-list:l0 level2 lfo1'><![if !supportLists]>1.<![endif]>Text</p>
_WORD_ITEM = re.compile(
    r"<p\b[^>]*\bmso-list:\s*l\d+\s+level(?P<level>\d+)[^>]*>(?P<body>.*?)</p>",
    re.IGNORECASE | re.DOTALL)
_WORD_MARKER = re.compile(
    r"<!(?:--)?\[if !supportLists\](?:--)?>(?P<marker>.*?)<!(?:--)?\[endif\](?:--)?>",
    re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_ORDERED_MARKER = re.compile(r"^\(?[0-9A-Za-z]{1,5}[.)]$")


def _language_of(text: str) -> str:
    found = _LANGUAGE.search(text)
    return found.group(1).lower()[:30] if found else ""


def _marked_code_blocks(html: str) -> str:
    def mark(match):
        before = html[max(0, match.start() - 400):match.start()]
        github = _GITHUB_LANGUAGE.search(before)
        # The <code> inside names the language most precisely
        # (language-python), the <pre> often more briefly (lang-py).
        language = (_language_of(match.group(2) or "") or _language_of(match.group(1))
                    or (github.group(1).lower()[:30] if github else ""))
        return match.group(0) + _CODE_START + language + _CODE_END
    return _PRE.sub(mark, html)


def _marked_checkboxes(html: str) -> str:
    return _CHECKBOX.sub(
        lambda m: _CHECKED if _CHECKED_ATTRIBUTE.search(m.group(0)) else _UNCHECKED, html)


def _word_lists(html: str) -> str:
    """Word's list paragraphs (a style naming the level, and the bullet or
    number typed in front) as HTML lists."""
    if "mso-list" not in html:
        return html
    out: List[str] = []
    open_lists: List[str] = []
    position = 0
    for item in _WORD_ITEM.finditer(html):
        between = html[position:item.start()]
        if open_lists and between.strip():
            # Something else came in between: the list ended.
            out.extend(f"</{kind}>" for kind in reversed(open_lists))
            open_lists.clear()
        out.append(between)
        body = item.group("body")
        marker = ""
        typed = _WORD_MARKER.search(body)
        if typed:
            marker = unescape(_TAG.sub("", typed.group("marker"))).replace("\xa0", " ").strip()
            body = body[:typed.start()] + body[typed.end():]
        kind = "ol" if _ORDERED_MARKER.match(marker) else "ul"
        level = max(1, min(int(item.group("level")), rich_text.MAX_LIST_DEPTH))
        while len(open_lists) > level:
            out.append(f"</{open_lists.pop()}>")
        if len(open_lists) == level and open_lists[-1] != kind:
            out.append(f"</{open_lists.pop()}>")
        while len(open_lists) < level:
            out.append(f"<{kind}>")
            open_lists.append(kind)
        out.append(f"<li>{body}</li>")
        position = item.end()
    out.extend(f"</{kind}>" for kind in reversed(open_lists))
    out.append(html[position:])
    return "".join(out)


class _InertDocument(QTextDocument):
    """A document that never reads a file or anything else an HTML page
    names (style sheets, pictures): what it holds is only what was pasted."""

    def loadResource(self, _type, _name):   # noqa: N802 (Qt's name)
        return None


# --------------------------------------------------------------------------- #
# Tidying what Qt read                                                         #
# --------------------------------------------------------------------------- #

def _strip_mark(block, length: int) -> None:
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + length, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()


def _code_runs(doc) -> List[Tuple[str, list]]:
    """The code blocks Qt read: runs of lines that keep their spacing, each
    with the language its mark names."""
    runs: List[Tuple[str, list]] = []
    current: Optional[Tuple[str, list]] = None
    for block in iter_blocks(doc):
        text = block.text()
        if text.startswith(_CODE_START):
            end = text.find(_CODE_END)
            language = text[1:end] if end > 0 else ""
            _strip_mark(block, end + 1 if end > 0 else 1)
            current = (language, [block])
            runs.append(current)
        elif block.blockFormat().nonBreakableLines():
            if current is None:
                current = ("", [])
                runs.append(current)
            current[1].append(block)
        else:
            current = None
    return runs


def _make_code_blocks(doc, language: str) -> None:
    for run_language, blocks in _code_runs(doc):
        while blocks and not blocks[0].text().strip():
            blocks.pop(0)
        while blocks and not blocks[-1].text().strip():
            blocks.pop()
        for block in blocks:
            fmt = block.blockFormat()
            fmt.setProperty(QTextFormat.Property.BlockCodeFence, "`")
            fmt.setProperty(QTextFormat.Property.BlockCodeLanguage, run_language or language)
            QTextCursor(block).setBlockFormat(fmt)


def _make_checklists(doc) -> None:
    for block in iter_blocks(doc):
        text = block.text()
        stripped = text.lstrip()
        if block.textList() is None or not stripped or stripped[0] not in (_CHECKED,
                                                                           _UNCHECKED):
            continue
        skip = len(text) - len(stripped) + 1
        if text[skip:skip + 1] == " ":
            skip += 1
        fmt = block.blockFormat()
        fmt.setMarker(QTextBlockFormat.MarkerType.Checked if stripped[0] == _CHECKED
                      else QTextBlockFormat.MarkerType.Unchecked)
        QTextCursor(block).setBlockFormat(fmt)
        _strip_mark(block, skip)


def _is_monospace(fmt: QTextCharFormat) -> bool:
    """Whether the page sets these words in a monospace font: it names the
    generic family, as every code element and code font stack does."""
    return any(family.strip().strip("'\"").lower() in ("monospace", "ui-monospace")
               for family in (fmt.fontFamilies() or []))


def _kept_href(href: str) -> Optional[str]:
    """Where a pasted link may go: the web, an email, Nostr. A path on the
    site it came from, a place on that page or a script would not work
    (or would be harmful) where the article is read."""
    if not href.lower().startswith(("http://", "https://", "mailto:", "nostr:")):
        return None
    address, _reason = normalize_link_input(href, nostr=True)
    return address


def _tidy_text(doc) -> None:
    """Inline code where the page used a monospace font (the words, not the
    spaces around them); links that cannot work where the article is read
    lose their link, never their words."""
    edits = []
    code = []
    for block in iter_blocks(doc):
        if block.blockFormat().nonBreakableLines():
            continue
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            it += 1
            if not fragment.isValid():
                continue
            fmt = fragment.charFormat()
            if fmt.isImageFormat():
                continue
            changed = QTextCharFormat(fmt)
            words = fragment.text()
            if _is_monospace(fmt) and words.strip():
                lead = len(words) - len(words.lstrip())
                code.append((fragment.position() + lead, len(words.strip())))
            if fmt.isAnchor():
                href = _kept_href(fmt.anchorHref())
                if href is None:
                    changed = rich_text.without_link(changed)
                elif href != fmt.anchorHref():
                    changed.setAnchorHref(href)
            if changed != fmt:
                edits.append((fragment.position(), fragment.length(), changed))
    for position, length, fmt in edits:
        cursor = QTextCursor(doc)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(fmt)
    for position, length in code:
        cursor = QTextCursor(doc)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(rich_text.style_format(rich_text.CODE, True))


def markdown_from_html(html: str, *, language: str = "") -> Tuple[str, int]:
    """HTML as the Markdown it can be, and how many pictures had to be left
    out. ``language`` is the code language when the source named one for
    all of it (VS Code)."""
    prepared = _marked_checkboxes(_marked_code_blocks(_word_lists(html or "")))
    doc = _InertDocument()
    doc.setHtml(prepared)
    _make_code_blocks(doc, language)
    _make_checklists(doc)
    _tidy_text(doc)
    left_out = 0

    def web_pictures_only(fmt: QTextImageFormat) -> Optional[str]:
        nonlocal left_out
        name = fmt.name()
        if not name.lower().startswith(("https://", "http://")):
            left_out += 1
            return None
        return image_markdown(str(fmt.property(QTextImageFormat.Property.ImageAltText) or ""),
                              name)

    markdown = document_to_markdown(doc, web_pictures_only)
    return _MARKS.sub("", markdown), left_out


# --------------------------------------------------------------------------- #
# What a paste holds                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class Pasted:
    """The document a paste brings in, and how many pictures it had to
    leave out."""
    document: QTextDocument
    left_out: int = 0


def document_from_markdown(markdown: str) -> QTextDocument:
    """Markdown read the way a .md file is opened."""
    doc = QTextDocument()
    doc.setMarkdown(markdown, READ_FEATURES)
    rich_text.normalize_after_markdown_load(doc)
    return doc


def _inline_code(text: str) -> QTextDocument:
    doc = QTextDocument()
    QTextCursor(doc).insertText(text, rich_text.style_format(rich_text.CODE, True))
    return doc


def _fenced(text: str, language: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text}\n{fence}\n"


def _vscode_language(source: QMimeData) -> Optional[str]:
    """The language VS Code says it copied, or None when it did not."""
    if not source.hasFormat(VSCODE_MIME):
        return None
    try:
        data = json.loads(bytes(source.data(VSCODE_MIME)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    mode = data.get("mode") if isinstance(data, dict) else None
    return mode.lower()[:30] if isinstance(mode, str) else None


def plain_text(source: QMimeData) -> str:
    """The paste as plain text: its text, or the words of its HTML when
    it came without (a paste into a code block, Paste and Match Style)."""
    if source.hasText() and source.text():
        return source.text()
    if source.hasHtml():
        doc = _InertDocument()
        doc.setHtml(source.html())
        return doc.toPlainText()
    return ""


def from_mime(source: QMimeData) -> Optional[Pasted]:
    """What a paste brings in, or None when it is plain text to insert as
    it is."""
    if source.hasFormat(MARKDOWN_MIME):
        markdown = bytes(source.data(MARKDOWN_MIME)).decode("utf-8", "replace")
        return Pasted(document_from_markdown(markdown))
    text = source.text() if source.hasText() else ""
    language = _vscode_language(source)
    if language is not None and text.strip():
        if language in ("markdown", "mdx"):
            return Pasted(document_from_markdown(text))
        if language == "plaintext":
            return None
        code = text.strip("\n")
        if "\n" not in code:
            return Pasted(_inline_code(code))
        return Pasted(document_from_markdown(_fenced(code, language)))
    if source.hasHtml():
        markdown, left_out = markdown_from_html(source.html())
        return Pasted(document_from_markdown(markdown), left_out)
    if looks_like_markdown(text):
        return Pasted(document_from_markdown(text))
    return None


def selection_markdown(cursor: QTextCursor) -> str:
    """The selection as Markdown, for the clipboard: pictures keep their
    names, so they are found again when pasted into this document."""
    doc = QTextDocument()
    QTextCursor(doc).insertFragment(cursor.selection())
    started_in = cursor.document().findBlock(cursor.selectionStart())
    if started_in.blockFormat().headingLevel() and not doc.begin().blockFormat().headingLevel():
        # Words from inside a heading: its size and weight are the
        # heading's, not theirs.
        rich_text.set_heading(QTextCursor(doc.begin()), rich_text.BODY)

    def keep_pictures(fmt: QTextImageFormat) -> str:
        return image_markdown(str(fmt.property(QTextImageFormat.Property.ImageAltText) or ""),
                              fmt.name())

    return document_to_markdown(doc, keep_pictures)
