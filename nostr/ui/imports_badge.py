# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""New posts on the app icon (C13, the owner's decision Q-7).

Off until the person turns it on. One setting, Nostr > Show New Posts on
the App Icon, offered once in the Imports window when new posts first
arrive there. When it is on, the app icon carries the number of posts in
the Inbox: the Dock icon on macOS, the taskbar button on Windows, the
launcher where a Linux desktop has one (QGuiApplication.setBadgeNumber).
The number is the Inbox's own, so it goes down as posts are imported or
skipped, and 0 takes it away. No banners, no menu bar or tray icon.

The setting is kept in settings.json, under :data:`SETTINGS_KEY`.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from i18n import _

SETTINGS_KEY = "imports_badge"
COMMAND_TITLE = _("Show New Posts on the App Icon")
OFFER = _("Show the number of new posts on the app icon? You can change this in the "
          "Nostr menu.")
OFFER_ACTION = _("Show")


def _system_badge(count: int) -> None:
    """The number on the app icon; 0 takes it away. Where a system has no
    badge, Qt does nothing."""
    setter = getattr(QGuiApplication.instance(), "setBadgeNumber", None)
    if setter is not None:
        setter(count)


class AppIconBadge(QObject):
    """The Inbox count on the app icon, when the person wants it.

    Signals:
      changed(bool)   the setting was turned on or off
    """

    changed = Signal(bool)

    def __init__(self, controller, *, load: Callable[[], Optional[dict]],
                 save: Callable[[dict], None],
                 show: Callable[[int], None] = _system_badge,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._save = save
        self._show = show
        stored = load()
        self._data = dict(stored) if isinstance(stored, dict) else {}
        self._shown: Optional[int] = None
        controller.inbox_count_changed.connect(self._on_count)
        controller.bound_changed.connect(self._on_bound)
        self._update()

    @property
    def on(self) -> bool:
        return bool(self._data.get("on"))

    def should_offer(self) -> bool:
        """Whether the Imports window still offers it (once, ever)."""
        return not self.on and not self._data.get("offered")

    def mark_offered(self) -> None:
        self._data["offered"] = True
        self._save(dict(self._data))

    def set_on(self, on: bool) -> None:
        on = bool(on)
        if on == self.on and self._data.get("offered"):
            return
        self._data.update(on=on, offered=True)
        self._save(dict(self._data))
        self._update()
        self.changed.emit(on)

    # -- the number ------------------------------------------------------------

    def _on_count(self, count: int) -> None:
        self._set(count if self.on and self._controller.bound else 0)

    def _on_bound(self, _bound: bool) -> None:
        self._update()

    def _update(self) -> None:
        controller = self._controller
        self._set(controller.counts().inbox if self.on and controller.bound else 0)

    def _set(self, count: int) -> None:
        if count != self._shown:
            self._shown = count
            self._show(count)
