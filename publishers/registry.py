"""Lookup of all enabled publishers.

The main window iterates this list to build its publishing menus and
to start any background services (e.g. the Telegram scheduler).

Order matters: it is the order shown in menus.
"""

from __future__ import annotations

from typing import List

from .base import Publisher


def get_publishers() -> List[Publisher]:
    """Return the publishers enabled in this build.

    Imports are local so that an environment missing one publisher's
    dependencies still loads the others.
    """
    publishers: List[Publisher] = []

    try:
        from .telegram import TelegramPublisher
        publishers.append(TelegramPublisher.singleton())
    except ImportError:
        pass

    return publishers
