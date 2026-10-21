# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bluesky (AT Protocol) thread import.

A Bluesky post on its own is a sentence or two; the useful shape is a
*self-thread*: an author writing an essay across many linked posts.
This module stitches that whole thread into one Markdown article.

Facts (per the public appview, ``public.api.bsky.app``, no auth):

- a handle resolves to a DID via ``com.atproto.identity.resolveHandle``;
- ``app.bsky.feed.getPostThread`` returns ``thread.post`` plus
  ``thread.parent`` (ancestors, no replies) and ``thread.replies``
  recursively downward from the *requested* post;
- rich-text ``facets`` carry link ranges as **UTF-8 byte offsets**
  (``index.byteStart/byteEnd``), so slicing must happen on the encoded
  bytes, never the Python string;
- image embeds expose a ``fullsize`` URL + ``alt``; external embeds a
  ``uri`` + ``title``; video embeds a ``playlist`` (HLS) + ``alt``;
- a hashtag is a facet whose feature is ``#tag`` with the ``tag``.

Only the author's own posts are followed: a stranger's reply is never
pulled into the article. The pasted post may sit anywhere in the
thread; ancestors are walked up to the author's root first, then down.
"""

from __future__ import annotations

import json
import re
from typing import Callable, List, Optional
from urllib.parse import quote, unquote

from ...drafts import derive_title_from_markdown
from ...rss.dates import to_unix
from ...rss.markdown import escape, unescape
from ...rss.parser import FeedItem
from ...rss.urls import link_target


BSKY_API = "https://public.api.bsky.app/xrpc/"
_POST_COLLECTION = "app.bsky.feed.post"

_BSKY_URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)?bsky\.app/profile/([^/]+)/post/([^/?#]+)",
    re.IGNORECASE,
)
_AT_URI_RE = re.compile(
    r"^at://(did:[^/]+)/app\.bsky\.feed\.post/([^/?#]+)", re.IGNORECASE)


def parse_bluesky_url(value: Optional[str]) -> Optional[dict]:
    """bsky.app post URL or at:// URI -> ``{"did"|"handle", "rkey"}``."""
    text = str(value or "").strip()
    match = _AT_URI_RE.match(text)
    if match:
        return {"did": match.group(1), "rkey": match.group(2)}
    match = _BSKY_URL_RE.search(text)
    if not match:
        return None
    actor = unquote(match.group(1))
    key = "did" if actor.startswith("did:") else "handle"
    return {key: actor, "rkey": match.group(2)}


# --------------------------------------------------------------------------- #
# Rich text + embeds (pure)                                                   #
# --------------------------------------------------------------------------- #

# A post's "*" or "_" is a character the writer typed, not emphasis
# (markdown.escape). What Markdown reads as syntax at the start of a line: a heading, a
# quote, a list item, a rule or a heading underline.
_LINE_START_SPECIAL = re.compile(
    r"^([ \t]*)(?:(#{1,6}(?=[ \t]|$)|[>+-](?=[ \t]|$)|[=-]+[ \t]*$)|(\d+)([.)])(?=[ \t]|$))",
    re.MULTILINE)




def _escape_line_starts(text: str) -> str:
    def _escape(match: re.Match) -> str:
        if match.group(3):             # "1." starts a list: the dot is escaped
            return f"{match.group(1)}{match.group(3)}\\{match.group(4)}"
        return f"{match.group(1)}\\{match.group(2)}"
    return _LINE_START_SPECIAL.sub(_escape, text)


def _hard_breaks(text: str) -> str:
    """A post's single line breaks are the writer's: Markdown would join
    the lines into one, so each becomes a hard break (two spaces, as the
    editor writes one)."""
    return re.sub(r"[ \t]*(?<!\n)\n(?!\n)", "  \n", text)


def apply_facets(text: str, facets) -> str:
    """Apply rich-text facets, producing Markdown.

    Facet ranges are UTF-8 **byte** offsets: slice the encoded bytes and
    decode each run, wrapping link facets as ``[text](uri)`` and leaving
    mentions/tags as their literal text. Overlapping or out-of-range
    facets are skipped. The text around and inside the facets is
    escaped, so what the writer typed is read as text, not as Markdown.
    """
    source = str(text or "")
    if not isinstance(facets, list) or not facets:
        return _escape_line_starts(escape(source))
    data = source.encode("utf-8")
    usable = sorted(
        (f for f in facets
         if isinstance(f, dict) and isinstance(f.get("index"), dict)
         and isinstance(f.get("features"), list)),
        key=lambda f: f["index"].get("byteStart", 0),
    )

    out: List[str] = []
    cursor = 0
    for facet in usable:
        try:
            start = int(facet["index"]["byteStart"])
            end = int(facet["index"]["byteEnd"])
        except (TypeError, ValueError, KeyError):
            continue
        if start < cursor or end > len(data) or end <= start:
            continue
        out.append(escape(data[cursor:start].decode("utf-8", errors="replace")))
        run = escape(data[start:end].decode("utf-8", errors="replace"))
        link = next(
            (x for x in facet["features"]
             if isinstance(x, dict)
             and x.get("$type") == "app.bsky.richtext.facet#link"
             and x.get("uri")),
            None,
        )
        out.append(f"[{run}]({link_target(str(link['uri']))})" if link else run)
        cursor = end
    out.append(escape(data[cursor:].decode("utf-8", errors="replace")))
    return _escape_line_starts("".join(out))


def facet_tags(record) -> List[str]:
    """The hashtags a post's facets carry (``#tag`` features), as the
    writer tagged them."""
    tags: List[str] = []
    facets = (record or {}).get("facets") if isinstance(record, dict) else None
    for facet in facets if isinstance(facets, list) else []:
        features = facet.get("features") if isinstance(facet, dict) else None
        for feature in features if isinstance(features, list) else []:
            if isinstance(feature, dict) \
                    and feature.get("$type") == "app.bsky.richtext.facet#tag" \
                    and str(feature.get("tag") or "").strip():
                tags.append(str(feature["tag"]).strip().lstrip("#"))
    return tags


def post_url(post: dict) -> str:
    """The post's page on bsky.app, from its at:// address and author."""
    uri = str((post or {}).get("uri") or "")
    author = (post or {}).get("author") or {}
    actor = str(author.get("handle") or author.get("did") or "")
    rkey = uri.rsplit("/", 1)[-1] if "/app.bsky.feed.post/" in uri else ""
    return f"https://bsky.app/profile/{actor}/post/{rkey}" if actor and rkey else ""


def _render_embed(embed, page: str = "") -> str:
    if not isinstance(embed, dict):
        return ""
    embed_type = str(embed.get("$type") or "")
    if "recordWithMedia" in embed_type:
        return _render_embed(embed.get("media"), page)
    if "images" in embed_type and isinstance(embed.get("images"), list):
        parts = []
        for image in embed["images"]:
            if not isinstance(image, dict):
                continue
            url = str(image.get("fullsize") or image.get("thumb") or "")
            if not url.lower().startswith(("http://", "https://")):
                continue
            alt = escape(re.sub(r"\s+", " ", str(image.get("alt") or "")).strip())
            parts.append(f"![{alt}]({url})")
        return "\n\n".join(parts)
    if "video" in embed_type:
        # A video is streamed as a playlist no browser opens as a page:
        # the link goes to the post, where it plays, when that is known.
        target = page or str(embed.get("playlist") or "")
        if not target.lower().startswith(("http://", "https://")):
            return ""
        alt = escape(re.sub(r"\s+", " ", str(embed.get("alt") or "")).strip())
        return f"[{'Video: ' + alt if alt else 'Watch the video'}]({target})"
    if "external" in embed_type and isinstance(embed.get("external"), dict):
        external = embed["external"]
        uri = external.get("uri")
        if uri:
            title = escape(str(external.get("title") or uri).strip())
            return f"[{title}]({link_target(str(uri))})"
    return ""  # quote-posts (record) are not inlined into the body


def post_to_markdown(post: dict) -> str:
    """One post -> a Markdown block (rich text + image/link embeds)."""
    record = (post or {}).get("record") or {}
    text = _hard_breaks(apply_facets(record.get("text") or "", record.get("facets")).strip())
    media = _render_embed((post or {}).get("embed"), post_url(post))
    return "\n\n".join(p for p in (text, media) if p)


# --------------------------------------------------------------------------- #
# Thread walking (pure, over a getPostThread response)                        #
# --------------------------------------------------------------------------- #

def collect_self_thread(thread: dict) -> List[dict]:
    """Ordered list of the author's own posts across the whole thread.

    Same-author ancestors (root-first) form the prefix; then the pasted
    post plus its same-author reply chain (earliest reply first at each
    step). A stranger's reply is never followed. Seen-URI sets make the
    walk immune to cyclic payloads.
    """
    root_did = (((thread or {}).get("post") or {}).get("author") or {}).get("did")

    prefix: List[dict] = []
    seen_up = set()
    node = (thread or {}).get("parent")
    while (
        isinstance(node, dict)
        and ((node.get("post") or {}).get("author") or {}).get("did") == root_did
        and (node.get("post") or {}).get("uri")
        and node["post"]["uri"] not in seen_up
    ):
        seen_up.add(node["post"]["uri"])
        prefix.insert(0, node["post"])  # true root ends up first
        node = node.get("parent")

    down: List[dict] = []
    seen_down = set()
    node = thread
    while (
        isinstance(node, dict)
        and isinstance(node.get("post"), dict)
        and node["post"].get("uri")
        and node["post"]["uri"] not in seen_down
    ):
        seen_down.add(node["post"]["uri"])
        down.append(node["post"])
        replies = [
            r for r in (node.get("replies") or [])
            if isinstance(r, dict)
            and ((r.get("post") or {}).get("author") or {}).get("did") == root_did
            and (r.get("post") or {}).get("uri")
            and r["post"]["uri"] not in seen_down
        ]
        replies.sort(key=lambda r: str(
            ((r["post"].get("record") or {}).get("createdAt")) or ""))
        node = replies[0] if replies else None

    return prefix + down


# --------------------------------------------------------------------------- #
# Resolution (network via the shared fetcher)                                 #
# --------------------------------------------------------------------------- #

def derive_thread_title(markdown: str, max_len: int = 80) -> str:
    """First readable line of the stitched thread, or a placeholder.

    Shares the editor's one Markdown title rule, so a thread opening
    with an image embed no longer titles itself with the image syntax.
    Hashtags now survive: the old local rule stripped ``#`` anywhere in
    the line, which turned "Great day #nostr" into "Great day nostr",
    and on Bluesky a hashtag is a word people mean to read.
    """
    title = derive_title_from_markdown(
        markdown, max_len=max_len, fallback="Bluesky thread",
    )
    # The post's text is escaped for Markdown; a title is plain text.
    return unescape(title)


def _get_json(fetcher, method: str, params: dict, on_done, on_error) -> None:
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"{BSKY_API}{method}?{query}"

    def _parse(body: str) -> None:
        try:
            on_done(json.loads(body))
        except (ValueError, TypeError):
            on_error("Bluesky returned an unreadable response")

    fetcher.fetch(url, on_success=_parse,
                  on_failure=lambda err: on_error(str(err)))


def resolve_bluesky_thread(
    url: str,
    *,
    fetcher,
    on_done: Callable[[dict], None],
    on_error: Callable[[str, str], None],
) -> None:
    """Resolve a post URL into a stitched thread.

    ``on_done`` receives ``{count, handle, display_name, item}`` where
    ``item`` is the normalised ``FeedItem``. ``on_error`` receives
    ``(message, code)`` with a stable error code.
    """
    ref = parse_bluesky_url(url)
    if ref is None:
        on_error("That is not a Bluesky post link", "BLUESKY_INVALID")
        return

    def _with_did(did: Optional[str]) -> None:
        if not did:
            on_error("We couldn't resolve that Bluesky handle",
                     "BLUESKY_NOT_FOUND")
            return
        uri = f"at://{did}/{_POST_COLLECTION}/{ref['rkey']}"
        _get_json(
            fetcher, "app.bsky.feed.getPostThread",
            {"uri": uri, "depth": 80, "parentHeight": 80},
            lambda data: _with_thread(data.get("thread")
                                      if isinstance(data, dict) else None),
            lambda message: on_error(message, "BLUESKY_NOT_FOUND"),
        )

    def _with_thread(thread) -> None:
        if not isinstance(thread, dict) or not isinstance(
                thread.get("post"), dict):
            on_error("That Bluesky post could not be found",
                     "BLUESKY_NOT_FOUND")
            return
        posts = collect_self_thread(thread)
        if not posts:
            on_error("That Bluesky post could not be found",
                     "BLUESKY_NOT_FOUND")
            return
        root = posts[0]
        author = root.get("author") or {}
        handle = str(author.get("handle") or ref.get("did") or "")
        root_rkey = str(root.get("uri") or "").rsplit("/", 1)[-1] or ref["rkey"]
        markdown = "\n\n".join(
            p for p in (post_to_markdown(post) for post in posts) if p)
        # The thread's own hashtags, then where it came from (repeats
        # are taken out with the rest, in normalize.py).
        tags = [*(t for post in posts for t in facet_tags(post.get("record"))), "bluesky"]
        item = FeedItem(
            guid=str(root.get("uri") or ""),
            title=derive_thread_title(markdown),
            link=f"https://bsky.app/profile/{handle}/post/{root_rkey}",
            summary=None,  # made of the opening words (normalize.py)
            content_html="",
            published_at=to_unix(
                (root.get("record") or {}).get("createdAt")),
            categories=tuple(tags),
            image=None,
            author=handle or None,
            content_markdown=markdown,
        )
        on_done({
            "count": len(posts),
            "handle": handle,
            "display_name": str(author.get("displayName") or handle),
            "item": item,
        })

    if ref.get("did"):
        _with_did(ref["did"])
    else:
        _get_json(
            fetcher, "com.atproto.identity.resolveHandle",
            {"handle": ref["handle"]},
            lambda data: _with_did(
                data.get("did") if isinstance(data, dict) else None),
            lambda message: on_error(message, "BLUESKY_NOT_FOUND"),
        )
