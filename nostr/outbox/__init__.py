# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""NIP-65, the outbox model: who reads and writes where.

One package owns every answer to "which relays?":

    defaults   the relays MyEditor picks by itself, by role, with the date
               they were last checked (the only place relay URLs live)
    policy     the rules, as pure functions (publish routing, private
               storage, reading someone's notes, safe list changes)
    lookup     finding a verified relay list or profile, and telling
               "there is none" apart from "nobody answered"
    directory  RelayDirectory: lookups, caching and the user's own lists
    writer     changing the user's relay list or profile without ever
               overwriting what is already there; setting up new accounts

legacy holds the selection code this package replaces, until every call
site has moved.
"""

from .legacy import (  # noqa: F401  (re-exported while call sites migrate)
    RELAY_CAP,
    RelayListCache,
)
from .directory import RelayDirectory, ask_private_relays  # noqa: F401
from .policy import (  # noqa: F401
    KIND_PROFILE,
    KIND_RELAY_LIST,
    LookupState,
    PublishPlan,
    RelayList,
    dedupe_relays,
    merge_profile_content,
    newest_valid,
    normalize_relay_url,
    parse_relay_list,
    plan_publish,
    private_relays,
    relay_list_tags_adding,
    replacement_created_at,
    starter_relay_list_tags,
)
