# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A header above an editor's text that scrolls away with it.

    ┌ editor ──────────────────────────────────┐
    │   ┌ header (the text column's width) ┐   │  shown at the top of the
    │   │ Why I write in plain text        │   │  text, in the editor's
    │   └──────────────────────────────────┘   │  top inset
    │   Every tool I loved is gone. My files   │  the document
    │   are still here …                       │
    └──────────────────────────────────────────┘

Something that belongs with a document without being part of it (an
article's title, summary and cover) is shown where the document starts,
in the text column, and leaves as the text scrolls, as a page's head
does. It is never in the document: Undo, saving and exporting don't see
it, and the text is not changed to make room for it.

A QTextEdit has no room above its text that scrolls (a margin of the
document would be an edit, with a step of Undo), so the room is the
editor's top inset (HtmlEditor.set_top_inset), and :class:`EditorHeader`
makes the two read as one page:

  * Scrolling down takes the header away first, at the speed of the text;
    then the text scrolls. Scrolling up at the top of the text brings it
    back, and so does reaching the first line (the scroll bar at the top,
    the caret on the first line).
  * Up on the first line of text goes into the header, at its last field
    (:meth:`HeaderWidget.enter_from_below`); the header hands the caret
    back to the start of the text with ``leave_down``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QPainter, QTextCursor
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid


class HeaderWidget(QWidget):
    """What an :class:`EditorHeader` shows: a widget that knows how tall it
    is for a width, takes the caret from below, and hands it back."""

    # The caret leaves the header downward, to the start of the text.
    leave_down = Signal()

    def height_for(self, width: int) -> int:
        """How tall the header is at ``width``, within its least and most
        height, as a layout would make it."""
        wanted = self.heightForWidth(width) if self.hasHeightForWidth() else -1
        if wanted < 0:
            wanted = self.sizeHint().height()
        return max(self.minimumHeight(), min(self.maximumHeight(), wanted))

    def enter_from_below(self) -> None:
        """The caret comes up from the text: focus the last field."""
        self.setFocus(Qt.FocusReason.BacktabFocusReason)


class _Ground(QWidget):
    """Where the header shows: clipped to the part in view, on the same
    ground as the text (the page in Paper Mode)."""

    def __init__(self, editor) -> None:
        super().__init__(editor)
        self._editor = editor

    def paintEvent(self, _event) -> None:  # noqa: N802, Qt's name
        QPainter(self).fillRect(self.rect(), self._editor.surface_color())


class EditorHeader(QObject):
    """Shows ``header`` above ``editor``'s text (see the module). Remove it
    with :meth:`detach`."""

    def __init__(self, editor, header: HeaderWidget) -> None:
        super().__init__(editor)
        self._editor = editor
        self._viewport = editor.viewport()
        self._header = header
        # How much of the header shows, from 0 (scrolled away) to its height.
        self._height = 0
        self._shown = 0
        # The scroll bar's value as last seen, to tell which way it went.
        self._last_value = editor.verticalScrollBar().value()
        self._adjusting = False
        # Clips the header to the part that shows; the header slides in it.
        self._clip = _Ground(editor)
        self._clip.setObjectName("editor_header_clip")
        header.setParent(self._clip)
        header.leave_down.connect(self._to_text)
        editor.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        editor.cursorPositionChanged.connect(self._on_caret_moved)
        editor.installEventFilter(self)
        self._viewport.installEventFilter(self)
        # A header that grows or shrinks asks its parent to lay it out again.
        self._clip.installEventFilter(self)
        self._clip.show()
        header.show()
        self.relayout(show=True)

    @property
    def header(self) -> HeaderWidget:
        return self._header

    def shown(self) -> int:
        """How much of the header shows, in pixels."""
        return self._shown

    def detach(self) -> None:
        """Take the header away; the text has its room back."""
        editor = self._editor
        editor.verticalScrollBar().valueChanged.disconnect(self._on_scrolled)
        editor.cursorPositionChanged.disconnect(self._on_caret_moved)
        editor.removeEventFilter(self)
        self._viewport.removeEventFilter(self)
        self._header.leave_down.disconnect(self._to_text)
        self._clip.hide()
        self._clip.deleteLater()
        editor.set_top_inset(0)
        self.deleteLater()

    # -- placing it -------------------------------------------------------------

    def _column(self):
        """The text column, in the editor's coordinates: the viewport less
        the document's margin on each side."""
        viewport = self._editor.viewport().geometry()
        margin = int(self._editor.document().documentMargin())
        return viewport.left() + margin, max(1, viewport.width() - 2 * margin)

    def relayout(self, *, show: bool = False) -> None:
        """Measure the header again (its content or the column changed);
        ``show`` brings all of it into view."""
        left, width = self._column()
        height = self._header.height_for(width)
        if show or self._shown >= self._height:
            self._shown = height                 # shown in full stays in full
        self._height = height
        self._shown = min(self._shown, height)
        self._place(left, width)

    def _place(self, left: Optional[int] = None, width: Optional[int] = None) -> None:
        if left is None:
            left, width = self._column()
        top = self._editor.contentsRect().top()
        # The room changes the viewport's height, and with it the scroll
        # bar's range: not a scroll by the person.
        self._adjusting = True
        self._editor.set_top_inset(self._shown)
        self._adjusting = False
        self._last_value = self._editor.verticalScrollBar().value()
        self._clip.setGeometry(left, top, width, self._shown)
        self._header.setGeometry(0, self._shown - self._height, width, self._height)

    def _show_by(self, pixels: int) -> int:
        """Show ``pixels`` more of the header (fewer when negative); returns
        how many it took."""
        before = self._shown
        self._shown = max(0, min(self._height, self._shown + pixels))
        if self._shown != before:
            self._place()
        return self._shown - before

    # -- following the text -------------------------------------------------------

    def _on_scrolled(self, value: int) -> None:
        if self._adjusting:
            return
        bar = self._editor.verticalScrollBar()
        before = self._last_value
        moved = value - before
        if moved > 0 and self._shown:
            # Down: the header goes first; the text moves by what is left.
            taken = -self._show_by(-moved)
            self._adjusting = True
            bar.setValue(before + moved - taken)
            self._adjusting = False
        elif value == bar.minimum() and moved < 0:
            self._show_by(self._height)          # back at the top: all of it
        self._last_value = bar.value()

    def _on_caret_moved(self) -> None:
        bar = self._editor.verticalScrollBar()
        cursor = self._editor.textCursor()
        if bar.value() == bar.minimum() and _on_first_line(cursor):
            self._show_by(self._height)

    def _to_text(self) -> None:
        editor = self._editor
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched, event) -> bool:
        if not isValid(self._editor):
            return False                          # the editor is going away
        kind = event.type()
        if watched is self._clip and kind == QEvent.Type.LayoutRequest:
            self.relayout()
        elif watched is self._viewport and kind == QEvent.Type.Resize:
            # After the editor has set its margins: the column as it is now.
            self.relayout()
        elif watched is self._viewport and kind == QEvent.Type.Wheel:
            return self._wheel(event)
        elif (watched is self._editor and kind == QEvent.Type.KeyPress
              and event.key() == Qt.Key.Key_Up
              and event.modifiers() in (Qt.KeyboardModifier.NoModifier,
                                        Qt.KeyboardModifier.KeypadModifier)
              and _on_first_line(self._editor.textCursor())):
            self._show_by(self._height)
            self._header.enter_from_below()
            return True
        return False

    def _wheel(self, event) -> bool:
        """Up at the top of the text brings the header back; anything else is
        the editor's (scrolling down takes it away in _on_scrolled)."""
        bar = self._editor.verticalScrollBar()
        pixels = event.pixelDelta().y() or round(
            event.angleDelta().y() / 120 * 3 * self._editor.fontMetrics().lineSpacing())
        if pixels > 0 and bar.value() == bar.minimum() and self._shown < self._height:
            self._show_by(pixels)
            return True
        return False


def _on_first_line(cursor: QTextCursor) -> bool:
    """Whether ``cursor`` is on the document's first line."""
    block = cursor.block()
    if block.previous().isValid():
        return False
    layout = block.layout()
    return layout is None or layout.lineCount() == 0 or \
        layout.lineForTextPosition(cursor.positionInBlock()).lineNumber() == 0
