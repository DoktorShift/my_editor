# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""NIP-54 wiki (kind 30818) content normalisation.

The content field of a wiki article has drifted across sources that
disagree: the current NIP-54 spec says Djot, older references say
AsciiDoc, and real kind-30818 events in the wild are plain Markdown.
Djot and Markdown share the syntax for everything that matters here, so
the body is treated as Markdown and only the two constructs that are
NOT valid Markdown are rewritten, leaving the common case untouched:

1. Wikilinks. Legacy ``[[Target]]`` / ``[[target|display]]`` and the
   Djot empty-reference form ``[display][]`` point at another wiki
   article, which has no portable URL in an imported draft, so the
   human display text is kept and the link mechanics dropped. A
   ``[display][]`` whose reference the body defines
   (``[display]: https://...``) is a Markdown link and stays one.
2. AsciiDoc section headings (``== Section``): a leading run of ``=``
   then a space is never valid Markdown or Djot, so mapping it to ``#``
   recovers legacy articles without disturbing Markdown bodies.

Deliberately conservative: ambiguous inline markup (``*word*`` is bold
in AsciiDoc but italic in Markdown) is left as-is rather than guessed
at. Code (fenced with backticks or tildes, and inline code spans) is
protected from every rewrite.
"""

from __future__ import annotations

import re

_WIKILINK_PIPE_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")  # [[target|display]]
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")                   # [[Target]]
_DJOT_WIKILINK_RE = re.compile(r"\[([^\]]+)\]\[\]")              # [display][]
# Horizontal whitespace only; \s would swallow the trailing newline
# (and the blank line after the heading) in multiline mode.
_ASCIIDOC_HEADING_RE = re.compile(
    r"^(={1,6})[ \t]+(.+?)[ \t]*=*[ \t]*$", re.MULTILINE)
# Code: a fence of backticks or tildes (to its closing fence, or to the
# end of an unclosed one), or an inline code span.
_CODE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)"
    r"|(?<!`)(`+)(?!`)[^\n]+?(?<!`)\2(?!`)",
    re.MULTILINE)
# A reference definition: "[label]: destination".
_REFERENCE_DEF_RE = re.compile(r"^[ ]{0,3}\[([^\]]+)\]:[ \t]*\S", re.MULTILINE)


def _label(text: str) -> str:
    """A reference label as Markdown matches it: case and runs of
    whitespace do not count."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_wiki_content(content: str) -> str:
    """Normalise a kind-30818 wiki body to clean Markdown."""
    text = str(content or "")

    # Protect code from every rewrite below.
    fences: list[str] = []

    def _stash(match: re.Match) -> str:
        fences.append(match.group(0))
        return f"\x00F{len(fences) - 1}\x00"

    text = _CODE_RE.sub(_stash, text)
    defined = {_label(m.group(1)) for m in _REFERENCE_DEF_RE.finditer(text)}

    text = _WIKILINK_PIPE_RE.sub(lambda m: m.group(2).strip(), text)
    text = _WIKILINK_RE.sub(lambda m: m.group(1).strip(), text)
    text = _DJOT_WIKILINK_RE.sub(
        lambda m: m.group(0) if _label(m.group(1)) in defined else m.group(1), text)
    text = _ASCIIDOC_HEADING_RE.sub(
        lambda m: f"{'#' * len(m.group(1))} {m.group(2).strip()}", text)

    text = re.sub(
        r"\x00F(\d+)\x00", lambda m: fences[int(m.group(1))], text)
    return text.strip()
