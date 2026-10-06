# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a typed or pasted link address means, and whether it may be one.

The Add Link popover hands over whatever the person typed; this module
turns it into the address a reader's app will open, or says in plain
words why it cannot be a link:

- a web address, with ``https://`` added when it was left out
  (``example.com/page``);
- an email address, which becomes ``mailto:``;
- a Nostr link (``nostr:npub1…``, or the bare ``npub1…``, ``note1…``,
  ``nevent1…``, ``naddr1…``, ``nprofile1…``);
- ``#name``, a place in the same document (a footnote).

Anything that runs code or reaches into the reader's own computer
(``javascript:``, ``data:``, ``file:``, ``vbscript:``) is refused, as is
any other scheme: what is published opens on strangers' devices. A web
address is held to the same rule the app opens links by
(url_safety.is_safe_external_url): no name or password in it (the
classic disguise ``https://good.example@evil.example/``, and a password
published for everyone) and a port that can be one.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlsplit

import url_safety
from i18n import _

_NOSTR_ENTITY = re.compile(r"(?:npub|nprofile|note|nevent|naddr)1[02-9ac-hj-np-z]{6,}")
_EMAIL = re.compile(r"[^@\s:/<>()\[\]]+@[^@\s:/<>()\[\]]+\.[A-Za-z]{2,}")
_DOMAIN = re.compile(r"(?:[\w-]+\.)+[A-Za-z][\w-]*(?::\d{1,5})?(?:[/?#]\S*)?", re.UNICODE)
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_DANGEROUS = {"javascript", "data", "file", "vbscript"}


def _ask_for(nostr: bool) -> str:
    if nostr:
        return _("Enter a web address, an email address or a Nostr link.")
    return _("Enter a web address or an email address.")


def _web_address_problem(address: str) -> str:
    """Why a web address cannot be a link, or "" when it can: the rule
    links are opened by, said in plain words."""
    parts = urlsplit(address)
    if "@" in parts.netloc:
        return _("An address with a name or password in it cannot be a link.")
    try:
        parts.port
    except ValueError:
        return _("That address has a port number that cannot be.")
    if not url_safety.is_safe_external_url(address):
        return _("That address is missing the name of the website.")
    return ""


def normalize_link_input(raw: str, *, nostr: bool = False) -> Tuple[Optional[str], str]:
    """``(address, "")`` for something that can be a link, or
    ``(None, reason)`` with a sentence to show next to the field.

    A Nostr link is accepted either way; ``nostr`` (a Nostr account in
    use) only decides whether the reasons mention Nostr links, which
    mean nothing to someone who never chose Nostr."""
    text = (raw or "").strip()
    if not text:
        return None, _ask_for(nostr)
    if any(ch.isspace() for ch in text):
        return None, _("An address has no spaces.")
    if text.startswith("#"):
        if len(text) > 1:
            return text, ""
        return None, _("Name the place in this document after the #.")
    entity = text[len("nostr:"):] if text.lower().startswith("nostr:") else text
    if _NOSTR_ENTITY.fullmatch(entity.lower()):
        return "nostr:" + entity.lower(), ""
    address = text[len("mailto:"):] if text.lower().startswith("mailto:") else text
    if _EMAIL.fullmatch(address):
        return "mailto:" + address, ""
    scheme = _SCHEME.match(text)
    if scheme and not text[scheme.end():].isdigit() and "." not in scheme.group(1):
        name = scheme.group(1).lower()
        if name in ("http", "https"):
            problem = _web_address_problem(text)
            return (None, problem) if problem else (text, "")
        if name == "nostr":
            return None, _("That is not a Nostr link.")
        if name in _DANGEROUS:
            return None, _("Links that run code or open files on a computer are not allowed.")
        if nostr:
            return None, _("Only web addresses, email addresses and Nostr links can be links.")
        return None, _("Only web addresses and email addresses can be links.")
    if _DOMAIN.fullmatch(text):
        problem = _web_address_problem("https://" + text)
        return (None, problem) if problem else ("https://" + text, "")
    return None, _ask_for(nostr)


def is_bare_http_url(text: str) -> bool:
    """Whether ``text`` is one web address and nothing else (a paste that
    should turn the selected words into a link)."""
    text = (text or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    return url_safety.is_safe_external_url(text)


def display_href(href: str, limit: int = 60) -> str:
    """A link's address short enough for a tooltip or a menu: without
    ``mailto:``, and cut in the middle when it is long."""
    shown = href[len("mailto:"):] if href.lower().startswith("mailto:") else href
    if len(shown) <= limit:
        return shown
    keep = (limit - 1) // 2
    return shown[:keep] + "\u2026" + shown[-keep:]


def web_address_for(href: str) -> Optional[str]:
    """Where opening a link goes in the browser: a web address as it is,
    a Nostr link through njump.me (a page every browser can show), None
    for anything else (an email opens the mail app instead)."""
    lowered = href.lower()
    if lowered.startswith(("http://", "https://")):
        return href
    if lowered.startswith("nostr:") and _NOSTR_ENTITY.fullmatch(lowered[len("nostr:"):]):
        return "https://njump.me/" + href[len("nostr:"):]
    return None
