# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many words a text has, and how long it takes to read.

One rule for every place that counts (the status bar, the preview, the
publish dialog), so they never disagree: a word is a run of characters
between spaces that holds at least one letter or digit. A web address or
a Nostr link is one word; Markdown marks standing alone ("-", "#", ">")
and footnote marks ("[^1]") are none.

The status bar counts the words on screen; the preview and the publish
dialog count Markdown, which also holds what the screen does not show
as words: the numbers of a numbered list, a checklist's boxes, the lines
around code, a picture's description. count_markdown_words leaves those
out, so the three agree.

Reading time follows the long-form Nostr readers: 225 words a minute,
rounded up, and at least a minute for anything there is to read.

Languages written without spaces between words (Chinese, Japanese) are
counted by those runs too, so their counts are low; a known limit.
"""

from __future__ import annotations

import math
import re

WORDS_PER_MINUTE = 225

_FOOTNOTE_MARK = re.compile(r"\[\^[^\]\s]+\]:?")
_WORDLIKE = re.compile(r"\w")

# What Markdown holds that the screen shows as no words: the lines around
# code, a list item's marker and checkbox (after any quote marks), and a
# picture (its description and address).
_FENCE = re.compile(r"^[ \t>]*(?:`{3,}|~{3,}).*$", re.MULTILINE)
_ITEM_MARKER = re.compile(r"^([ \t>]*)(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?",
                          re.MULTILINE)
_PICTURE = re.compile(r"!\[[^\]]*\]\((?:<[^>]*>|[^)\s]*)(?:\s+\"[^\"]*\")?\)")


def count_words(text: str) -> int:
    """The number of words in ``text``."""
    text = _FOOTNOTE_MARK.sub(" ", text or "")
    return sum(1 for token in text.split() if _WORDLIKE.search(token))


def count_markdown_words(markdown: str) -> int:
    """The words of Markdown as the screen shows them (see the module
    docstring): the same count the status bar gives the document."""
    text = _FENCE.sub("", markdown or "")
    text = _ITEM_MARKER.sub(r"\1", text)
    return count_words(_PICTURE.sub(" ", text))


def reading_minutes(words: int) -> int:
    """Minutes to read ``words`` words: 0 for none, else at least 1."""
    if words <= 0:
        return 0
    return max(1, math.ceil(words / WORDS_PER_MINUTE))
