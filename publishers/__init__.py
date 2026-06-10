"""Pluggable destinations for publishing content from the editor.

Each destination is a self-contained subpackage that implements the
``Publisher`` protocol from :mod:`publishers.base` and registers itself
in :mod:`publishers.registry`. Destinations stand alone: nothing under
``publishers/`` is allowed to import from ``nostr/`` or from any other
destination's package.

Currently shipped:

* :mod:`publishers.telegram` - post to one or more Telegram chats as a
  bot, with optional client-side scheduling.
"""

CLIENT_NAME: str = "My-Editor"
