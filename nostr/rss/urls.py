# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Addresses in imported posts: the one place that says what a web
address is, makes a relative one whole, and writes one as a Markdown
link target."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlsplit

WEB_SCHEMES = ("http", "https")


def scheme(url: Optional[str]) -> str:
    """The scheme of ``url`` in lower case ("" for a relative address,
    and for one no parser reads, such as ``http://[::1``)."""
    try:
        return urlsplit((url or "").strip()).scheme.lower()
    except ValueError:
        return ""


def is_web(url: Optional[str]) -> bool:
    """Whether ``url`` is an http(s) address."""
    return scheme(url) in WEB_SCHEMES


def whole_url(url: Optional[str], base: Optional[str]) -> Optional[str]:
    """``url`` made whole against the web address ``base``: a relative or
    protocol-relative address gets the page's scheme and host. Any other
    address, and every address when ``base`` is not a web address, is
    returned as it is."""
    value = (url or "").strip()
    if not value or value.startswith("#") or not is_web(base):
        return url
    if value.startswith("//") or not scheme(value):
        try:
            return urljoin(base, value)
        except ValueError:
            return url
    return url


def link_target(url: str) -> str:
    """An address as a Markdown link or image target: a space, a
    parenthesis or an angle bracket in it would end the target early."""
    return (url.strip().replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            .replace("<", "%3C").replace(">", "%3E"))
