# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""References to people, notes and articles in what is written.

A reference is a ``nostr:`` link (NIP-21) to a NIP-19 entity: a person
(``npub``, ``nprofile``), a note (``note``, ``nevent``) or an article
(``naddr``). Written in a note or an article, it is how Nostr mentions
someone or quotes something (NIP-27); readers turn it into a name or a
card. This module is the one place that finds them and reads them:

- :func:`normalize_reference` reads what someone pasted to quote a note
  or an article (a ``nostr:`` link, a bare ``nevent1…``, or the web
  address of a reader that shows one: ``https://njump.me/nevent1…``,
  ``https://primal.net/e/note1…``, ``https://yakihonne.com/article/naddr1…``)
  and says in plain words why something is not one;
- :func:`find_references` finds them in a note or an article, outside
  code (code shows a reference as text);
- :func:`quote_tags` makes the tags a published event carries for the
  notes and articles it quotes: a ``q`` tag each (NIP-18), and a ``p``
  tag for each quoted author, so they hear about it.

People mentioned are tagged by ``publisher.extract_inline_mentions``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from i18n import _

from . import bech32
from .outbox.policy import normalize_relay_url

PERSON = "person"
NOTE = "note"
ARTICLE = "article"

# An entity inside anything that names one: a nostr: link, a bare entity,
# a reader's web address (after a slash, a colon, a hash, a question mark,
# an equals sign, or a space).
_ENTITY_IN = re.compile(
    r"(?:^|[/:#?=\s])((?:note|nevent|naddr|npub|nprofile)1[02-9ac-hj-np-z]{6,})", re.IGNORECASE)
# A reference in written text.
_REFERENCE = re.compile(r"nostr:((?:npub|nprofile|note|nevent|naddr)1[02-9ac-hj-np-z]+)")
# A Nostr secret key in written text, plain or encrypted (NIP-49).
SECRET_KEY = re.compile(r"\b(?:nsec|ncryptsec)1[02-9ac-hj-np-z]{20,}", re.IGNORECASE)


@dataclass(frozen=True)
class Reference:
    """One reference, read: what it points at and the hints it carries."""

    kind: str                  # PERSON, NOTE or ARTICLE
    entity: str                # the NIP-19 entity, as written (lowercase)
    pubkey: str = ""           # the person, or the author when known (hex)
    event_id: str = ""         # a note's id (hex)
    coordinate: str = ""       # an article's address: "30023:<pubkey>:<identifier>"
    relays: Tuple[str, ...] = ()

    @property
    def uri(self) -> str:
        return "nostr:" + self.entity

    @property
    def relay(self) -> str:
        """The first relay hint that is a relay address, or ""."""
        for relay in self.relays:
            normal = normalize_relay_url(relay)
            if normal:
                return normal
        return ""


def read_entity(entity: str) -> Optional[Reference]:
    """A NIP-19 entity (``npub1…``, ``nevent1…``, ...) read, or None when
    it does not decode."""
    entity = (entity or "").strip().lower()
    try:
        if entity.startswith("npub1"):
            return Reference(PERSON, entity, pubkey=bech32.decode_npub(entity))
        if entity.startswith("nprofile1"):
            pubkey, relays = bech32.decode_nprofile(entity)
            return Reference(PERSON, entity, pubkey=pubkey, relays=tuple(relays))
        if entity.startswith("note1"):
            return Reference(NOTE, entity, event_id=bech32.decode_note(entity))
        if entity.startswith("nevent1"):
            event_id, relays, author, _kind = bech32.decode_nevent(entity)
            return Reference(NOTE, entity, pubkey=author or "", event_id=event_id,
                             relays=tuple(relays))
        if entity.startswith("naddr1"):
            identifier, author, kind, relays = bech32.decode_naddr(entity)
            return Reference(ARTICLE, entity, pubkey=author,
                             coordinate=f"{kind}:{author}:{identifier}", relays=tuple(relays))
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
    return None


def normalize_reference(raw: str) -> Tuple[Optional[Reference], str]:
    """``(reference, "")`` for a pasted link to a note or an article, or
    ``(None, reason)`` with a sentence to show next to the field."""
    text = (raw or "").strip()
    if not text:
        return None, _("Paste a link to a note or an article.")
    found = _ENTITY_IN.search(text)
    if found is None:
        return None, _("That is not a link to a note or an article on Nostr.")
    reference = read_entity(found.group(1))
    if reference is None:
        return None, _("That link is damaged: a part of it is missing or mistyped.")
    if reference.kind == PERSON:
        return None, _("That is a link to a person. To mention someone, type @ and "
                       "their name.")
    return reference, ""


def short_key(npub: str) -> str:
    """``npub1abcd…wxyz``: a key short enough to read."""
    return f"{npub[:9]}\u2026{npub[-4:]}" if len(npub) > 16 else npub


def person_name(entity: str, names: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """How a person a reference names is called: their name when ``names``
    (pubkey -> name or None) knows it, else their short key; the entity
    itself when it does not decode."""
    reference = read_entity(entity)
    if reference is None or reference.kind != PERSON:
        return entity
    name = names(reference.pubkey) if names else None
    return name or short_key(bech32.encode_npub(reference.pubkey))


def outside_code(markdown: str, change: Callable[[str], str]) -> str:
    """Apply ``change`` to the Markdown outside fenced code blocks and
    inline code, which show references as they are."""
    out = []
    fence = None
    for line in markdown.split("\n"):
        stripped = line.lstrip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            out.append(line)
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append(line)
            continue
        parts = re.split(r"(`+[^`]*`+)", line)
        out.append("".join(p if p.startswith("`") else change(p) for p in parts))
    return "\n".join(out)


def find_references(content: str, *, markdown: bool = True) -> List[Reference]:
    """Every reference in ``content``, in order, each once: outside code
    in Markdown (an article), anywhere in plain text (a note)."""
    found: List[Reference] = []
    seen = set()

    def collect(text: str) -> str:
        for match in _REFERENCE.finditer(text):
            entity = match.group(1).lower()
            if entity in seen:
                continue
            seen.add(entity)
            reference = read_entity(entity)
            if reference is not None:
                found.append(reference)
        return text

    if markdown:
        outside_code(content or "", collect)
    else:
        collect(content or "")
    return found


def quote_tags(references: Iterable[Reference],
               known_p: Sequence[str] = ()) -> List[List[str]]:
    """The tags for the notes and articles ``references`` quote: a ``q``
    tag each, with a relay hint and, for a note, its author (NIP-18), then
    a ``p`` tag for each quoted author not in ``known_p`` (the people
    already tagged), so they hear about it. Each quoted thing once."""
    q_tags: List[List[str]] = []
    p_tags: List[List[str]] = []
    quoted = set()
    tagged = set(known_p)
    for reference in references:
        if reference.kind == NOTE and reference.event_id:
            target = reference.event_id
            tag = ["q", target, reference.relay]
            if reference.pubkey:
                tag.append(reference.pubkey)
        elif reference.kind == ARTICLE and reference.coordinate:
            target = reference.coordinate
            tag = ["q", target, reference.relay]
        else:
            continue
        if target in quoted:
            continue
        quoted.add(target)
        q_tags.append(tag)
        if reference.pubkey and reference.pubkey not in tagged:
            tagged.add(reference.pubkey)
            p_tags.append(["p", reference.pubkey] + ([reference.relay]
                                                      if reference.relay else []))
    return q_tags + p_tags


def public_reference_for(secret: str) -> Optional[str]:
    """The ``nostr:`` link to the person a plain secret key belongs to, or
    None (an encrypted key, a damaged one)."""
    key = (secret or "").strip().lower()
    if not key.startswith("nsec1"):
        return None
    try:
        secret_hex = bech32.decode_nsec(key)
        from .crypto import get_public_key
        public = get_public_key(bytes.fromhex(secret_hex))
    except (ValueError, TypeError):
        return None
    return "nostr:" + bech32.encode_npub(public.hex())


def without_secret_keys(text: str) -> Optional[str]:
    """``text`` with every plain secret key replaced by the link to its
    person, or None when one cannot be (an encrypted key)."""
    out = text
    for key in secret_keys_in(text):
        public = public_reference_for(key)
        if public is None:
            return None
        out = out.replace(key, public)
    return out


def secret_keys_in(text: str) -> List[str]:
    """The Nostr secret keys written in ``text`` (plain or encrypted),
    each once, in order."""
    found: List[str] = []
    for match in SECRET_KEY.finditer(text or ""):
        key = match.group(0)
        if key not in found:
            found.append(key)
    return found
