# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic NIP-23 d-tag derivation for feed items.

The same rule as EINUNDZWANZIG STANDUP, so a post imported in either
app gets the same draft identifier and the other app sees it as
already imported instead of creating a second draft:

    sha256(first non-empty of (guid, link, title), each trimmed)[:16]

Exactly one seed value is hashed, never a concatenation. Falling back
through ``link`` and ``title`` makes the identifier stable across CMSs
that regenerate ``guid`` on every render. Existing drafts key on this
derivation; it must not change.

STANDUP derives it in two places, and they agree with this one for
every item that has a guid (RSS ``<guid>``, Atom ``<id>``, a JSON Feed
``id``), which is almost every real feed. tests/test_rss_dtag.py pins
the shared cases with values computed by both of STANDUP's
implementations, and the known differences, which each give a second
draft for those items only:

- no guid and a relative link: STANDUP's server makes the link absolute
  first, its browser code and this one do not;
- no guid and no link: STANDUP's server strips HTML from the title;
- a numeric JSON Feed ``id``: hashed as its digits here and on STANDUP's
  server, while STANDUP's browser code cannot read it;
- a guid of only spaces: skipped here (the link is used), refused by
  STANDUP.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Optional

from i18n import _


class NoIdentifierError(ValueError):
    """Raised when a feed item has no guid, link, or title to hash."""


def derive_identifier(
    *,
    guid: Optional[str] = None,
    link: Optional[str] = None,
    title: Optional[str] = None,
    prefix: Optional[str] = None,
) -> str:
    """Return a 16-char hex identifier for the item.

    The seed is the first value among ``guid``, ``link``, ``title``
    that is not empty once surrounding white space is trimmed.
    ``prefix`` is prepended verbatim to the hash when given.
    """
    seed = next((value for value in (_trimmed(guid), _trimmed(link), _trimmed(title))
                 if value), "")
    if not seed:
        # Shown on the item's row in the import list, so translated.
        raise NoIdentifierError(
            _("Cannot derive identifier: item has no guid, link, or title")
        )
    digest = sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def _trimmed(value) -> str:
    """``value`` as trimmed text; a number (a JSON Feed id) as its digits."""
    if value is None:
        return ""
    return str(value).strip()
