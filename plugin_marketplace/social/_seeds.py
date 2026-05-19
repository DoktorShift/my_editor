"""Single source of truth for the marketplace's seed relay list.

Used as a backstop when NIP-65 outbox routing can't resolve an
author's chosen relays. Keeping one helper avoids subtle drift
between the fetcher, publisher, profile fetcher, and mute / follow
caches — each of which used to maintain its own copy of the same
union logic.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from nostr import DEFAULT_RELAYS


def seed_relays(extra: Iterable[str] = ()) -> List[str]:
    """Return the deduped, order-preserving seed set.

    ``extra`` (typically a user's curated overrides) takes precedence
    so their explicit choices come first; the project default fills
    coverage gaps.
    """
    seen: Dict[str, None] = {}
    for url in extra or ():
        if isinstance(url, str) and url.strip():
            seen.setdefault(url, None)
    for url in DEFAULT_RELAYS:
        seen.setdefault(url, None)
    return list(seen.keys())
