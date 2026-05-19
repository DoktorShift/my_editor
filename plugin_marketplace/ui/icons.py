"""Plugin icon rendering for the marketplace UI.

Three sources, in priority order:

  1. ``listing.icon_path`` - an absolute path to an SVG/PNG file the
     controller resolved from the plugin's manifest. Used for installed
     plugins that ship an icon next to their ``__init__.py``.
  2. ``listing.icon_url`` - an HTTPS URL the registry advertised. Not
     yet fetched (a future async pass will cache the bytes); for now we
     skip straight to the fallback rather than block the list render.
  3. **Generated avatar** - a colored rounded square showing the
     plugin's initial. Deterministic from the plugin id so the same
     plugin always paints the same color, the way Gmail and Slack
     generate participant avatars.

The module deliberately renders into ``QPixmap`` rather than handing
out file paths so callers can ask for any size and we'll DPI-aware
scale once and cache. Cache keys include the size because a thumbnail
and a detail-pane icon are different bitmaps even for the same source.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

# Stable, friendly palette. Indexed by hash(plugin_id) % len so the
# same plugin gets the same color every time. Picked to read well on
# both light and dark editor themes.
_PALETTE: tuple[str, ...] = (
    "#2d7fff",  # blue
    "#3aa14a",  # green
    "#b8860b",  # gold
    "#c44d4d",  # red
    "#7c5fff",  # purple
    "#0aa4a4",  # teal
    "#e07a3c",  # orange
    "#5a7ea8",  # slate
)


def plugin_icon(
    *,
    plugin_id: str,
    name: str,
    icon_path: str = "",
    size: int = 32,
) -> QIcon:
    """Return a ``QIcon`` for the plugin at the requested logical size.

    ``QIcon`` is returned (not ``QPixmap``) so callers can hand it to
    ``QListWidgetItem.setIcon`` or ``QLabel`` without converting. Qt
    handles DPI scaling for QIcon transparently.
    """
    pixmap = _plugin_pixmap(plugin_id, name, icon_path, size)
    icon = QIcon()
    icon.addPixmap(pixmap)
    return icon


def plugin_pixmap(
    *,
    plugin_id: str,
    name: str,
    icon_path: str = "",
    size: int = 32,
) -> QPixmap:
    """Return a ``QPixmap`` of the plugin icon at the requested size.

    Use this when you need to put the icon inside a ``QLabel`` (the
    detail pane's hero icon) and want full control over the painted
    bitmap.
    """
    return _plugin_pixmap(plugin_id, name, icon_path, size)


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def _plugin_pixmap(
    plugin_id: str,
    name: str,
    icon_path: str,
    size: int,
) -> QPixmap:
    """Cached render. ``lru_cache`` keys on every positional arg, so
    icon-path changes and size changes both correctly invalidate.

    Two pipelines: when ``icon_path`` points at a readable file, we
    let Qt load it (SVG, PNG, JPG are all natively supported in
    PySide6 builds with ``QtSvg`` linked, which is the default). When
    nothing is available, we paint a deterministic avatar.
    """
    if icon_path:
        loaded = _load_from_file(icon_path, size)
        if loaded is not None:
            return loaded
    return _draw_avatar(plugin_id, name, size)


def _load_from_file(path: str, size: int) -> Optional[QPixmap]:
    """Load ``path`` as a ``QPixmap`` scaled to ``size`` square.

    SVG is loaded losslessly at the requested size (Qt rasterizes from
    vector data). Raster formats are scaled with smooth transformation.
    Returns ``None`` for unreadable files so the caller falls back to
    the generated avatar.
    """
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix.lower() == ".svg":
        # QIcon understands SVG via the platform's image plugins.
        # Rasterise at the requested logical size to avoid blurry
        # upscale-from-tiny in some Qt versions.
        icon = QIcon(str(p))
        pixmap = icon.pixmap(QSize(size, size))
        if not pixmap.isNull():
            return pixmap
    image = QImage(str(p))
    if image.isNull():
        return None
    return QPixmap.fromImage(
        image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    )


def _draw_avatar(plugin_id: str, name: str, size: int) -> QPixmap:
    """Paint a deterministic rounded-square avatar with the plugin's
    initial.

    Designed to read at any reasonable size from 16 to 96 pixels by
    scaling the corner radius and font with ``size``.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    color = _color_for(plugin_id)
    radius = max(4, size // 5)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.fillPath(path, QBrush(color))

    # Subtle inner border so the tile holds its shape against the
    # dialog's translucent backgrounds.
    pen = QPen(QColor(0, 0, 0, 40))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.drawPath(path)

    initial = _initial_for(plugin_id, name)
    font = QFont()
    font.setBold(True)
    # Glyph height around 55% of the square reads as confident without
    # touching the edges.
    font.setPixelSize(max(8, int(size * 0.55)))
    painter.setFont(font)
    painter.setPen(QPen(QColor("white")))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, initial)

    painter.end()
    return pixmap


def _color_for(plugin_id: str) -> QColor:
    """Deterministic palette pick.

    Using ``hashlib.md5`` rather than ``hash()`` so the result is stable
    across Python interpreters (Python's built-in ``hash`` randomizes
    per-process).
    """
    digest = hashlib.md5(plugin_id.encode("utf-8")).digest()
    index = digest[0] % len(_PALETTE)
    return QColor(_PALETTE[index])


def _initial_for(plugin_id: str, name: str) -> str:
    """Return the single character drawn inside the avatar.

    Prefer the first uppercase letter of the display name; fall back to
    the first character of the plugin id. Always uppercase.
    """
    source = name.strip() or plugin_id.strip() or "?"
    for ch in source:
        if ch.isalnum():
            return ch.upper()
    return "?"
