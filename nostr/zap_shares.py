# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How the zaps an article receives are shared, in the whole percentages
people set them in (NIP-57 zap splits, Appendix G).

An article names the people a zap to it is shared between, one
``("zap", <pubkey>, <relay>, <weight>)`` tag each, and readers' apps that
support zap splits pay every one of them their share. Here the shares
are whole percentages that add up to 100: the author keeps what the
others leave, so the author's share is never set and can't drift.
Weights another app wrote are read as percentages of their total.

The same model as Standup's editor (src/utils/zapSplit.js), so an
article split in one reads the same in the other.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

ZapTag = Tuple[str, ...]

_PUBKEY = re.compile(r"^[0-9a-f]{64}$")


def _weight(value) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 0.0
    return weight if math.isfinite(weight) and weight > 0 else 0.0


def read_zaps(zaps: Iterable[Sequence[str]]) -> List[Tuple[str, str, float]]:
    """``[(pubkey, relay, weight)]`` named by zap tags, in their order, by
    the appendix's rules: without weights everyone shares equally; with
    some, the unweighted get nothing; a weight of 0 or less pays nobody; a
    person named twice gets both weights."""
    named = [tag for tag in zaps
             if len(tag) >= 2 and tag[0] == "zap" and isinstance(tag[1], str)
             and _PUBKEY.match(tag[1].lower())]
    weighted = any(len(tag) >= 4 for tag in named)
    people: Dict[str, Tuple[str, float]] = {}
    for tag in named:
        weight = _weight(tag[3]) if len(tag) >= 4 else (0.0 if weighted else 1.0)
        if not weight:
            continue
        pubkey = tag[1].lower()
        relay, before = people.get(pubkey, ("", 0.0))
        people[pubkey] = (relay or (tag[2] if len(tag) >= 3 else ""), before + weight)
    return [(pubkey, relay, weight) for pubkey, (relay, weight) in people.items()]


def percentages(weights: Sequence[float]) -> List[int]:
    """Whole percentages for ``weights`` that add up to 100, the rest of the
    division going to the largest remainders (as Standup's splitPercents)."""
    total = sum(weights)
    if total <= 0:
        return [0 for _ in weights]
    exact = [100 * weight / total for weight in weights]
    whole = [math.floor(value) for value in exact]
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - whole[i]), i))
    for i in order[:100 - sum(whole)]:
        whole[i] += 1
    return whole


def shares_of(zaps: Iterable[Sequence[str]], author: str) -> Dict[str, int]:
    """Everyone but ``author`` a zap pays, with their percentage."""
    named = read_zaps(zaps)
    percents = percentages([weight for _pubkey, _relay, weight in named])
    return {pubkey: share for (pubkey, _relay, _weight), share in zip(named, percents)
            if pubkey != author}


def relays_of(zaps: Iterable[Sequence[str]]) -> Dict[str, str]:
    """The relay each person a zap pays is named with."""
    return {pubkey: relay for pubkey, relay, _weight in read_zaps(zaps)}


def author_share(shares: Mapping[str, int]) -> int:
    """What the author keeps: 100 % less everyone else's share."""
    return max(0, 100 - sum(shares.values()))


def most_for(shares: Mapping[str, int], pubkey: str) -> int:
    """The largest share ``pubkey`` can have: what the others leave."""
    return 100 - sum(share for person, share in shares.items() if person != pubkey)


def with_share(shares: Mapping[str, int], pubkey: str, share: int) -> Dict[str, int]:
    """``shares`` with ``pubkey``'s set to ``share``, held within what the
    others leave."""
    changed = dict(shares)
    changed[pubkey] = max(0, min(most_for(shares, pubkey), int(round(share))))
    return changed


def evenly(pubkeys: Sequence[str]) -> Dict[str, int]:
    """Everyone and the author an equal share; the author keeps the rest."""
    each = 100 // (len(pubkeys) + 1)
    return {pubkey: each for pubkey in pubkeys}


def zap_tags(shares: Mapping[str, int], author: str, relays: Mapping[str, str],
             fallback_relay: str = "") -> Tuple[ZapTag, ...]:
    """The zap tags for ``shares``: the author first with what they keep,
    then everyone with a share, in order; none while the author keeps
    everything, as every app pays by default. Every tag names a relay
    where its person can be found (some apps read a tag without one as
    weight 0): theirs, else ``fallback_relay``."""
    shared = [(pubkey, share) for pubkey, share in shares.items()
              if share > 0 and pubkey != author]
    if not shared:
        return ()
    own = author_share(shares)
    people = ([(author, own)] if own > 0 and author else []) + shared
    return tuple(("zap", pubkey, relays.get(pubkey) or fallback_relay, str(share))
                 for pubkey, share in people)
