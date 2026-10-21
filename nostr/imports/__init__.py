# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Universal source importer.

The extensible half of the import feature. ``nostr/rss/`` stays a pure
feed-format library (parsing, d-tag derivation, HTML-to-Markdown
normalisation, URL discovery helpers); this package owns everything
around it:

- ``errors``     stable error codes + friendly user-facing copy
- ``fetch``      the shared Qt network fetcher (size-capped, charset
                 sniffing, bounded redirects)
- ``registry``   the source-resolver registry: ``detect`` + ``resolve``
                 per source kind, everything downstream consumes one
                 normalised ``Feed`` shape
- ``resolvers``  one module per source kind, RSS as the catch-all

The central contract: input -> detect() -> resolver -> normalised Feed
-> preview -> filtering -> import pipeline -> article creation. Adding
a new source kind is one resolver module + one registry entry + tests;
the pipeline and UI never change for it.

What every source gets without doing anything for it, so a new one
only reads its format:

- its HTML made ready for Markdown (``nostr/rss/clean.py``): lazy
  pictures, embeds, video and audio, code languages, page furniture,
  tracking pixels;
- its details made fit to publish (``nostr/rss/normalize.py``): a plain
  summary (the opening words when there is none), hashtags without
  spaces, a whole cover address, the author in the attribution line;
- at import, what the post's page states when the source left it out
  (``page_meta.py``), and its whole text when it gave a teaser.

So a source module hands over the post as its source holds it: the
body as the HTML its page shows (expand what only the platform renders,
as ``sources/wxr.py`` does for WordPress shortcodes), the summary and
title as written, every detail the format has (date, tags, cover,
author, link). It never fixes up HTML or details itself.
"""

from .errors import ERROR_CODES, SourceError, friendly_message
from .registry import (
    ResolveInput,
    ResolveResult,
    can_resolve_source,
    detect_resolver,
    resolve_source,
)

__all__ = [
    "ERROR_CODES",
    "SourceError",
    "friendly_message",
    "ResolveInput",
    "ResolveResult",
    "can_resolve_source",
    "detect_resolver",
    "resolve_source",
]
