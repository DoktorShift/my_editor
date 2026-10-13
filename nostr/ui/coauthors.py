# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who writes an article with its author, added and removed the same way
in Article Details and in Publish as Article.

A co-author is chosen with the window's person picker; the author is
never their own co-author, and nobody is added twice. Zaps shared
equally (article_details.equal_zaps) follow the co-authors as they
change; zaps another app shared are left as it set them until changed
in Article Details. A co-author whose profile has no lightning address
can't receive zaps and gets no share: each is asked about once
(``can_receive_zaps``), counted in until answered, and asked again the
next time when no relay answered.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..article_details import ArticleDetails, equal_zaps
from ..profiles import Profile

Coauthor = Tuple[str, str]          # pubkey, relay hint


class Coauthors:
    """The co-author rules both windows follow (see the module docstring).

    ``profile`` gives the author, ``relay_hint`` the relay a person's zap
    is announced on, and ``can_receive_zaps(pubkey, hint, answer)`` answers
    True, False, or None when no relay answered."""

    def __init__(self, *, profile: Callable[[], Optional[Profile]],
                 relay_hint: Callable[[str], str] = lambda pubkey: "",
                 can_receive_zaps: Optional[Callable[[str, str, Callable], None]] = None
                 ) -> None:
        self._profile = profile
        self._relay_hint = relay_hint
        self._can_receive_zaps = can_receive_zaps
        # pubkey -> True, False, or None while asking.
        self._zappable: Dict[str, Optional[bool]] = {}

    # -- changing them ----------------------------------------------------------

    def added(self, details: ArticleDetails, person) -> Optional[ArticleDetails]:
        """``details`` with ``person`` (a Person) a co-author, or None when
        they are one already or are the author."""
        own = self._profile()
        if any(pubkey == person.pubkey for pubkey, _hint in details.coauthors) or (
                own is not None and person.pubkey == own.user_pubkey):
            return None
        return self.changed(details,
                            details.coauthors + ((person.pubkey, person.relay_hint or ""),))

    def removed(self, details: ArticleDetails, pubkey: str) -> ArticleDetails:
        return self.changed(details, tuple(p for p in details.coauthors if p[0] != pubkey))

    def changed(self, details: ArticleDetails,
                coauthors: Sequence[Coauthor]) -> ArticleDetails:
        """``details`` with ``coauthors``: shared zaps follow them (equally);
        zaps another app shared are left as they were."""
        zaps = details.zaps
        if zaps and zaps == self.equal_zaps(details.coauthors):
            zaps = self.equal_zaps(coauthors)
        return details.with_changes(coauthors=tuple(coauthors), zaps=zaps)

    def shared(self, details: ArticleDetails, share: bool) -> ArticleDetails:
        """``details`` with zaps shared equally with the co-authors, or not
        shared."""
        return details.with_changes(zaps=self.equal_zaps(details.coauthors) if share else ())

    def equal_zaps(self, coauthors: Sequence[Coauthor]):
        """Equal shares with the co-authors who can receive zaps (or are not
        known yet not to)."""
        own = self._profile()
        if own is None:
            return ()
        recipients = tuple(person for person in coauthors
                           if self._zappable.get(person[0]) is not False)
        return equal_zaps(own.user_pubkey, self._relay_hint(own.user_pubkey), recipients)

    # -- who can receive zaps ---------------------------------------------------

    def unzappable(self, coauthors: Sequence[Coauthor]) -> List[str]:
        """The co-authors known to have no lightning address."""
        return [pubkey for pubkey, _hint in coauthors if self._zappable.get(pubkey) is False]

    def ask(self, coauthors: Sequence[Coauthor],
            answered: Callable[[str, Optional[bool]], None]) -> None:
        """Ask once whether each co-author can receive zaps (their profile
        names a lightning address); ``answered(pubkey, able)`` follows."""
        if self._can_receive_zaps is None:
            return
        for pubkey, hint in coauthors:
            if pubkey in self._zappable:
                continue
            self._zappable[pubkey] = None
            self._can_receive_zaps(pubkey, hint, lambda able, key=pubkey: answered(key, able))

    def learned(self, details: ArticleDetails, pubkey: str,
                able: Optional[bool]) -> Optional[ArticleDetails]:
        """What ``details`` become now that ``pubkey`` is known to be able
        to receive zaps or not: with equal shares taken back or given, the
        same details when only what is shown changes, or None when nothing
        does. One whose profile couldn't be read (None) keeps their share
        and is asked about again the next time."""
        if able is None:
            self._zappable.pop(pubkey, None)
            return None
        before = self.equal_zaps(details.coauthors)
        self._zappable[pubkey] = able
        if not any(p == pubkey for p, _hint in details.coauthors):
            return None
        if details.zaps and details.zaps == before:
            after = self.equal_zaps(details.coauthors)
            if after != before:
                return details.with_changes(zaps=after)
        return details
