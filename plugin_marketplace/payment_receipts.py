"""Persistent record of paid plugin installs.

When a user pays for a plugin, we keep a small receipt next to the
installed-plugins folder so:

  - re-opening the marketplace doesn't re-ask for payment for
    something the user already paid for,
  - uninstall + reinstall (e.g. switching machines) can prompt the
    user with "we recognize this plugin, we'll skip payment"
    information when the receipt is available, and
  - the user can audit their purchases.

The receipt persists locally as ``payment_receipts.json`` inside the
user-config root. No payment proof is sent off-device; the only
verification we ever did was the LUD-21 ``verify`` callback at the
time of purchase. If the receipts file is missing or corrupted we
gracefully treat every paid plugin as "needs payment again."
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PaymentReceipt:
    """One past payment for one plugin install.

    Fields named to match what a user could justifiably ask the
    marketplace UI to display in a "purchase history" surface.
    """

    plugin_id: str
    version: str
    sats: int
    lightning_address: str
    bolt11: str
    preimage: str
    paid_at: int        # unix seconds
    source_name: str = ""


class PaymentReceiptStore:
    """Append-only JSON receipts. Read on demand; write on each new pay."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._receipts: Optional[List[PaymentReceipt]] = None

    def all(self) -> List[PaymentReceipt]:
        if self._receipts is None:
            self._receipts = self._load()
        return list(self._receipts)

    def has_paid_for(self, plugin_id: str, version: str = "") -> bool:
        """``True`` if a matching receipt exists.

        Matches on ``plugin_id`` alone when ``version`` is empty so a
        user who paid for v1.0 doesn't pay again for v1.0.1. Plugin
        authors who want to charge per-version can bump the major.
        """
        for r in self.all():
            if r.plugin_id != plugin_id:
                continue
            if version and r.version and r.version != version:
                continue
            return True
        return False

    def add(self, receipt: PaymentReceipt) -> None:
        receipts = self.all()
        receipts.append(receipt)
        self._receipts = receipts
        self._save(receipts)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> List[PaymentReceipt]:
        if not self._path.is_file():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, json.JSONDecodeError):
            # A corrupted receipts file shouldn't lock the user out of
            # the marketplace; we treat it as "no past receipts" and
            # subsequent saves will rewrite a healthy file.
            return []
        out: List[PaymentReceipt] = []
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                try:
                    out.append(PaymentReceipt(
                        plugin_id=str(entry.get("plugin_id", "")),
                        version=str(entry.get("version", "")),
                        sats=int(entry.get("sats", 0)),
                        lightning_address=str(entry.get("lightning_address", "")),
                        bolt11=str(entry.get("bolt11", "")),
                        preimage=str(entry.get("preimage", "")),
                        paid_at=int(entry.get("paid_at", 0)),
                        source_name=str(entry.get("source_name", "")),
                    ))
                except (TypeError, ValueError):
                    continue
        return out

    def _save(self, receipts: List[PaymentReceipt]) -> None:
        payload = [asdict(r) for r in receipts]
        # Write atomically so a crashed editor never leaves the file
        # half-written and unreadable.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


def utc_now_seconds() -> int:
    """Wrap ``time.time`` for monkey-patchability in tests."""
    return int(time.time())
