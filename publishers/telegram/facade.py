"""Singleton facade satisfying the :class:`publishers.base.Publisher` protocol.

Owns the shared mutable state that every dialog needs: the
:class:`TelegramSettings` store, the :class:`BotRegistry`, the
:class:`ScheduledQueue`, the :class:`FileIdCache`, and the running
:class:`TelegramScheduler`.

Lifecycle:

* :meth:`singleton` is called once by :mod:`publishers.registry` and
  again whenever the main window wants the facade.
* :meth:`bootstrap` is called once after the main window exists; it
  starts the scheduler.
* On editor shutdown, the facade is garbage-collected; the scheduler's
  QTimer is destroyed with it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from .bots import BotRegistry
from .file_ids import FileIdCache
from .queue import ScheduledQueue
from .scheduler import TelegramScheduler
from .settings import TelegramSettings


class TelegramPublisher(QObject):
    """Singleton entry point for the Telegram destination."""

    name: str = "telegram"
    display_name: str = "Telegram"

    _instance: Optional["TelegramPublisher"] = None

    def __init__(self) -> None:
        super().__init__()
        self._settings = TelegramSettings()
        self._bots = BotRegistry(self._settings, parent=self)
        self._queue = ScheduledQueue()
        self._file_ids = FileIdCache()
        self._scheduler = TelegramScheduler(
            settings=self._settings,
            queue=self._queue,
            bots=self._bots,
            file_ids=self._file_ids,
            parent=self,
        )
        self._main_window: Optional[QWidget] = None

    # -- singleton --------------------------------------------------- #

    @classmethod
    def singleton(cls) -> "TelegramPublisher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- shared state accessors ------------------------------------- #

    @property
    def settings(self) -> TelegramSettings:
        return self._settings

    @property
    def bots(self) -> BotRegistry:
        return self._bots

    @property
    def queue(self) -> ScheduledQueue:
        return self._queue

    @property
    def file_ids(self) -> FileIdCache:
        return self._file_ids

    @property
    def scheduler(self) -> TelegramScheduler:
        return self._scheduler

    # -- Publisher protocol ----------------------------------------- #

    def is_configured(self) -> bool:
        return len(self._settings.bots) > 0

    def open_setup(self, parent: QWidget) -> None:
        from .ui.bots_dialog import BotsDialog
        BotsDialog(self, parent=parent).exec()

    def open_publish(self, parent: QWidget, body_markdown: str) -> None:
        if not self.is_configured():
            self.open_setup(parent)
            if not self.is_configured():
                return
        from .ui.publish_dialog import PublishDialog
        PublishDialog(self, body_markdown=body_markdown, parent=parent).exec()

    def open_queue(self, parent: QWidget) -> None:
        from .ui.queue_dialog import QueueDialog
        QueueDialog(self, parent=parent).exec()

    # -- lifecycle hooks -------------------------------------------- #

    def bootstrap(self, main_window: QWidget) -> None:
        """Start the scheduler. Call after the main window is constructed."""
        self._main_window = main_window
        self._scheduler.start()
