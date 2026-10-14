"""How code blocks and quotes look on screen, wherever a text is shown.

The editor and the preview draw them alike, as every reader does: a
rounded box behind each code block, with room around its lines, and a bar
beside each level of a quote. Only painted, never put into the document,
so nothing of it is saved or published.

Both views call the painters from their paintEvent, under the text.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

import rich_text

# Room between a code block's lines and the edge of its box.
CODE_PAD_X = 10
CODE_PAD_Y = 4
CODE_RADIUS = 6

QUOTE_BAR_WIDTH = 3


def code_box_color(dark: bool) -> QColor:
    return QColor("#2D2F33") if dark else QColor("#F1F3F5")


def quote_bar_color(dark: bool) -> QColor:
    return QColor("#6B6F76") if dark else QColor("#C3C8CE")


def quote_bar_x(left_margin: float, depth: int, level: int) -> float:
    """Where the bar of quote ``level`` (0 the outermost) stands, from the
    paragraph's left edge: a third into the indent of its level, so the
    text keeps clear of it."""
    indent = left_margin / depth if depth and left_margin > 0 else rich_text.QUOTE_INDENT
    return level * indent + round(indent * 0.3)


def _first_visible(view):
    return view.cursorForPosition(QPoint(0, 0)).block()


def _offset(view):
    return view.horizontalScrollBar().value(), view.verticalScrollBar().value()


def paint_code_boxes(view, painter: QPainter, color: QColor) -> None:
    """One box behind each code block in sight of ``view`` (a QTextEdit),
    around all its lines, padded by CODE_PAD_X and CODE_PAD_Y."""
    height = view.viewport().height()
    layout = view.document().documentLayout()
    dx, dy = _offset(view)
    block = _first_visible(view)
    if rich_text.is_code_block(block):
        block = rich_text.code_run(block)[0]
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    while block.isValid():
        rect = layout.blockBoundingRect(block)
        if rect.top() - dy > height:
            break
        if not rich_text.is_code_block(block):
            block = block.next()
            continue
        first, last = rich_text.code_run(block)
        # A block's rect runs from its first line to its last, its top
        # and bottom margins outside it; across, from the edge its left
        # margin is measured from.
        top_rect, fmt = layout.blockBoundingRect(first), first.blockFormat()
        left = top_rect.left() + fmt.leftMargin() - CODE_PAD_X - dx
        right = top_rect.right() - fmt.rightMargin() + CODE_PAD_X - dx
        top = top_rect.top() - CODE_PAD_Y - dy
        bottom = layout.blockBoundingRect(last).bottom() + CODE_PAD_Y - dy
        painter.drawRoundedRect(QRectF(left, top, right - left, bottom - top),
                                CODE_RADIUS, CODE_RADIUS)
        block = last.next()
    painter.restore()


def paint_quote_bars(view, painter: QPainter, color: QColor) -> None:
    """A bar beside each level of each quote in sight of ``view``, unbroken
    from one paragraph of a quote to the next."""
    height = view.viewport().height()
    layout = view.document().documentLayout()
    dx, dy = _offset(view)
    block = _first_visible(view)
    while block.isValid():
        rect = layout.blockBoundingRect(block)
        if rect.top() - dy > height:
            break
        depth = rich_text.quote_depth(block)
        if depth:
            fmt = block.blockFormat()
            previous = block.previous()
            above = rich_text.quote_depth(previous) if previous.isValid() else 0
            for level in range(depth):
                # A bar runs on through the space between two paragraphs
                # of the same quote: up to the one before, when it is in
                # the quote at this level too.
                top = layout.blockBoundingRect(previous).bottom() if above > level else rect.top()
                x = rect.left() + quote_bar_x(fmt.leftMargin(), depth, level)
                painter.fillRect(QRectF(x - dx, top - dy, QUOTE_BAR_WIDTH, rect.bottom() - top),
                                 color)
        block = block.next()
