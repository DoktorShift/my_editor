"""Build ``https://t.me/...`` URLs from Telegram chat + message ids.

Per the Bot API docs, three permalink shapes exist:

* Public supergroup / channel (the chat has a ``@username``):
  ``https://t.me/<username>/<message_id>``
  - works for anyone with the link.
* Private supergroup / channel (numeric ``chat_id`` starts with -100):
  ``https://t.me/c/<id>/<message_id>`` where ``<id>`` is the chat id
  with the leading ``-100`` stripped - works only for members.
* Legacy basic group (``chat_id`` < 0 but not starting with -100),
  private chats (``chat_id`` > 0): no permalink exists. We return
  ``None``; the UI surfaces "no public link".
"""

from __future__ import annotations

from typing import Optional

from .settings import Chat


def permalink_for(chat: Chat, message_id: int) -> Optional[str]:
    """Return a viewable URL or ``None`` if the chat does not support one."""
    if message_id <= 0:
        return None

    username = (chat.username or "").lstrip("@")
    if username:
        return f"https://t.me/{username}/{message_id}"

    cid = chat.id
    if cid < 0:
        text = str(cid)
        if text.startswith("-100") and len(text) > 4:
            return f"https://t.me/c/{text[4:]}/{message_id}"
        # Legacy basic group: no permalink available.
        return None

    # Private chat with a user: no per-message public link.
    return None


def is_public(chat: Chat) -> bool:
    """``True`` when ``permalink_for`` would produce a publicly viewable URL."""
    return bool(chat.username)
