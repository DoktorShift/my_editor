"""Modal zap dialog: compose → invoice → wait for receipt.

Flow stages (the user always sees exactly one):

  Compose   ─  amount preset chips + custom field + optional comment
            ─  "Send zap" builds + signs + fetches a bolt11

  Invoice   ─  QR + copyable bolt11 + "waiting for your wallet..."
            ─  closes when ``EngagementFetcher`` reports the matching
               kind:9735 receipt, or when the user dismisses

  Done      ─  "Zap confirmed!" once a receipt lands

The dialog never holds private keys. Signing the zap request goes
through ``BunkerSessionPool`` (NIP-46). The QR fallback works
without any wallet plugin installed; when an NWC plugin is present
in a later milestone, the dialog will skip straight to one-tap pay.

Specs:
  - NIP-57:  https://github.com/nostr-protocol/nips/blob/master/57.md
  - LUD-16:  https://github.com/lnurl/luds/blob/luds/16.md
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from PySide6.QtCore import Qt, QThreadPool, QRunnable, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nostr import DEFAULT_RELAYS
from nostr.events import build_event
from nostr.qr import make_qr_pixmap

from ..social.lnurl import (
    LnurlError,
    LnurlPayData,
    build_zap_request_tags,
    fetch_invoice,
    fetch_lnurl_pay_data,
    lightning_address_to_lnurl_endpoint,
)
from ..social.models import PluginAnchor
from .icons import plugin_pixmap


# Amount presets, in sats. Picked to span casual tipping (21) through
# a meaningful "thank you" (1k+). Custom lets users key in any value.
_PRESET_SATS: tuple[int, ...] = (21, 100, 500, 1_000, 5_000)


class ZapDialog(QDialog):
    """Send sats to a plugin's lightning address with optional Nostr zap.

    Constructed once per click. Owns its own LNURL worker and watches
    the parent marketplace's engagement fetcher for the eventual zap
    receipt so the dialog can flip to a "confirmed" state automatically.
    """

    # Emitted when the dialog successfully completes the round-trip:
    # bolt11 fetched, user clicked "I paid", or receipt arrived.
    zap_dispatched = Signal(int)   # amount_sats

    def __init__(
        self,
        *,
        parent_window,
        anchor: PluginAnchor,
        recipient_display_name: str,
        recipient_avatar_pixmap=None,
        lightning_address: str,
        bunker_client,
        active_pubkey: str,
        engagement_fetcher,
        cache,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send a zap")
        self.setModal(True)
        self.setMinimumSize(440, 460)

        self._parent_window = parent_window
        self._anchor = anchor
        self._recipient_display = recipient_display_name or "the plugin author"
        self._recipient_avatar = recipient_avatar_pixmap
        self._lightning_address = lightning_address
        self._bunker_client = bunker_client
        self._active_pubkey = active_pubkey
        self._cache = cache

        self._chosen_sats: int = 100
        self._comment: str = ""
        self._signed_zap_request: Optional[dict] = None
        self._pay_data: Optional[LnurlPayData] = None
        self._invoice: str = ""
        # Dialog lifetime flag. Workers consult this before invoking
        # slots so a closed dialog never gets a callback after its Qt
        # signal queue has been torn down.
        self._alive: bool = True

        # Watch the engagement fetcher for the eventual receipt so we
        # can flip into the confirmed state without polling.
        self._fetcher = engagement_fetcher
        self._receipt_check_pubkey = active_pubkey
        if self._fetcher is not None:
            self._fetcher.snapshot_changed.connect(self._maybe_flip_to_confirmed)

        self._build_ui()
        self._show_compose()

    # ----------------------------------------------------------------------
    # UI scaffolding
    # ----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(14)

        # Header band: avatar + recipient name + lightning address.
        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignCenter)
        if self._recipient_avatar is not None and not self._recipient_avatar.isNull():
            icon_label.setPixmap(self._recipient_avatar)
        else:
            icon_label.setPixmap(plugin_pixmap(
                plugin_id=self._anchor.author_pubkey,
                name=self._recipient_display,
                icon_path="",
                size=48,
            ))
        header.addWidget(icon_label, 0, Qt.AlignTop)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel(f"Zap <b>{_escape(self._recipient_display)}</b>")
        title.setStyleSheet("font-size: 15px;")
        header_text.addWidget(title)
        addr = QLabel(
            f"<span style='color:#888; font-size: 12px;'>⚡ {_escape(self._lightning_address)}</span>"
        )
        addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header_text.addWidget(addr)
        header.addLayout(header_text, 1)
        root.addLayout(header)

        # Stacked content: compose | invoice | confirmed.
        self._stack = QStackedWidget()
        self._compose_page = self._build_compose_page()
        self._invoice_page = self._build_invoice_page()
        self._confirmed_page = self._build_confirmed_page()
        self._stack.addWidget(self._compose_page)   # 0
        self._stack.addWidget(self._invoice_page)   # 1
        self._stack.addWidget(self._confirmed_page) # 2
        root.addWidget(self._stack, 1)

        # Status / error line above the buttons. Visible only when
        # there's something to say.
        self._status = QLabel("")
        self._status.setStyleSheet("color: #c44; font-size: 12px;")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        root.addWidget(self._status)

        # Footer buttons. Page transitions swap which buttons show.
        self._footer = QHBoxLayout()
        self._footer.setSpacing(8)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._send_btn = QPushButton("Send zap")
        self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_send)
        self._copy_btn = QPushButton("Copy invoice")
        self._copy_btn.clicked.connect(self._on_copy_invoice)
        self._copy_btn.setVisible(False)
        self._close_btn = QPushButton("Done")
        self._close_btn.setVisible(False)
        self._close_btn.clicked.connect(self.accept)
        self._footer.addStretch(1)
        self._footer.addWidget(self._cancel_btn)
        self._footer.addWidget(self._copy_btn)
        self._footer.addWidget(self._send_btn)
        self._footer.addWidget(self._close_btn)
        root.addLayout(self._footer)

    # ----------------------------------------------------------------------
    # Compose page
    # ----------------------------------------------------------------------

    def _build_compose_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Amount preset chips.
        amount_label = QLabel("Choose amount")
        amount_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(amount_label)

        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for sats in _PRESET_SATS:
            chip = self._make_preset(sats)
            preset_row.addWidget(chip)
        layout.addLayout(preset_row)

        # Custom amount.
        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        custom_label = QLabel("Or custom:")
        custom_label.setStyleSheet("color: #888; font-size: 12px;")
        custom_row.addWidget(custom_label, 0)
        self._custom_input = QLineEdit()
        self._custom_input.setPlaceholderText("sats")
        self._custom_input.setFixedWidth(120)
        self._custom_input.textChanged.connect(self._on_custom_changed)
        custom_row.addWidget(self._custom_input)
        custom_row.addStretch(1)
        layout.addLayout(custom_row)

        comment_label = QLabel("Comment (optional)")
        comment_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(comment_label)

        self._comment_input = QPlainTextEdit()
        self._comment_input.setPlaceholderText("Say something with your zap...")
        self._comment_input.setFixedHeight(72)
        self._comment_input.setStyleSheet(
            "QPlainTextEdit { background: rgba(255,255,255,0.04); "
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            "padding: 8px; font-size: 13px; color: #ddd; }"
        )
        layout.addWidget(self._comment_input)

        layout.addStretch(1)
        return wrap

    def _make_preset(self, sats: int) -> QPushButton:
        btn = QPushButton(f"{sats:,} sats")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setStyleSheet(_PRESET_STYLE)
        btn.clicked.connect(lambda *_a, s=sats: self._on_preset_picked(s))
        if sats == self._chosen_sats:
            btn.setChecked(True)
        self._preset_group.addButton(btn)
        return btn

    def _on_preset_picked(self, sats: int) -> None:
        self._chosen_sats = sats
        self._custom_input.blockSignals(True)
        self._custom_input.clear()
        self._custom_input.blockSignals(False)

    def _on_custom_changed(self, text: str) -> None:
        # Any custom input wins over the preset selection.
        try:
            value = int(text.strip())
        except ValueError:
            return
        if value <= 0:
            return
        self._chosen_sats = value
        for btn in self._preset_group.buttons():
            btn.setChecked(False)

    # ----------------------------------------------------------------------
    # Invoice page
    # ----------------------------------------------------------------------

    def _build_invoice_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)

        self._invoice_summary = QLabel("")
        self._invoice_summary.setWordWrap(True)
        self._invoice_summary.setStyleSheet("color: #ddd; font-size: 13px;")
        layout.addWidget(self._invoice_summary)

        # QR centered.
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._qr_label, 0, Qt.AlignHCenter)

        # Truncated invoice + select-to-copy.
        self._invoice_field = QLineEdit()
        self._invoice_field.setReadOnly(True)
        self._invoice_field.setStyleSheet(
            "QLineEdit { background: rgba(0,0,0,0.20); "
            "border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            "padding: 6px 8px; font-family: 'Menlo, monospace'; "
            "color: #aaa; font-size: 11px; }"
        )
        layout.addWidget(self._invoice_field)

        self._waiting_label = QLabel(
            "<span style='color:#888'>Waiting for your wallet... "
            "we'll confirm here when the payment lands.</span>"
        )
        self._waiting_label.setWordWrap(True)
        layout.addWidget(self._waiting_label)

        layout.addStretch(1)
        return wrap

    # ----------------------------------------------------------------------
    # Confirmed page
    # ----------------------------------------------------------------------

    def _build_confirmed_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        glyph = QLabel("✓")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet("color: #3aa14a; font-size: 64px;")
        layout.addWidget(glyph)

        self._confirmed_title = QLabel("Zap confirmed")
        self._confirmed_title.setAlignment(Qt.AlignCenter)
        self._confirmed_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self._confirmed_title)

        self._confirmed_body = QLabel("")
        self._confirmed_body.setAlignment(Qt.AlignCenter)
        self._confirmed_body.setWordWrap(True)
        self._confirmed_body.setStyleSheet("color: #aaa; font-size: 13px;")
        layout.addWidget(self._confirmed_body)

        layout.addStretch(1)
        return wrap

    # ----------------------------------------------------------------------
    # State transitions
    # ----------------------------------------------------------------------

    def _show_compose(self) -> None:
        self._stack.setCurrentWidget(self._compose_page)
        self._send_btn.setVisible(True)
        self._send_btn.setEnabled(True)
        self._copy_btn.setVisible(False)
        self._close_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._status.setVisible(False)

    def _show_invoice(self) -> None:
        self._stack.setCurrentWidget(self._invoice_page)
        self._send_btn.setVisible(False)
        self._copy_btn.setVisible(True)
        self._close_btn.setVisible(True)
        self._close_btn.setText("Close")
        self._cancel_btn.setVisible(False)

        # Paint the QR + invoice.
        self._invoice_summary.setText(
            f"<b>{self._chosen_sats:,} sats</b> to "
            f"<b>{_escape(self._recipient_display)}</b>. "
            "Scan with any Lightning wallet, or copy the invoice."
        )
        self._invoice_field.setText(self._invoice)
        try:
            pixmap = make_qr_pixmap(self._invoice, size=260)
            self._qr_label.setPixmap(pixmap)
        except Exception:  # noqa: BLE001
            self._qr_label.setText("(QR could not be rendered)")

    def _show_confirmed(self, *, sats: int) -> None:
        self._stack.setCurrentWidget(self._confirmed_page)
        self._confirmed_body.setText(
            f"{sats:,} sats sent to {self._recipient_display}. "
            "Thanks for supporting this plugin."
        )
        self._send_btn.setVisible(False)
        self._copy_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)
        self._close_btn.setText("Done")

    def _show_error(self, message: str) -> None:
        self._status.setText(message)
        self._status.setVisible(True)
        self._send_btn.setEnabled(True)
        self._send_btn.setText("Try again")

    # ----------------------------------------------------------------------
    # Send pipeline
    # ----------------------------------------------------------------------

    def _on_send(self) -> None:
        amount_msat = self._chosen_sats * 1000
        if amount_msat <= 0:
            self._show_error("Pick an amount above zero.")
            return
        self._status.setVisible(False)
        self._send_btn.setEnabled(False)
        self._send_btn.setText("Building invoice...")
        self._comment = self._comment_input.toPlainText().strip()

        # Step 1: resolve the LNURL endpoint on a worker thread.
        worker = _LnurlPrepareWorker(self._lightning_address)
        worker.signals.success.connect(self._on_lnurl_resolved)
        worker.signals.failed.connect(self._show_error)
        QThreadPool.globalInstance().start(worker)

    def _on_lnurl_resolved(self, pay_data: LnurlPayData) -> None:
        if not self._alive:
            return
        self._pay_data = pay_data
        # Cache the LNURL service's nostrPubkey so receipts arriving
        # later can be validated against the actual signer (NIP-57's
        # only protection against forged 9735s).
        if pay_data.allows_nostr and pay_data.nostr_pubkey and self._cache is not None:
            try:
                self._cache.store_lnurl_pubkey(
                    self._lightning_address, pay_data.nostr_pubkey,
                )
            except Exception:  # noqa: BLE001
                # Cache write failures are non-fatal; the receipt
                # validation just stays in the looser parse mode.
                pass
        amount_msat = self._chosen_sats * 1000
        if amount_msat < pay_data.min_sendable_msat:
            self._show_error(
                f"This wallet requires at least {pay_data.min_sendable_msat // 1000:,} sats."
            )
            return
        if amount_msat > pay_data.max_sendable_msat:
            self._show_error(
                f"This wallet caps zaps at {pay_data.max_sendable_msat // 1000:,} sats."
            )
            return
        if not pay_data.allows_nostr:
            # The LNURL accepts standard payments but won't mint a
            # zap receipt. Fall back to a plain LNURL-pay invoice so
            # the user can still tip; the marketplace just won't see
            # the kind:9735 confirmation.
            self._fetch_plain_invoice(amount_msat)
            return

        # Step 2: build + sign the kind:9734 zap request.
        try:
            tags = build_zap_request_tags(
                recipient_pubkey=self._anchor.author_pubkey,
                amount_msat=amount_msat,
                relays=list(DEFAULT_RELAYS),
                plugin_anchor=self._anchor.coord,
            )
        except LnurlError as exc:
            self._show_error(str(exc))
            return

        unsigned = build_event(
            pubkey_hex=self._active_pubkey,
            kind=9734,
            content=self._comment,
            tags=tags,
            created_at=int(time.time()),
        )

        if self._bunker_client is None:
            self._show_error(
                "Connect a Nostr signer to send a zap with a Nostr receipt."
            )
            return

        def _on_signed(signed: dict) -> None:
            self._signed_zap_request = signed
            self._fetch_zap_invoice(amount_msat, signed)

        def _on_sign_failed(reason: str) -> None:
            self._show_error(f"Signer rejected zap request: {reason}")

        self._bunker_client.sign_event(unsigned, _on_signed, _on_sign_failed)

    def _fetch_zap_invoice(self, amount_msat: int, signed_request: dict) -> None:
        request_json = json.dumps(signed_request, separators=(",", ":"))
        worker = _LnurlInvoiceWorker(
            pay_data=self._pay_data,
            amount_msat=amount_msat,
            zap_request_json=request_json,
        )
        worker.signals.success.connect(self._on_invoice_ready)
        worker.signals.failed.connect(self._show_error)
        QThreadPool.globalInstance().start(worker)

    def _fetch_plain_invoice(self, amount_msat: int) -> None:
        """LNURL endpoint doesn't support zaps; fall back to a plain
        LNURL-pay invoice. No kind:9735 receipt expected."""
        worker = _LnurlInvoiceWorker(
            pay_data=self._pay_data,
            amount_msat=amount_msat,
            zap_request_json="",
        )
        worker.signals.success.connect(self._on_invoice_ready)
        worker.signals.failed.connect(self._show_error)
        QThreadPool.globalInstance().start(worker)

    def _on_invoice_ready(self, bolt11: str) -> None:
        if not self._alive:
            return
        self._invoice = bolt11
        self._show_invoice()
        # Tell the rest of the editor the zap is in flight; counts as
        # a soft commitment for the optimistic UI.
        self.zap_dispatched.emit(self._chosen_sats)

    # ----------------------------------------------------------------------
    # Receipt watcher
    # ----------------------------------------------------------------------

    def _maybe_flip_to_confirmed(self, anchor) -> None:
        """The engagement fetcher signaled new events. If a kind:9735
        receipt for *our* zapper pubkey landed, flip to confirmed.

        We parse in strict mode here: receipts have to be signed by
        the LNURL service whose nostrPubkey we resolved during the
        compose step. Loose-mode receipts (any signer) would let any
        relay forge a confirmation.
        """
        if self._stack.currentWidget() is not self._invoice_page:
            return
        if anchor != self._anchor:
            return
        expected_lnurl = (
            self._pay_data.nostr_pubkey
            if self._pay_data is not None and self._pay_data.allows_nostr
            else None
        )
        ratings, comments, zaps, deletions = self._cache.parsed_for_anchor(
            self._anchor, expected_lnurl_pubkey=expected_lnurl,
        )
        for zap in zaps:
            if zap.zapper_pubkey == self._active_pubkey:
                self._show_confirmed(sats=zap.amount_sats)
                return

    def _on_copy_invoice(self) -> None:
        if not self._invoice:
            return
        QGuiApplication.clipboard().setText(self._invoice)
        # Tiny visual feedback by flashing the button label.
        original = self._copy_btn.text()
        self._copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self._copy_btn.setText(original))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._alive = False
        if self._fetcher is not None:
            try:
                self._fetcher.snapshot_changed.disconnect(self._maybe_flip_to_confirmed)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(event)


# ──────────────────────────────────────────────────────────────────────
# Workers
# ──────────────────────────────────────────────────────────────────────

class _PrepareSignals(QObject):
    success = Signal(object)   # LnurlPayData
    failed = Signal(str)


class _LnurlPrepareWorker(QRunnable):
    """Resolve a lightning address to its LNURL-pay metadata."""

    def __init__(self, lightning_address: str) -> None:
        super().__init__()
        self._address = lightning_address
        self.signals = _PrepareSignals()

    def run(self) -> None:  # noqa: D401
        try:
            endpoint = lightning_address_to_lnurl_endpoint(self._address)
            data = fetch_lnurl_pay_data(endpoint)
        except LnurlError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"unexpected error: {exc}")
            return
        self.signals.success.emit(data)


class _InvoiceSignals(QObject):
    success = Signal(str)   # bolt11
    failed = Signal(str)


class _LnurlInvoiceWorker(QRunnable):
    """POST the LNURL callback with the (optional) zap request."""

    def __init__(
        self,
        *,
        pay_data: LnurlPayData,
        amount_msat: int,
        zap_request_json: str,
    ) -> None:
        super().__init__()
        self._pay_data = pay_data
        self._amount_msat = amount_msat
        self._zap_request_json = zap_request_json
        self.signals = _InvoiceSignals()

    def run(self) -> None:  # noqa: D401
        try:
            bolt11 = fetch_invoice(
                pay_data=self._pay_data,
                amount_msat=self._amount_msat,
                zap_request_json=self._zap_request_json,
            )
        except LnurlError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"unexpected error: {exc}")
            return
        self.signals.success.emit(bolt11)


# ──────────────────────────────────────────────────────────────────────
# Styles
# ──────────────────────────────────────────────────────────────────────

_PRESET_STYLE = (
    "QPushButton {"
    "  background: transparent;"
    "  border: 1px solid rgba(255,255,255,0.12);"
    "  border-radius: 10px;"
    "  padding: 6px 12px;"
    "  color: #ddd;"
    "  font-size: 12px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover {"
    "  border-color: rgba(245,180,0,0.55);"
    "  color: #fff;"
    "}"
    "QPushButton:checked {"
    "  background: rgba(245,180,0,0.18);"
    "  border-color: #f5b400;"
    "  color: #ffd45a;"
    "}"
)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
