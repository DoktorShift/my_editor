# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many words a text has, and how long it takes to read.

One rule for every place that counts (the status bar, the preview, the
publish dialog), so they never disagree: a word is a run of characters
between spaces that holds at least one letter or digit. A web address or
a Nostr link is one word; Markdown marks standing alone ("-", "#", ">")
and footnote marks ("[^1]") are none.

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


def count_words(text: str) -> int:
    """The number of words in ``text``."""
    text = _FOOTNOTE_MARK.sub(" ", text or "")
    return sum(1 for token in text.split() if _WORDLIKE.search(token))


def reading_minutes(words: int) -> int:
    """Minutes to read ``words`` words: 0 for none, else at least 1."""
    if words <= 0:
        return 0
    return max(1, math.ceil(words / WORDS_PER_MINUTE))
