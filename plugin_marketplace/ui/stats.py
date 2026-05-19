"""Reusable widgets for plugin engagement metadata.

The marketplace surfaces a handful of metrics drawn from VS Code
Marketplace, Chrome Web Store, and App Store patterns:

  - Average star rating (with half-star precision)
  - Rating count, install count, comments, zaps as a tight icon-and-
    number row, App Store-style
  - Lightning tip chip that copies the address to the clipboard and
    flashes a confirmation

Components in this module take a ``PluginListing`` and render its
metrics; they never query the network themselves. That keeps the
rendering side cheap and lets future code paths (M4 Nostr fetcher)
just refresh the listing and call ``populate`` again.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import PluginListing


# ──────────────────────────────────────────────────────────────────────
# Number + time formatters
# ──────────────────────────────────────────────────────────────────────

def format_count(n: int) -> str:
    """Compact integer formatter used by every metric chip.

    Examples:
        ``999``      -> ``"999"``
        ``1_234``    -> ``"1.2k"``
        ``12_345``   -> ``"12k"``
        ``1_234_567`` -> ``"1.2M"``
    """
    if n < 0:
        return "0"
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n / 1_000:.1f}k".rstrip("0").rstrip(".")
    if n < 1_000_000:
        return f"{n // 1_000}k"
    if n < 10_000_000:
        return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    return f"{n // 1_000_000}M"


def format_sats(n: int) -> str:
    """Sat-aware formatter. Below 1k stays exact; otherwise compact.

    Lightning amounts are emotionally weighted; people care about the
    difference between 21 sats and 210 sats much more than between
    21k and 22k, so we keep the small numbers precise.
    """
    if n < 1_000:
        return f"{n}"
    return format_count(n)


def format_relative_time(iso: str) -> str:
    """Friendly relative time for the ``updated_at`` field.

    Falls back to the raw string for anything we can't parse so we
    never lie about freshness. Returns empty string for empty input.
    """
    if not iso:
        return ""
    try:
        # ``datetime.fromisoformat`` handles common forms; pad a Z
        # since older Python versions don't.
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
    try:
        delta = now - dt
    except TypeError:
        return iso
    secs = max(0, int(delta.total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86_400:
        return f"{secs // 3600} h ago"
    if secs < 30 * 86_400:
        return f"{secs // 86_400} d ago"
    if secs < 365 * 86_400:
        return f"{secs // (30 * 86_400)} mo ago"
    return f"{secs // (365 * 86_400)} yr ago"


# ──────────────────────────────────────────────────────────────────────
# Star rating widget
# ──────────────────────────────────────────────────────────────────────

class RatingStars(QWidget):
    """Five painted stars with half-star precision.

    Painted with QPainter rather than as text glyphs because unicode
    star characters render at different weights across Qt styles, and
    half-filled is impossible with text. A QPainter implementation
    gets us the App Store look on every platform and scales cleanly.
    """

    def __init__(
        self,
        rating: float = 0.0,
        *,
        size: int = 14,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._rating = max(0.0, min(5.0, rating))
        self._size = size
        self.setFixedHeight(size + 2)
        # 5 stars, 2-pixel gap each.
        self.setFixedWidth(size * 5 + 8)

    def set_rating(self, rating: float) -> None:
        self._rating = max(0.0, min(5.0, rating))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._size * 5 + 8, self._size + 2)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        gold = QColor("#f5b400")
        outline = QColor(255, 255, 255, 50)

        for i in range(5):
            x = i * (self._size + 2)
            star_rect = QRectF(x, 0, self._size, self._size)
            star = _star_polygon(star_rect)

            # Empty outline
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(outline, 1))
            painter.drawPolygon(star)

            # Filled portion based on rating - per-star precision.
            # rating 4.3 = three full stars, one 30%-filled star, one empty.
            fill_frac = max(0.0, min(1.0, self._rating - i))
            if fill_frac > 0:
                painter.save()
                clip_w = self._size * fill_frac
                painter.setClipRect(QRectF(x, 0, clip_w, self._size))
                painter.setBrush(QBrush(gold))
                painter.setPen(QPen(gold.darker(115), 1))
                painter.drawPolygon(star)
                painter.restore()

        painter.end()


def _star_polygon(rect: QRectF) -> QPolygonF:
    """Build a 5-pointed star polygon inscribed in ``rect``."""
    import math
    cx = rect.center().x()
    cy = rect.center().y()
    r_outer = min(rect.width(), rect.height()) / 2
    r_inner = r_outer * 0.45
    points = []
    # Start at the top (angle = -90 degrees) and alternate outer/inner.
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        points.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return QPolygonF(points)


# ──────────────────────────────────────────────────────────────────────
# Single metric: glyph + number
# ──────────────────────────────────────────────────────────────────────

class _MetricChip(QWidget):
    """One ``glyph value`` pair used inside the stats row.

    Kept as its own widget rather than an HBox of two labels so the
    glyph + number stay glued together and the parent layout can space
    chips evenly with a single ``setSpacing`` call.
    """

    def __init__(
        self,
        glyph: str,
        value: str,
        *,
        tooltip: str = "",
        color: str = "#bbb",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        glyph_label = QLabel(glyph)
        glyph_label.setStyleSheet(f"color: {color}; font-size: 13px;")
        value_label = QLabel(value)
        value_label.setStyleSheet("color: #ddd; font-size: 12px; font-weight: 500;")
        layout.addWidget(glyph_label)
        layout.addWidget(value_label)
        if tooltip:
            self.setToolTip(tooltip)


# ──────────────────────────────────────────────────────────────────────
# Stats row
# ──────────────────────────────────────────────────────────────────────

class StatsRow(QWidget):
    """Horizontal row of engagement metrics under the listing header.

    Layout pattern from VS Code Marketplace and App Store: rating on
    the left, then a sequence of compact glyph+number chips for the
    other metrics. Metrics with no value are skipped so the row never
    shows " 0 installs " or " 0 ratings ".
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)
        self._stars: Optional[RatingStars] = None

    def populate(self, listing: PluginListing) -> None:
        """Replace the row's contents with chips drawn from ``listing``."""
        # Clear existing children
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        rendered_anything = False

        # Stars + numeric average + count, in a single attached cluster.
        if listing.rating_count > 0:
            cluster = QWidget()
            cluster_layout = QHBoxLayout(cluster)
            cluster_layout.setContentsMargins(0, 0, 0, 0)
            cluster_layout.setSpacing(6)
            stars = RatingStars(listing.rating_avg)
            cluster_layout.addWidget(stars)
            number = QLabel(
                f"<span style='color:#ddd;font-weight:600'>{listing.rating_avg:.1f}</span>"
                f"  <span style='color:#888'>({format_count(listing.rating_count)})</span>"
            )
            number.setStyleSheet("font-size: 12px;")
            cluster_layout.addWidget(number)
            cluster.setToolTip(
                f"{listing.rating_avg:.1f} out of 5 stars - "
                f"{listing.rating_count} rating{'s' if listing.rating_count != 1 else ''}"
            )
            self._layout.addWidget(cluster)
            rendered_anything = True

        if listing.downloads > 0:
            self._layout.addWidget(_MetricChip(
                "⬇", format_count(listing.downloads),
                tooltip=f"{listing.downloads:,} installs",
                color="#8aa6cc",
            ))
            rendered_anything = True

        if listing.comments_count > 0:
            self._layout.addWidget(_MetricChip(
                "💬", format_count(listing.comments_count),
                tooltip=f"{listing.comments_count} comment{'s' if listing.comments_count != 1 else ''} on Nostr",
                color="#8aa6cc",
            ))
            rendered_anything = True

        if listing.zaps_sats > 0:
            self._layout.addWidget(_MetricChip(
                "⚡", format_sats(listing.zaps_sats) + " sats",
                tooltip=f"{listing.zaps_sats:,} sats received via Lightning zaps",
                color="#f5b400",
            ))
            rendered_anything = True

        relative = format_relative_time(listing.updated_at)
        if relative:
            label = QLabel(f"<span style='color:#888'>Updated {relative}</span>")
            label.setStyleSheet("font-size: 12px;")
            label.setToolTip(listing.updated_at)
            self._layout.addWidget(label)
            rendered_anything = True

        self._layout.addStretch(1)
        self.setVisible(rendered_anything)


# ──────────────────────────────────────────────────────────────────────
# Compact row used inside list cells
# ──────────────────────────────────────────────────────────────────────

class CompactStatsLine(QLabel):
    """Single-line, plain-text version of the stats row.

    Used inside the list-row widget where painted stars would be
    overkill; a unicode-only summary keeps the row compact. Falls
    back to an empty string when no metrics are available.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("color: #888; font-size: 11px;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def populate(self, listing: PluginListing) -> None:
        parts: List[str] = []
        if listing.rating_count > 0:
            parts.append(
                f"<span style='color:#f5b400'>★</span> "
                f"<span style='color:#ddd'>{listing.rating_avg:.1f}</span>"
                f" ({format_count(listing.rating_count)})"
            )
        if listing.downloads > 0:
            parts.append(f"⬇ {format_count(listing.downloads)}")
        if listing.comments_count > 0:
            parts.append(f"💬 {format_count(listing.comments_count)}")
        if listing.zaps_sats > 0:
            parts.append(
                f"<span style='color:#f5b400'>⚡</span> "
                f"{format_sats(listing.zaps_sats)} sats"
            )
        if parts:
            self.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))
            self.setVisible(True)
        else:
            self.setVisible(False)


# ──────────────────────────────────────────────────────────────────────
# Lightning tip chip
# ──────────────────────────────────────────────────────────────────────

class LightningTipChip(QPushButton):
    """A pill-shaped button surfacing the plugin author's tip address.

    Clicking copies the lightning address to the clipboard and briefly
    flashes a "Copied" confirmation, the same affordance Substack and
    Geyser use for tip addresses. Future M6+ work will replace the
    copy behavior with a true Zap flow that pays a small invoice via
    a connected wallet.
    """

    address_copied = Signal(str)

    # Backwards-compatible alias: the detail header listens to a
    # ``copied`` signal alongside the historical ``address_copied``.
    copied = Signal(str)

    def __init__(
        self,
        lightning_address: str = "",
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._address = ""
        self._original_text = ""
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(245,180,0,0.12);"
            "  color: #f5b400;"
            "  border: 1px solid rgba(245,180,0,0.55);"
            "  border-radius: 14px;"
            "  padding: 5px 12px;"
            "  font-weight: 600;"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(245,180,0,0.20);"
            "}"
        )
        self.clicked.connect(self._copy_address)
        if lightning_address:
            self.set_address(lightning_address)

    def set_address(self, lightning_address: str) -> None:
        """Update the chip after construction.

        Lets one chip instance hop between plugins as the user
        navigates the master-detail; cheaper than rebuilding layout.
        """
        self._address = lightning_address or ""
        if not self._address:
            self.setText("")
            self.setToolTip("")
            return
        self._original_text = f"⚡ Tip {_short_address(self._address)}"
        self.setText(self._original_text)
        self.setToolTip(
            f"Copy {self._address} to clipboard.\n"
            "Send any amount you like with your Lightning wallet."
        )

    def _copy_address(self) -> None:
        if not self._address:
            return
        QGuiApplication.clipboard().setText(self._address)
        self.address_copied.emit(self._address)
        self.copied.emit(self._address)
        self.setText("⚡ Copied")
        QTimer.singleShot(1400, lambda: self.setText(self._original_text))


def _short_address(addr: str) -> str:
    """Render lightning addresses compactly when they get long.

    ``donations@my-editor.example`` stays as-is; anything longer than
    28 characters gets truncated with an ellipsis in the middle so
    the user sees both the username and the domain.
    """
    if len(addr) <= 28:
        return addr
    user, _, host = addr.partition("@")
    if not host:
        return addr[:25] + "..."
    # Keep first 8 chars of user + the full host with an ellipsis between.
    return f"{user[:8]}...@{host}"
