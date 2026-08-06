#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared QTextDocument traversal helpers for the exporters.

The editor stores bullets as literal text: lines prefixed with "• " and
indented in 4-space steps (see HtmlEditor._indent_level_and_has_bullet).
Inline formatting lives on QTextFragment char formats. Every handrolled
exporter (HTML, R Markdown) needs the same walk: blocks in order, each
block as a list of (text, format) runs, with the bullet marker stripped
without dragging its formatting along.

Image fragments carry the object-replacement character (U+FFFC) as text
and a char format where isImageFormat() is true; exporters decide how to
serialize them.
"""

BULLET_MARKER = "• "

# Spaces per bullet nesting level. Mirrors tab_width in editor.py; kept as a
# constant here so exporters never import the widget class.
INDENT_STEP = 4


def iter_blocks(doc):
    """Yield the document's QTextBlocks in order."""
    block = doc.begin()
    while block.isValid():
        yield block
        block = block.next()


def iter_block_runs(block):
    """Yield (text, QTextCharFormat) for each fragment of a block, in order."""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            yield frag.text(), frag.charFormat()
        it += 1


def parse_bullet_line(line: str) -> tuple[int, bool]:
    """Return (leading_spaces, has_bullet) for a block's text.

    Mirrors HtmlEditor._indent_level_and_has_bullet (editor.py) so exporters
    and the editor agree on what counts as a bullet line.
    """
    spaces = 0
    while spaces < len(line) and line[spaces] == ' ':
        spaces += 1
    return spaces, line[spaces:spaces + 2] == BULLET_MARKER


def bullet_depth(spaces: int) -> int:
    """Nesting depth (>= 1) for a bullet with the given leading spaces.

    First-level bullets normally sit at 4 spaces, but Backtab can leave a
    bullet at column 0; both map to depth 1. Ragged indents floor.
    """
    return max(1, spaces // INDENT_STEP)


def skip_prefix(runs, n: int):
    """Drop the first n characters from a list of (text, fmt) runs.

    Used to strip the indentation plus bullet marker ("    • ") from a bullet
    block. The marker may straddle fragment boundaries (e.g. when the user
    toggled bold mid-line), so a straddling run is sliced and any formatting
    on the marker itself is discarded. Runs that become empty are dropped.
    """
    out = []
    remaining = n
    for text, fmt in runs:
        if remaining >= len(text):
            remaining -= len(text)
            continue
        if remaining:
            text = text[remaining:]
            remaining = 0
        if text:
            out.append((text, fmt))
    return out
