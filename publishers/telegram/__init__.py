"""Telegram bot publisher for my_editor.

A standalone destination: nothing in this package imports from
``nostr/``. The feature works for users who have never opened the
Nostr setup.

Public surface:

* :class:`TelegramPublisher` - facade that satisfies the
  :class:`publishers.base.Publisher` protocol and owns the singleton
  state shared between dialogs (settings, scheduler, file-id cache).

Supporting modules:

* :mod:`publishers.telegram.errors` - mapped error taxonomy.
* :mod:`publishers.telegram.settings` - on-disk store (bots, chats).
* :mod:`publishers.telegram.api` - HTTP wrapper over the Bot API.
* :mod:`publishers.telegram.format` - markdown to Telegram HTML.
* :mod:`publishers.telegram.permalinks` - ``t.me/...`` URL building.
* :mod:`publishers.telegram.invites` - deeplinks + QR helpers.
* :mod:`publishers.telegram.publisher` - end-to-end send job.
* :mod:`publishers.telegram.queue` - scheduled-post persistence.
* :mod:`publishers.telegram.scheduler` - QTimer-driven dispatcher.
* :mod:`publishers.telegram.file_ids` - per-bot file_id cache.
"""

from .facade import TelegramPublisher  # noqa: F401
