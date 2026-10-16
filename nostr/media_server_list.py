# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tell other Nostr apps where an account's media lives (BUD-03, kind 10063).

Every app that shows someone's pictures needs to know which media
servers to look on when an image address stops answering, and which ones
the person likes to upload to. The published list says so. Reading it is
nostr/blossom/server_list.py; this module publishes it, and only ever
after the person asked for it (ADR AD-13.5).

The list is a replaceable event, so publishing one replaces the last one
in every app. It goes through the same read-modify-write as the relay
list and the profile (nostr/outbox/writer.py): the current list is read
fresh, a list that could not be read is never replaced, the servers are
written in the order given (the first is the one preferred), any tag
that is not a server is kept, and nothing is sent when the list already
says exactly this. Where it goes is where the profile goes: the
account's own write relays and the indexers.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from nostr.blossom.server_list import BLOSSOM_SERVER_LIST_KIND, build_server_list_event
from nostr.outbox.writer import ReplaceableWriter


def server_list_tags(servers: Sequence[str], base: Optional[dict]) -> list:
    """The tags of the new list: the servers, in order, normalized the way
    the reader normalizes them, then the base's other tags unchanged."""
    servers_part = build_server_list_event(servers, "0" * 64, created_at=0)["tags"]
    kept = [list(tag) for tag in (base or {}).get("tags", [])
            if isinstance(tag, (list, tuple)) and tag and tag[0] != "server"]
    return servers_part + kept


def publish_server_list(*, servers: Sequence[str], **deps) -> ReplaceableWriter:
    """A writer that publishes ``servers`` as the account's media servers.

    Raises ValueError for an empty list (after normalization): an empty
    list would tell every app the account keeps its media nowhere.
    """
    if not server_list_tags(servers, None):
        raise ValueError("no usable media server to publish")

    def mutate(base: Optional[dict]) -> Optional[Tuple[str, list]]:
        tags = server_list_tags(servers, base)
        if base is not None and tags == [list(t) for t in base.get("tags", [])]:
            return None
        return "", tags

    return ReplaceableWriter(kind=BLOSSOM_SERVER_LIST_KIND, mutate=mutate,
                             on_absent="create", **deps)
