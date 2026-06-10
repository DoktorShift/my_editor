"""Helper links to bring the user's bot into a chat.

Per the Telegram docs (https://core.telegram.org/api/links and
/bots/features#deep-linking) the deep links that matter are:

* ``https://t.me/<bot>``                              - open the bot in
                                                        Telegram (you talking
                                                        to your bot).
* ``https://t.me/<bot>?startgroup=true``              - pick a group, add bot.
* ``https://t.me/<bot>?startgroup&admin=<perms>``     - pick group, add as admin.
* ``https://t.me/<bot>?startchannel&admin=<perms>``   - pick channel, add as admin.

The bare ``?startchannel=true`` form is not documented to work; channels
require admin status to admit a bot, so the ``admin=`` parameter is
always appended for the channel variant.

A note on DMs: Telegram bots cannot start conversations with users.
A user must send ``/start`` to the bot first, after which that user's
chat shows up in the publisher's chat list and can be picked as a
target. The "Open the bot" link is what you'd share with someone who
wants to start receiving DMs from your bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import quote


# Telegram permission flag names accepted in the ``admin`` query param.
ADMIN_FLAGS_CHANNEL_POSTER: Tuple[str, ...] = (
    "post_messages",
    "edit_messages",
    "delete_messages",
)

ADMIN_FLAGS_CHANNEL_FULL: Tuple[str, ...] = (
    "post_messages",
    "edit_messages",
    "delete_messages",
    "pin_messages",
    "invite_users",
    "manage_chat",
)

ADMIN_FLAGS_GROUP: Tuple[str, ...] = (
    "delete_messages",
    "pin_messages",
    "invite_users",
    "restrict_members",
    "manage_chat",
)


@dataclass(frozen=True)
class InviteLink:
    """A labelled deeplink for the bot."""

    category: str
    """One of ``"group"``, ``"channel"``, ``"dm"`` - for UI grouping."""

    label: str
    description: str
    url: str


def open_bot_link(bot_username: str) -> str:
    return f"https://t.me/{_clean(bot_username)}"


def add_to_group_link(bot_username: str, *, as_admin: bool = False) -> str:
    base = f"https://t.me/{_clean(bot_username)}?startgroup"
    if as_admin:
        return base + "&admin=" + _perms(ADMIN_FLAGS_GROUP)
    return base + "=true"


def add_to_channel_link(bot_username: str, *, full_admin: bool = False) -> str:
    perms = ADMIN_FLAGS_CHANNEL_FULL if full_admin else ADMIN_FLAGS_CHANNEL_POSTER
    return f"https://t.me/{_clean(bot_username)}?startchannel&admin=" + _perms(perms)


def all_invite_links(bot_username: str) -> List[InviteLink]:
    """The standard set we expose in the bots manager + publish dialog."""
    u = _clean(bot_username)
    return [
        InviteLink(
            category="group",
            label="Add to a group",
            description=(
                "Pick one of your groups; the bot is added as a regular member."
            ),
            url=add_to_group_link(u, as_admin=False),
        ),
        InviteLink(
            category="group",
            label="Add to a group as admin",
            description=(
                "Same picker, but the bot is added with admin rights "
                "(delete, pin, invite)."
            ),
            url=add_to_group_link(u, as_admin=True),
        ),
        InviteLink(
            category="channel",
            label="Add to a channel (poster)",
            description=(
                "Channels require admins. The bot will be added as admin "
                "with post + edit + delete permissions."
            ),
            url=add_to_channel_link(u, full_admin=False),
        ),
        InviteLink(
            category="channel",
            label="Add to a channel (full admin)",
            description=(
                "Adds the bot to a channel with full admin rights, including "
                "pin and invite."
            ),
            url=add_to_channel_link(u, full_admin=True),
        ),
        InviteLink(
            category="dm",
            label="Open the bot in Telegram",
            description=(
                "Opens a chat with the bot. Share this link with anyone who "
                "wants to receive DMs from the bot - they must send /start "
                "first, then they'll show up in the chat picker."
            ),
            url=open_bot_link(u),
        ),
    ]


# Back-compat alias for any caller that imported the old name.
def dm_link(bot_username: str) -> str:
    return open_bot_link(bot_username)


def _perms(flags: Tuple[str, ...]) -> str:
    return quote("+".join(flags), safe="+")


def _clean(bot_username: str) -> str:
    return (bot_username or "").lstrip("@")
