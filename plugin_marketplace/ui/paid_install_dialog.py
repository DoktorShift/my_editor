"""Paid-install dialog: LNURL-pay + LUD-21 verify, then install.

State machine (stacked pages, one visible at a time):

  PREPARING   — resolving the lightning address, fetching the LNURL
                metadata, requesting an invoice.
  PAY         — QR + copyable bolt11 + "waiting for payment…"
                Background timer polls the LUD-21 verify URL once a
                second so the dialog flips to ``CONFIRMED`` the moment
                the wallet pays.
  CONFIRMED   — settlement confirmed; persists a PaymentReceipt and
                hands the listing to the install worker. Stays open
                until the install itself completes.
  FAILED      — user-actionable error message + Retry button.

Nothing in this dialog blocks the editor's main thread: LNURL HTTP
calls run on a QThreadPool worker and the verify-poll uses a Qt
QTimer. The user can cancel at any time; cancellation discards the
in-flight invoice but never half-installs the plugin.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nostr.qr import make_qr_pixmap

from ..models import PluginListing
from ..payment_receipts import PaymentReceipt, PaymentReceiptStore, utc_now_seconds
from ..social.lnurl import (
    InvoiceWithVerify,
    LnurlError,
    LnurlPayData,
    check_invoice_settled,
    fetch_lnurl_pay_data,
    fetch_paid_invoice,
    lightning_address_to_lnurl_endpoint,
)


# Polling cadence for the LUD-21 verify URL while the user pays. A
# settled payment usually shows up within a few seconds; cap the
# polling lifetime so a forgotten dialog doesn't leak network calls.
_VERIFY_POLL_MS: int = 1500
_VERIFY_TIMEOUT_S: int = 15 * 60


class PaidInstallDialog(QDialog):
    """Modal flow that pays for a plugin then triggers its install.

    Caller pattern:

        dlg = PaidInstallDialog(
            listing=listing, receipt_store=store, parent=mainwindow,
        )
        dlg.payment_confirmed.connect(lambda receipt: install_worker.start(...))
        dlg.exec()
    """

    payment_confirmed = Signal(object)   # PaymentReceipt
    dismissed = Signal()                 # user closed without paying

    def __init__(
        self,
        *,
        listing: PluginListing,
        receipt_store: PaymentReceiptStore,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pay for {listing.name}")
        self.setModal(True)
        self.setMinimumSize(440, 440)

        self._listing = listing
        self._receipts = receipt_store
        self._pay_data: Optional[LnurlPayData] = None
        self._invoice: Optional[InvoiceWithVerify] = None
        self._poll_started_at: float = 0.0
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_VERIFY_POLL_MS)
        self._poll_timer.timeout.connect(self._poll_verify)

        self._build_ui()
        self._show_preparing()
        self._start_lnurl_flow()

    # ------------------------------------------------------------------
    # UI scaffolding
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(14)

        header = QLabel(
            f"<b>{_escape(self._listing.name)}</b> · "
            f"<span style='color:#aaa'>{self._listing.price_sats:,} sats</span>"
        )
        header.setStyleSheet("font-size: 14px;")
        root.addWidget(header)

        self._stack = QStackedWidget()
        self._preparing_page = self._build_preparing_page()
        self._pay_page = self._build_pay_page()
        self._confirmed_page = self._build_confirmed_page()
        self._failed_page = self._build_failed_page()
        self._stack.addWidget(self._preparing_page)
        self._stack.addWidget(self._pay_page)
        self._stack.addWidget(self._confirmed_page)
        self._stack.addWidget(self._failed_page)
        root.addWidget(self._stack, 1)

        # Footer
        self._footer = QHBoxLayout()
        self._footer.setSpacing(8)
        self._copy_btn = QPushButton("Copy invoice")
        self._copy_btn.clicked.connect(self._copy_invoice)
        self._copy_btn.setVisible(False)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._retry_btn = QPushButton("Try again")
        self._retry_btn.clicked.connect(self._start_lnurl_flow)
        self._retry_btn.setVisible(False)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setVisible(False)
        self._footer.addStretch(1)
        self._footer.addWidget(self._copy_btn)
        self._footer.addWidget(self._retry_btn)
        self._footer.addWidget(self._cancel_btn)
        self._footer.addWidget(self._close_btn)
        root.addLayout(self._footer)

    def _build_preparing_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setAlignment(Qt.AlignCenter)
        spinner = QLabel("Preparing invoice…")
        spinner.setStyleSheet("color: #aaa; font-size: 13px;")
        layout.addWidget(spinner, 0, Qt.AlignCenter)
        return wrap

    def _build_pay_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._qr_label, 0, Qt.AlignHCenter)

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
            "<span style='color:#888'>Waiting for your wallet to settle… "
            "we'll confirm here when the payment lands.</span>"
        )
        self._waiting_label.setWordWrap(True)
        layout.addWidget(self._waiting_label)
        layout.addStretch(1)
        return wrap

    def _build_confirmed_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setSpacing(10)
        glyph = QLabel("✓")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet("color: #3aa14a; font-size: 56px;")
        layout.addWidget(glyph)
        title = QLabel("Payment confirmed")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)
        body = QLabel(
            "Installing now. You'll see the plugin in your installed list "
            "as soon as the download finishes."
        )
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        body.setStyleSheet("color: #aaa; font-size: 13px;")
        layout.addWidget(body)
        layout.addStretch(1)
        return wrap

    def _build_failed_page(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setSpacing(10)
        self._failed_label = QLabel("")
        self._failed_label.setAlignment(Qt.AlignCenter)
        self._failed_label.setStyleSheet("color: #c44; font-size: 13px;")
        self._failed_label.setWordWrap(True)
        layout.addWidget(self._failed_label, 1, Qt.AlignCenter)
        return wrap

    # ------------------------------------------------------------------
    # Page transitions
    # ------------------------------------------------------------------

    def _show_preparing(self) -> None:
        self._stack.setCurrentWidget(self._preparing_page)
        self._copy_btn.setVisible(False)
        self._retry_btn.setVisible(False)
        self._close_btn.setVisible(False)
        self._cancel_btn.setVisible(True)

    def _show_pay(self) -> None:
        self._stack.setCurrentWidget(self._pay_page)
        self._copy_btn.setVisible(True)
        self._retry_btn.setVisible(False)
        self._close_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setText("Cancel payment")

    def _show_confirmed(self) -> None:
        self._stack.setCurrentWidget(self._confirmed_page)
        self._copy_btn.setVisible(False)
        self._retry_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)

    def _show_failed(self, message: str) -> None:
        self._failed_label.setText(message)
        self._stack.setCurrentWidget(self._failed_page)
        self._copy_btn.setVisible(False)
        self._retry_btn.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._close_btn.setVisible(False)
        self._cancel_btn.setText("Cancel")
        self._poll_timer.stop()

    # ------------------------------------------------------------------
    # LNURL flow
    # ------------------------------------------------------------------

    def _start_lnurl_flow(self) -> None:
        if not self._listing.lightning_address:
            self._show_failed(
                "This plugin is marked paid but the author didn't publish "
                "a lightning address. Ask them to update the listing."
            )
            return
        self._show_preparing()
        worker = _PrepareWorker(self._listing.lightning_address)
        worker.signals.success.connect(self._on_lnurl_ready)
        worker.signals.failed.connect(self._show_failed)
        QThreadPool.globalInstance().start(worker)

    def _on_lnurl_ready(self, pay_data: LnurlPayData) -> None:
        self._pay_data = pay_data
        amount_msat = self._listing.price_sats * 1000
        worker = _InvoiceWorker(pay_data=pay_data, amount_msat=amount_msat,
                                comment=f"my-editor: {self._listing.plugin_id}")
        worker.signals.success.connect(self._on_invoice_ready)
        worker.signals.failed.connect(self._show_failed)
        QThreadPool.globalInstance().start(worker)

    def _on_invoice_ready(self, invoice: InvoiceWithVerify) -> None:
        self._invoice = invoice
        self._invoice_field.setText(invoice.bolt11)
        try:
            self._qr_label.setPixmap(make_qr_pixmap(invoice.bolt11, size=240))
        except Exception:  # noqa: BLE001
            self._qr_label.setText("(QR could not be rendered)")
        self._show_pay()
        if invoice.verify_url:
            self._poll_started_at = time.time()
            self._poll_timer.start()
            self._waiting_label.setText(
                "<span style='color:#888'>Waiting for your wallet to settle… "
                "we'll confirm here when the payment lands.</span>"
            )
        else:
            # Without LUD-21 we cannot verify settlement. Refuse to
            # install on an unverifiable payment. The user can still
            # tip via the QR (it's a real bolt11) but the marketplace
            # makes clear this is not a paid install.
            self._show_unverifiable_settlement_warning()

    def _show_unverifiable_settlement_warning(self) -> None:
        """LUD-21 verify is missing — block the paid-install path.

        Pre-audit behavior was to trust a user-confirmed "I paid" button,
        which let anyone install a paid plugin for free. We now refuse
        the paid install entirely and surface the limitation honestly.
        Authors who want to charge must use an LNURL service that
        advertises a ``verify`` URL.
        """
        self._waiting_label.setText(
            "<b>This payment cannot be verified.</b><br/>"
            "The lightning service for "
            f"<b>{_escape(self._lightning_address())}</b> does not advertise "
            "a LUD-21 <code>verify</code> URL, so we cannot confirm that "
            "the bolt11 above has been settled.<br/><br/>"
            "Installation requires verifiable payment. Ask the plugin "
            "author to use an LNURL service that supports LUD-21."
        )
        self._waiting_label.setStyleSheet("color: #c44; font-size: 13px;")
        # Replace the cancel button with a single Close button so the
        # user is never under the impression they can click through.
        self._cancel_btn.setText("Close")
        try:
            self._cancel_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._poll_timer.stop()

    def _lightning_address(self) -> str:
        return self._listing.lightning_address

    # ------------------------------------------------------------------
    # Verify polling
    # ------------------------------------------------------------------

    def _poll_verify(self) -> None:
        invoice = self._invoice
        if invoice is None or not invoice.verify_url:
            self._poll_timer.stop()
            return
        if time.time() - self._poll_started_at > _VERIFY_TIMEOUT_S:
            self._show_failed(
                "Stopped checking for payment after 15 minutes. "
                "If your payment did go through, restart the install — "
                "we'll detect the existing receipt."
            )
            return
        worker = _VerifyWorker(invoice.verify_url)
        worker.signals.success.connect(self._on_verify_result)
        worker.signals.failed.connect(self._on_verify_error)
        QThreadPool.globalInstance().start(worker)

    def _on_verify_result(self, settled: bool, preimage: str) -> None:
        if settled:
            self._finalize_payment(preimage=preimage)

    def _on_verify_error(self, _reason: str) -> None:
        # Transient verify failures are absorbed silently — the next
        # tick will try again. We only show an error if the polling
        # window expires.
        pass

    def _finalize_payment(self, *, preimage: str) -> None:
        self._poll_timer.stop()
        if self._invoice is None:
            return
        receipt = PaymentReceipt(
            plugin_id=self._listing.plugin_id,
            version=self._listing.version,
            sats=self._listing.price_sats,
            lightning_address=self._listing.lightning_address,
            bolt11=self._invoice.bolt11,
            preimage=preimage,
            paid_at=utc_now_seconds(),
            source_name=self._listing.source_name,
        )
        try:
            self._receipts.add(receipt)
        except Exception:  # noqa: BLE001
            # A failed receipt write shouldn't block the user from
            # finishing what they paid for. We log a status hint via
            # the dialog message; the install still proceeds.
            self._waiting_label.setText(
                "Couldn't write the local receipt — installing anyway."
            )
        self._show_confirmed()
        self.payment_confirmed.emit(receipt)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._poll_timer.stop()
        self.dismissed.emit()
        self.reject()

    def _copy_invoice(self) -> None:
        invoice = self._invoice
        if invoice is None:
            return
        QGuiApplication.clipboard().setText(invoice.bolt11)
        self._copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self._copy_btn.setText("Copy invoice"))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._poll_timer.stop()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #

class _PrepareSignals(QObject):
    success = Signal(object)
    failed = Signal(str)


class _PrepareWorker(QRunnable):
    def __init__(self, lightning_address: str) -> None:
        super().__init__()
        self._address = lightning_address
        self.signals = _PrepareSignals()

    def run(self) -> None:
        try:
            endpoint = lightning_address_to_lnurl_endpoint(self._address)
            data = fetch_lnurl_pay_data(endpoint)
        except LnurlError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:   # noqa: BLE001
            self.signals.failed.emit(f"unexpected error: {exc}")
            return
        self.signals.success.emit(data)


class _InvoiceSignals(QObject):
    success = Signal(object)   # InvoiceWithVerify
    failed = Signal(str)


class _InvoiceWorker(QRunnable):
    def __init__(self, *, pay_data: LnurlPayData, amount_msat: int, comment: str) -> None:
        super().__init__()
        self._pay_data = pay_data
        self._amount_msat = amount_msat
        self._comment = comment
        self.signals = _InvoiceSignals()

    def run(self) -> None:
        try:
            invoice = fetch_paid_invoice(
                pay_data=self._pay_data,
                amount_msat=self._amount_msat,
                comment=self._comment,
            )
        except LnurlError as exc:
            self.signals.failed.emit(str(exc))
            return
        except Exception as exc:   # noqa: BLE001
            self.signals.failed.emit(f"unexpected error: {exc}")
            return
        self.signals.success.emit(invoice)


class _VerifySignals(QObject):
    success = Signal(bool, str)   # settled, preimage
    failed = Signal(str)


class _VerifyWorker(QRunnable):
    def __init__(self, verify_url: str) -> None:
        super().__init__()
        self._verify_url = verify_url
        self.signals = _VerifySignals()

    def run(self) -> None:
        try:
            result = check_invoice_settled(self._verify_url)
        except LnurlError as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.success.emit(result.settled, result.preimage)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
