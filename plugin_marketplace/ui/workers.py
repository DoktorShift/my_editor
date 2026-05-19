"""Background workers for the marketplace.

Two pieces of work are pushed off the UI thread:

  - **Catalog refresh** - fetches every registry index. May take
    several seconds on a slow connection; users hate frozen windows.
  - **Plugin install** - downloads, verifies, extracts. Always
    network-bound and sometimes large.

Both workers communicate with the dialog through Qt signals so
results land on the UI thread before any Qt widget is touched. The
hot-reload step (creating menu actions) runs on the UI thread after
the install worker signals success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from ..controller import CatalogSnapshot, MarketplaceController
from ..installer import InstallError, InstallResult
from ..models import PluginListing


# ──────────────────────────────────────────────────────────────────────
# Catalog refresh
# ──────────────────────────────────────────────────────────────────────

class CatalogRefreshSignals(QObject):
    finished = Signal(object)  # CatalogSnapshot
    failed = Signal(str)


class CatalogRefreshWorker(QRunnable):
    """Refreshes the marketplace catalog on a thread-pool worker.

    Long network latency mustn't freeze the dialog. The worker emits
    ``finished`` with a CatalogSnapshot on success, ``failed`` with a
    user-readable string otherwise.

    Pass ``signals_parent`` (typically the dialog) so the signals
    QObject is destroyed with the parent; otherwise a worker still
    running when the dialog closes would emit into nothing and Qt
    would log "Signal source has been deleted".
    """

    def __init__(
        self,
        controller: MarketplaceController,
        *,
        signals_parent: Optional[QObject] = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.signals = CatalogRefreshSignals(signals_parent)

    def run(self) -> None:  # noqa: D401
        try:
            snapshot = self.controller.refresh_catalog()
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(snapshot)


# ──────────────────────────────────────────────────────────────────────
# Plugin install
# ──────────────────────────────────────────────────────────────────────

class InstallSignals(QObject):
    progress = Signal(float)         # 0.0..1.0 during download
    finished = Signal(object)        # InstallResult
    failed = Signal(str)


class InstallWorker(QRunnable):
    """Installs one plugin in a worker thread.

    The hot-reload (menu wiring, payment-provider registration) must
    happen on the UI thread; this worker only does the download +
    extract + atomic-replace work that doesn't touch Qt.
    """

    def __init__(
        self,
        controller: MarketplaceController,
        listing: PluginListing,
        *,
        signals_parent: Optional[QObject] = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.listing = listing
        self.signals = InstallSignals(signals_parent)

    def run(self) -> None:  # noqa: D401
        try:
            result = self.controller.install(
                self.listing,
                progress_cb=lambda fraction: self.signals.progress.emit(fraction),
            )
        except InstallError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - last-line safety net
            self.signals.failed.emit(f"unexpected error: {exc}")
            return
        self.signals.finished.emit(result)
