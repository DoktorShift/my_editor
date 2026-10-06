#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
HTML Editor widget with bullet and format logic.
"""

import os
import re
import sys

from PySide6.QtCore import (
    Qt, QTimer, QRect, QPoint, QMetaMethod, QEvent, QMimeData, QUrl, Signal,
)
from PySide6.QtGui import (
    QPainter, QTextBlockFormat, QTextCursor, QTextCharFormat, QColor, QClipboard, QPen,
    QTextOption, QImage, QPixmap, QTextDocument, QTextFormat, QDesktopServices,
)
from PySide6.QtWidgets import QTextEdit, QMenu, QApplication, QToolTip
from constants import (
    DARK_BG, DARK_FG, LIGHT_BG, LIGHT_FG, DARK_SELECTION, LIGHT_SELECTION,
    DARK_GUIDE, LIGHT_GUIDE, DARK_CURRENT_LINE, LIGHT_CURRENT_LINE, DARK_PAPER, LIGHT_PAPER,
)
from fonts import code_font, writing_font
from i18n import _, ngettext
import image_safety
import link_url
import paste
import rich_text
import url_safety
from link_popover import LinkPopover, clipboard_address


# Characters per line when writing: within the 50 to 75 that typography
# (and nostrdesign.org's UI tips) give for comfortable reading.
WRITING_MEASURE = 72

# Stand-in painted for an image whose bytes have not arrived yet. Its
# size is deliberately modest: it is replaced in place once the real
# image resolves, and a large box would reflow the whole document twice.
_PLACEHOLDER_SIZE = (160, 100)
_PLACEHOLDER_COLOR = "#c8c8c8"


def _n_pictures_left_out(count: int) -> str:
    return ngettext("A picture could not be pasted with the text. Copy it on its own "
                    "and paste it.",
                    "{count} pictures could not be pasted with the text. Copy each on its "
                    "own and paste it.", count).format(count=count)


def _is_document_relative(url) -> bool:
    """Whether a resource name means "beside the document".

    A name with no scheme and no absolute path belongs to the directory
    the file came from. Qt's own answer is the process working
    directory, which is wherever the app happened to be launched from
    and names a different file entirely.
    """
    if url.scheme():
        return False
    name = url.toString()
    return bool(name) and not name.startswith(("/", "\\")) and not os.path.isabs(name)


class HtmlEditor(QTextEdit):
    """QTextEdit with bullet and format logic."""

    # Media entry points. The editor knows nothing about where an image
    # goes or who uploads it; it reports the gesture and lets whoever is
    # connected decide. Nothing is emitted when nobody is listening, so
    # the widget stays usable on its own (tests, previews).
    image_pasted = Signal(object)    # QImage from the clipboard
    urls_dropped = Signal(list)      # list[QUrl] dropped on the editor (or image files pasted)
    # A short message for the status bar about what the editor just did
    # (a paste that had to leave pictures out).
    notice = Signal(str)
    # The part of the document on screen changed (scrolled, resized):
    # whoever paints only what is visible (find highlights, spelling)
    # paints again.
    visible_area_changed = Signal()

    # Kinds of highlights shown over the text, bottom to top: each kind
    # keeps its own (set_highlights), the editor shows them together.
    HIGHLIGHT_LAYERS = ("spelling", "find")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setUndoRedoEnabled(True)
        # What a screen reader calls the text area until the window names
        # it after its document.
        self.setAccessibleName(_("Document"))

        # Who fills the context menu: the window puts its own commands
        # there (the same ones as in its menus). Without one, the menu has
        # Cut, Copy and Paste.
        self._context_menu_filler = None
        # Whether the document can hold Markdown structure (lists,
        # headings): the window answers per tab; on its own, it can.
        self._holds_structure = lambda: True
        # Who opens a link Command-clicked (Ctrl-clicked elsewhere): the
        # window, which knows where each kind of link goes.
        self._link_opener = None
        # Whether a Nostr account is in use: Nostr shows up in the editor
        # only then (nostr/state.py).
        self._nostr_active = lambda: False

        # Resource seam: a resolver plus the URL scheme it answers for.
        # Injected by the window so the editor carries no knowledge of
        # what an asset is or where its bytes live.
        self._resource_resolver = None
        self._asset_scheme = None
        self._asset_placeholder = None
        self._local_image_resolver = None

        # Always wrap to the visible viewport width, breaking long unbroken
        # tokens instead of forcing a horizontal scrollbar. This keeps
        # reflow correct across Paper Mode toggles and window resizes.
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

        self.setStyleSheet(f"""
            QTextEdit {{
                background: {DARK_BG};
                color: {DARK_FG};
                border: none;
                selection-background-color: {DARK_SELECTION};
                padding: 8px;
            }}
        """)
        # Code and plain text are set in the monospace font; writing gets
        # the system's text font (set_writing_font). The font is set here,
        # not in the style sheet, which could only name a family.
        self.setFont(code_font())
        # Characters per line when writing; None for code (full width).
        self._writing_measure_chars = None
        self._highlights = {layer: [] for layer in self.HIGHLIGHT_LAYERS}
        self.verticalScrollBar().valueChanged.connect(self.visible_area_changed)

        # Track active formatting state for persistent formatting
        self.active_format = {
            'bold': False,
            'italic': False,
            'underline': False,
            'color': None
        }

        # Connect to cursor position changes to update active format
        self.cursorPositionChanged.connect(self._update_active_format)

        # Block cursor (terminal-style): hide Qt's thin cursor, draw our own
        self.setCursorWidth(0)
        self._cursor_visible = True
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._on_cursor_blink)
        self._blink_timer.start()

        # Background viewing aids (all painted behind the text in paintEvent).
        self._bg_pattern = "none"        # none | lines | dashed | dots | grid
        self._paper_mode = False
        self._highlight_line = False
        self._paper_measure_chars = 88   # target column measure in Paper Mode
        self.viewport().setAutoFillBackground(False)

    def undo(self):
        self.document().undo()

    def redo(self):
        self.document().redo()

    # -------- Resource resolution --------
    def set_resource_resolver(self, resolver, scheme: str) -> None:
        """Install the callback that turns a ``scheme:`` name into a QImage.

        ``resolver`` may return None; the placeholder is shown then.
        """
        self._resource_resolver = resolver
        self._asset_scheme = scheme or None

    def set_local_image_resolver(self, resolver) -> None:
        """Install the callback that reads an image named beside the file.

        ``resolver`` takes the name exactly as the document spells it
        and returns a QImage or None. Injected so the editor holds no
        opinion about where the document lives or how bytes decode.
        """
        self._local_image_resolver = resolver

    def _placeholder_image(self) -> QImage:
        """One shared stand-in image, built on first use."""
        if self._asset_placeholder is None:
            image = QImage(*_PLACEHOLDER_SIZE, QImage.Format_RGB32)
            image.fill(QColor(_PLACEHOLDER_COLOR))
            self._asset_placeholder = image
        return self._asset_placeholder

    def loadResource(self, type_, url):
        """Resolve a document resource, keeping asset names off the disk.

        An asset name is never passed to Qt's default resolution: Qt
        would treat it as a relative file path. A miss returns the
        placeholder rather than nothing, because Qt caches an image but
        re-asks for a null result on every repaint.

        A document-relative image goes to the local resolver for the
        same reason it never falls through: Qt's fallback reads the
        name against the working directory, which is not the folder the
        picture was saved beside.
        """
        if self._asset_scheme is not None and url.scheme() == self._asset_scheme:
            if type_ != QTextDocument.ImageResource:
                return None
            image = None
            if self._resource_resolver is not None:
                image = self._resource_resolver(url.toString())
            return image if image is not None else self._placeholder_image()
        if (type_ == QTextDocument.ImageResource
                and self._local_image_resolver is not None
                and _is_document_relative(url)):
            image = self._local_image_resolver(url.toString())
            return image if image is not None else self._placeholder_image()
        return super().loadResource(type_, url)

    # -------- Format Toggles --------
    def _all_in_selection(self, cursor: QTextCursor, check) -> bool:
        """Return True if every text fragment in the selection passes check(QTextCharFormat).
        Uses block/fragment iteration (efficient, no char-by-char loop)."""
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        block = doc.findBlock(start)
        while block.isValid() and block.position() < end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    frag_start = frag.position()
                    frag_end = frag_start + frag.length()
                    if frag_end > start and frag_start < end:
                        if not check(frag.charFormat()):
                            return False
                it += 1
            block = block.next()
        return True

    def toggle_bold(self):
        cursor = self.textCursor()
        fmt = QTextCharFormat()
        if cursor.hasSelection():
            all_bold = self._all_in_selection(cursor, lambda f: f.fontWeight() > 400)
            new_weight = 400 if all_bold else 700
            fmt.setFontWeight(new_weight)
            cursor.mergeCharFormat(fmt)
            self.setTextCursor(cursor)
        else:
            current = self.currentCharFormat()
            new_weight = 400 if current.fontWeight() > 400 else 700
            fmt.setFontWeight(new_weight)
            self.mergeCurrentCharFormat(fmt)
        self.active_format['bold'] = (new_weight > 400)

    def toggle_italic(self):
        cursor = self.textCursor()
        fmt = QTextCharFormat()
        if cursor.hasSelection():
            all_italic = self._all_in_selection(cursor, lambda f: f.fontItalic())
            new_italic = not all_italic
            fmt.setFontItalic(new_italic)
            cursor.mergeCharFormat(fmt)
            self.setTextCursor(cursor)
        else:
            current = self.currentCharFormat()
            new_italic = not current.fontItalic()
            fmt.setFontItalic(new_italic)
            self.mergeCurrentCharFormat(fmt)
        self.active_format['italic'] = new_italic

    def toggle_underline(self):
        cursor = self.textCursor()
        fmt = QTextCharFormat()
        if cursor.hasSelection():
            all_underline = self._all_in_selection(cursor, lambda f: f.fontUnderline())
            new_underline = not all_underline
            fmt.setFontUnderline(new_underline)
            cursor.mergeCharFormat(fmt)
            self.setTextCursor(cursor)
        else:
            current = self.currentCharFormat()
            new_underline = not current.fontUnderline()
            fmt.setFontUnderline(new_underline)
            self.mergeCurrentCharFormat(fmt)
        self.active_format['underline'] = new_underline

    def apply_color(self, qcolor: QColor | None):
        cursor = self.textCursor()
        if qcolor is None:
            if cursor.hasSelection():
                # mergeCharFormat can only set properties, not clear them.
                # Must iterate character by character to actually remove the color.
                start, end = cursor.selectionStart(), cursor.selectionEnd()
                c = QTextCursor(self.document())
                c.beginEditBlock()
                c.setPosition(start)
                while c.position() < end:
                    c.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
                    char_fmt = c.charFormat()
                    char_fmt.clearForeground()
                    c.setCharFormat(char_fmt)
                    c.setPosition(c.position())
                c.endEditBlock()
            else:
                char_fmt = self.currentCharFormat()
                char_fmt.clearForeground()
                self.setCurrentCharFormat(char_fmt)
        else:
            fmt = QTextCharFormat()
            fmt.setForeground(qcolor)
            if cursor.hasSelection():
                cursor.mergeCharFormat(fmt)
            else:
                self.mergeCurrentCharFormat(fmt)
        self.active_format['color'] = qcolor
        self.ensureCursorVisible()

    def toggle_style(self, style: str) -> bool:
        """Turn an inline style (rich_text.INLINE) on or off: over the
        whole selection, or for what is typed next. Returns the new state."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            on = rich_text.toggle_style(cursor, style)
            self.setTextCursor(cursor)
            return on
        on = not rich_text.has_style(self.currentCharFormat(), style)
        if on or style != rich_text.CODE:
            self.mergeCurrentCharFormat(rich_text.style_format(style, on))
        else:
            fmt = self.currentCharFormat()
            fmt.setFontFixedPitch(False)
            fmt.clearProperty(QTextFormat.Property.FontFamilies)
            fmt.clearProperty(QTextFormat.Property.FontFamily)
            self.setCurrentCharFormat(fmt)
        return on

    def set_heading(self, level: int) -> None:
        """Body (0) or Heading 1 to 3 for the paragraphs under the cursor;
        the style they already have turns them back into Body."""
        cursor = self.textCursor()
        rich_text.set_heading(cursor, level)
        self.setTextCursor(cursor)
        now = cursor.block().blockFormat().headingLevel()
        self.setCurrentCharFormat(rich_text.restyled(self.currentCharFormat(), now))
        self._update_active_format()

    def toggle_strike(self):
        return self.toggle_style(rich_text.STRIKE)

    def toggle_code(self):
        return self.toggle_style(rich_text.CODE)

    def reset_to_default(self):
        """Clear Formatting: no bold, italic, underline, strikethrough,
        inline code or color, over the selection or for what is typed
        next. A link stays a link."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            rich_text.clear_formatting(cursor)
            self.setTextCursor(cursor)
        else:
            self.setCurrentCharFormat(rich_text.typing_format_without(self.currentCharFormat()))

        self.active_format['bold'] = False
        self.active_format['italic'] = False
        self.active_format['underline'] = False
        self.active_format['color'] = None
        self.ensureCursorVisible()

    def _update_active_format(self):
        """Update active format state based on current cursor position."""
        self._leave_link_at_end()
        fmt = self.currentCharFormat()
        self.active_format['bold'] = (fmt.fontWeight() > 400)
        self.active_format['italic'] = fmt.fontItalic()
        self.active_format['underline'] = fmt.fontUnderline()
        self.active_format['color'] = fmt.foreground().color() if fmt.hasProperty(QTextCharFormat.ForegroundBrush) else None

    # -------- Block cursor --------
    def _block_cursor_rect(self):
        """Return the rect the block cursor occupies (used for painting and invalidation)."""
        rect = self.cursorRect()
        rect.setWidth(max(self.fontMetrics().averageCharWidth(), 10))
        return rect

    def _on_cursor_blink(self):
        self._cursor_visible = not self._cursor_visible
        self.viewport().update(self._block_cursor_rect())

    # -------- Background viewing aids --------
    def set_background_pattern(self, name: str):
        self._bg_pattern = name or "none"
        self.viewport().update()

    def set_highlight_current_line(self, on: bool):
        self._highlight_line = bool(on)
        self.viewport().update()

    def set_paper_mode(self, on: bool):
        self._paper_mode = bool(on)
        self._update_paper_margins()
        self.viewport().update()

    def set_writing_font(self, writing: bool) -> None:
        """Writing (Markdown, drafts): the system's text font at a reading
        size, in a column of a comfortable line length. Otherwise (code,
        plain text): the monospace font across the whole width."""
        self.setFont(writing_font() if writing else code_font())
        self._writing_measure_chars = WRITING_MEASURE if writing else None
        self._update_paper_margins()
        self.viewport().update()

    def _update_paper_margins(self):
        """The text column: Paper Mode's page, or the writing measure, or
        the whole width."""
        chars = self._paper_measure_chars if self._paper_mode else self._writing_measure_chars
        if not chars:
            self.setViewportMargins(0, 0, 0, 0)
            return
        metrics = self.fontMetrics()
        char_w = (metrics.averageCharWidth() if self._writing_measure_chars
                  else metrics.horizontalAdvance("0")) or 8
        measure = char_w * chars
        side = int(max(0, (self.width() - measure) / 2))
        self.setViewportMargins(side, 0, side, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._paper_mode or self._writing_measure_chars:
            self._update_paper_margins()
        self.visible_area_changed.emit()

    # -------- Highlights over the text --------
    def set_highlights(self, layer: str, selections) -> None:
        """Show ``selections`` (QTextEdit.ExtraSelection) as the highlights
        of one kind (HIGHLIGHT_LAYERS), in place of that kind's earlier
        ones; the other kinds stay. Each highlight holds a cursor the
        document moves on every edit, so callers keep them to what is on
        screen (visible_range)."""
        self._highlights[layer] = list(selections)
        self.setExtraSelections([s for name in self.HIGHLIGHT_LAYERS
                                 for s in self._highlights[name]])

    def highlights(self, layer: str) -> list:
        return list(self._highlights[layer])

    def visible_range(self):
        """``(first, last)``: the document positions from the start of the
        first paragraph on screen to the end of the last."""
        viewport = self.viewport()
        top = self.cursorForPosition(QPoint(0, 0)).block()
        bottom = self.cursorForPosition(
            QPoint(viewport.width() - 1, viewport.height() - 1)).block()
        return top.position(), bottom.position() + bottom.length()

    def _is_dark(self) -> bool:
        return hasattr(self, "_theme_colors") and self._theme_colors["bg"] == DARK_BG

    def _surface_color(self) -> QColor:
        if self._paper_mode:
            return DARK_PAPER if self._is_dark() else LIGHT_PAPER
        bg = self._theme_colors["bg"] if hasattr(self, "_theme_colors") else DARK_BG
        return QColor(bg)

    def paintEvent(self, event):
        vp = self.viewport()
        painter = QPainter(vp)

        # We own the background now (the stylesheet sets it transparent), so fill
        # it ourselves. This is what lets the guides sit behind the text.
        painter.fillRect(vp.rect(), self._surface_color())

        if (self._highlight_line and self.hasFocus()
                and not self.textCursor().hasSelection()):
            line_rect = self.cursorRect()
            band = QRect(0, line_rect.top(), vp.width(), line_rect.height())
            painter.fillRect(band, DARK_CURRENT_LINE if self._is_dark() else LIGHT_CURRENT_LINE)

        if self._bg_pattern != "none" or self._paper_mode:
            self._paint_guides(painter)

        self._paint_quote_bars(painter)
        painter.end()

        super().paintEvent(event)

        # Block cursor on top of the text (unchanged behavior).
        cursor = self.textCursor()
        if not self.hasFocus() or not self._cursor_visible or cursor.hasSelection():
            return
        rect = self._block_cursor_rect()
        color = QColor(212, 212, 212, 210) if self._is_dark() else QColor(51, 51, 51, 210)
        p2 = QPainter(vp)
        p2.fillRect(rect, color)
        p2.end()

    def _paint_quote_bars(self, painter):
        """A bar beside each level of a quote, as every reader draws one.
        Only painted: nothing of it is in the document."""
        vp = self.viewport()
        block = self.cursorForPosition(QPoint(0, 0)).block()
        layout = self.document().documentLayout()
        dx = self.horizontalScrollBar().value()
        dy = self.verticalScrollBar().value()
        color = QColor("#5A5D63") if self._is_dark() else QColor("#C9CDD2")
        while block.isValid():
            rect = layout.blockBoundingRect(block)
            top = int(rect.top()) - dy
            if top > vp.height():
                break
            depth = rich_text.quote_depth(block)
            if depth:
                fmt = block.blockFormat()
                height = int(rect.height() - fmt.topMargin() - fmt.bottomMargin())
                for level in range(depth):
                    x = int(rect.left()) - dx + level * rich_text.QUOTE_INDENT + 12
                    painter.fillRect(QRect(x, top + int(fmt.topMargin()), 3, height), color)
            block = block.next()

    def _paint_guides(self, painter):
        vp = self.viewport()
        guide = DARK_GUIDE if self._is_dark() else LIGHT_GUIDE
        padding = 8  # matches the stylesheet padding on the editor
        width = vp.width()
        height = vp.height()

        solid = QPen(guide)
        solid.setCosmetic(True)
        solid.setWidth(1)

        pattern = self._bg_pattern
        if pattern in ("lines", "dashed", "grid", "dots"):
            line_ys, pitch = self._rule_line_ys(height)
        else:
            line_ys, pitch = [], 0

        if pattern in ("lines", "dashed"):
            pen = QPen(guide)
            pen.setCosmetic(True)
            pen.setWidth(1)
            if pattern == "dashed":
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            for y in line_ys:
                painter.drawLine(padding, y, width - padding, y)

        elif pattern in ("grid", "dots") and pitch > 0 and line_ys:
            xs = list(range(padding, width - padding + 1, pitch))
            if pattern == "grid":
                painter.setPen(solid)
                for y in line_ys:
                    painter.drawLine(padding, y, width - padding, y)
                top, bottom = line_ys[0], line_ys[-1]
                for x in xs:
                    painter.drawLine(x, top, x, bottom)
            else:  # dots
                painter.setPen(Qt.NoPen)
                painter.setBrush(guide)
                for y in line_ys:
                    for x in xs:
                        painter.drawEllipse(QPoint(x, y), 1, 1)

        if self._paper_mode:
            painter.setPen(solid)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(0, 0, 0, height)
            painter.drawLine(width - 1, 0, width - 1, height)

    def _rule_line_ys(self, height):
        """Baseline y (viewport coordinates) of every ruled line across the page.

        Anchored to the real text layout via cursorRect, which maps a document
        position to viewport coordinates exactly (accounting for scroll offset and
        document margins), so each line of text sits on a rule with no drift. The
        rhythm is then extended above and below the text to fill the whole page."""
        vp = self.viewport()
        block = self.cursorForPosition(QPoint(0, 0)).block()
        if not block.isValid():
            return [], 0
        baselines = []
        pitch = 0
        b = block
        while b.isValid():
            top = self.cursorRect(QTextCursor(b)).top()
            if top > height:
                break
            bl = b.layout()
            for i in range(bl.lineCount()):
                line = bl.lineAt(i)
                baselines.append(int(round(top + line.y() + line.ascent())))
                if pitch == 0:
                    pitch = int(round(line.height()))
            b = b.next()
        if not baselines:
            return [], 0
        if pitch <= 0:
            pitch = max(int(round(self.fontMetrics().height() * 1.5)), 8)
        y = baselines[0] - pitch
        while y >= -pitch:
            baselines.insert(0, y)
            y -= pitch
        y = baselines[-1] + pitch
        while y <= height + pitch:
            baselines.append(y)
            y += pitch
        return baselines, pitch

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._cursor_visible = True
        self._blink_timer.start()
        self.viewport().update(self._block_cursor_rect())

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._cursor_visible = False
        self._blink_timer.stop()
        self.viewport().update(self._block_cursor_rect())

    def set_structure_check(self, check) -> None:
        """Install ``check()``: whether this document holds Markdown
        structure. Where it does not (a .txt file, code, Markdown opened
        as its text), Tab keeps typing bullets as text."""
        self._holds_structure = check

    # -------- Lists --------
    def toggle_list(self, kind: str) -> None:
        """Bulleted (rich_text.BULLET) or Numbered List (rich_text.NUMBER)
        for the paragraphs under the cursor; again, it is taken away."""
        cursor = self.textCursor()
        rich_text.toggle_list(cursor, kind)
        self.setTextCursor(cursor)

    def change_indent(self, delta: int) -> None:
        """Increase (+1) or Decrease (-1) Indent: list nesting, or the
        indent of a bullet typed as text."""
        cursor = self.textCursor()
        if rich_text.change_indent(cursor, delta):
            self.setTextCursor(cursor)
            return
        line, start = self._line_info(cursor)
        spaces, has_bullet = self._indent_level_and_has_bullet(line)
        if has_bullet:
            self._indent_typed_bullet(line, start, spaces, has_bullet, delta)

    # -------- Links --------
    def set_nostr_check(self, check) -> None:
        """Install ``check()``: whether a Nostr account is in use."""
        self._nostr_active = check

    def set_link_opener(self, opener) -> None:
        """Install ``opener(href)``, which opens a Command-clicked link."""
        self._link_opener = opener

    def link_at_caret(self):
        """``(start, end, href)`` of the link at the caret, or None."""
        return rich_text.link_range(self.textCursor())

    def show_link_popover(self) -> None:
        """Add Link, or Edit Link when the caret is in one: the popover
        under the words, prefilled with what is known."""
        cursor = self.textCursor()
        found = rich_text.link_range(cursor)
        if found is not None:
            start, end, href = found
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            text = cursor.selectedText()
        else:
            href = clipboard_address()
            cursor = self._trimmed_selection(cursor)
            self.setTextCursor(cursor)
            text = cursor.selectedText()
        several = "\u2029" in text
        popover = LinkPopover(text=text.replace("\u2029", " ").replace("\u2028", " "),
                              href=href, editing=found is not None,
                              nostr=self._nostr_active(), text_editable=not several,
                              parent=self)
        popover.applied.connect(self.apply_link)
        popover.removed.connect(self.remove_link)
        # Escape, Cancel or a click elsewhere: the writing goes on here.
        # (A method, not a lambda: Qt drops the connection if the editor
        # goes first, a lambda would call into a deleted editor.)
        popover.destroyed.connect(self._link_popover_closed)
        popover.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        start_rect = self.cursorRect(self._selection_start_cursor())
        popover.show_below(start_rect, self.viewport())

    def _link_popover_closed(self, _popover=None) -> None:
        self.setFocus()

    @staticmethod
    def _trimmed_selection(cursor: QTextCursor) -> QTextCursor:
        """The selection without the spaces (and paragraph ends) at its
        edges: they stay outside the link, as words processors keep them,
        instead of being deleted with it."""
        selected = cursor.selectedText()
        if not selected.strip():
            return cursor
        lead = len(selected) - len(selected.lstrip())
        trail = len(selected) - len(selected.rstrip())
        trimmed = QTextCursor(cursor)
        trimmed.setPosition(cursor.selectionStart() + lead)
        trimmed.setPosition(cursor.selectionEnd() - trail, QTextCursor.MoveMode.KeepAnchor)
        return trimmed

    def _selection_start_cursor(self) -> QTextCursor:
        cursor = QTextCursor(self.textCursor())
        cursor.setPosition(cursor.selectionStart())
        return cursor

    def apply_link(self, text: str, href: str) -> None:
        """Link the selection to ``href``: its words as they are, or
        ``text`` in their place when the person retyped them. With nothing
        selected, ``text`` (or else the address itself) is inserted."""
        cursor = self.textCursor()
        if not cursor.hasSelection() and not text:
            text = link_url.display_href(href, limit=10_000)
        rich_text.set_link(cursor, text, href)
        cursor.setPosition(cursor.selectionEnd())
        self.setTextCursor(cursor)
        self._leave_link_at_end()
        self.setFocus()

    def remove_link(self) -> None:
        cursor = self.textCursor()
        rich_text.remove_link(cursor)
        self.setTextCursor(cursor)

    def _open_link(self, href: str) -> None:
        if self._link_opener is not None:
            self._link_opener(href)
            return
        web = link_url.web_address_for(href)
        if web and url_safety.is_safe_external_url(web):
            QDesktopServices.openUrl(QUrl(web))
        elif href.lower().startswith("mailto:"):
            QDesktopServices.openUrl(QUrl(href))

    def _leave_link_at_end(self) -> None:
        """Typing right before or right after a link is not part of it
        (links are not "sticky", as in every word processor); typing
        inside one is."""
        cursor = self.textCursor()
        fmt = self.currentCharFormat()
        if cursor.hasSelection() or not fmt.isAnchor():
            return
        if not cursor.atBlockStart() and not cursor.atBlockEnd():
            after = QTextCursor(cursor)
            after.movePosition(QTextCursor.MoveOperation.NextCharacter)
            before, following = cursor.charFormat(), after.charFormat()
            if (before.isAnchor() and following.isAnchor()
                    and before.anchorHref() == following.anchorHref()):
                return              # inside the link
        self.setCurrentCharFormat(rich_text.without_link(fmt))

    def mousePressEvent(self, event):
        # Command-click (Ctrl-click on Windows and Linux) opens a link.
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            href = self.anchorAt(event.position().toPoint())
            if href:
                self._open_link(href)
                event.accept()
                return
        super().mousePressEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            href = self.anchorAt(event.pos())
            if href:
                how = (_("Command-click to open") if sys.platform == "darwin"
                       else _("Ctrl+click to open"))
                QToolTip.showText(event.globalPos(),
                                  link_url.display_href(href) + "\n" + how, self)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    # -------- Quotes and dividers --------
    def toggle_quote(self) -> None:
        """Quote the paragraphs under the cursor, or take the quote away."""
        cursor = self.textCursor()
        rich_text.toggle_quote(cursor)
        self.setTextCursor(cursor)
        self.viewport().update()

    def insert_divider(self) -> None:
        """A divider after the paragraph at the cursor; typing goes on below it."""
        cursor = self.textCursor()
        rich_text.insert_divider(cursor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    # -------- Context menu --------
    def set_context_menu_filler(self, filler) -> None:
        """Install ``filler(menu, editor, pos)``, which puts the commands in
        the context menu (``pos`` is where it was asked for, in viewport
        coordinates). Injected, so the editor holds no command list."""
        self._context_menu_filler = filler

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if self._is_dark():
            menu.setStyleSheet("""
                QMenu {
                    background: #252526;
                    color: #CCCCCC;
                    border: 1px solid #3C3C3C;
                    padding: 4px;
                }
                QMenu::item { padding: 4px 20px 4px 30px; }
                QMenu::item:selected { background: #1E1E1E; color: #FFFFFF; }
                QMenu::item:disabled { color: #6A6A6A; }
                QMenu::separator { height: 1px; background: #3C3C3C; margin: 4px 0px; }
            """)
        else:
            menu.setStyleSheet("""
                QMenu {
                    background: #F8F8F8;
                    color: #333333;
                    border: 1px solid #E1E1E1;
                    padding: 4px;
                }
                QMenu::item { padding: 4px 20px 4px 30px; }
                QMenu::item:selected { background: #F3F3F3; color: #000000; }
                QMenu::item:disabled { color: #999999; }
                QMenu::separator { height: 1px; background: #E1E1E1; margin: 4px 0px; }
            """)
        if self._context_menu_filler is not None:
            self._context_menu_filler(menu, self, event.pos())
        else:
            menu.addAction(_("Cut"), self.cut)
            menu.addAction(_("Copy"), self.copy)
            menu.addAction(_("Paste"), self.paste_from_clipboard)
        menu.exec(event.globalPos())

    # -------- Bullet Logic (•) --------
    @staticmethod
    def _line_info(cursor: QTextCursor):
        """Get current line text + start position of the line."""
        c = QTextCursor(cursor)
        c.movePosition(QTextCursor.StartOfLine, QTextCursor.MoveAnchor)
        start_pos = c.position()
        c.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        line_text = c.selectedText()
        return line_text, start_pos

    def _indent_level_and_has_bullet(self, line: str):
        spaces = 0
        i = 0
        while i < len(line) and line[i] == ' ':
            spaces += 1
            i += 1
        has_bullet = line[i:i+2] == "• "
        return spaces, has_bullet

    def _set_line_text(self, start_pos: int, new_text: str, cursor_pos_after=None):
        c = self.textCursor()
        c.setPosition(start_pos)
        c.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        c.beginEditBlock()
        c.removeSelectedText()
        c.insertText(new_text)
        c.endEditBlock()

        if cursor_pos_after is None:
            cursor_pos_after = start_pos + len(new_text)

        nc = self.textCursor()
        nc.setPosition(cursor_pos_after)
        self.setTextCursor(nc)

    def _indent_typed_bullet(self, line: str, start: int, spaces: int, has_bullet: bool,
                             delta: int) -> None:
        """Tab and Shift+Tab on a line with a bullet typed as text ("• "):
        four spaces more or less, and a bullet for a line that has none."""
        tab_width = 4
        if delta > 0:
            new_line = " " * (spaces + tab_width)
            if not has_bullet:
                new_line += "• " + line.lstrip()
            else:
                new_line += line[spaces:]
        else:
            new_line = " " * max(0, spaces - tab_width) + line[spaces:]
        bullet_pos = new_line.find("• ")
        move_to = start + bullet_pos + 2 if bullet_pos >= 0 else start + len(new_line.rstrip())
        self._set_line_text(start, new_line, move_to)
        if delta > 0:
            self._apply_active_format_to_cursor()

    def keyPressEvent(self, e):
        # Keep cursor solid immediately after a keypress; blink restarts from now
        old_cursor_rect = self._block_cursor_rect()
        self._cursor_visible = True
        self._blink_timer.start()
        self.viewport().update(old_cursor_rect)

        # Undo / Redo
        if e.key() == Qt.Key_Z and e.modifiers() == Qt.ControlModifier:
            self.undo()
            return
        if (e.key() == Qt.Key_Z and e.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier)) or \
           (e.key() == Qt.Key_Y and e.modifiers() == Qt.ControlModifier):
            self.redo()
            return

        if e.text() and e.text().isprintable():
            # The caret may have arrived at a link's edge without moving
            # (a document loaded around it): what is typed is not the link.
            self._leave_link_at_end()

        if self._structure_key(e):
            self.ensureCursorVisible()
            return

        c = self.textCursor()
        line, start = self._line_info(c)
        spaces, has_bullet = self._indent_level_and_has_bullet(line)

        if e.key() == Qt.Key_Tab:
            if c.atBlockStart() or (c.positionInBlock() <= spaces + (2 if has_bullet else 0)):
                self._indent_typed_bullet(line, start, spaces, has_bullet, +1)
                self.ensureCursorVisible()
                return

        elif e.key() == Qt.Key_Backtab:
            if c.atBlockStart() or (c.positionInBlock() <= spaces + (2 if has_bullet else 0)):
                self._indent_typed_bullet(line, start, spaces, has_bullet, -1)
                self.ensureCursorVisible()
                return

        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if has_bullet:
                content_after_bullet = line[spaces + 2:].strip()
                cursor_pos_in_block = c.positionInBlock()
                bullet_end_pos = spaces + 2
                is_empty_bullet = (content_after_bullet == "") and (cursor_pos_in_block <= bullet_end_pos)

                if is_empty_bullet:
                    # Double Enter: remove bullet, start plain line
                    c.beginEditBlock()
                    c.movePosition(QTextCursor.StartOfBlock)
                    c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                    c.removeSelectedText()
                    c.insertText("\n")
                    c = self.textCursor()
                    c.movePosition(QTextCursor.StartOfBlock)
                    fmt = QTextCharFormat()
                    fmt.setFontWeight(400)
                    fmt.setFontItalic(False)
                    fmt.setFontUnderline(False)
                    fmt.clearForeground()
                    c.setCharFormat(fmt)
                    self.mergeCurrentCharFormat(fmt)
                    self.active_format.update({'bold': False, 'italic': False, 'underline': False, 'color': None})
                    c.endEditBlock()
                    self.setTextCursor(c)
                    self.ensureCursorVisible()
                    return
                else:
                    # Continue bullet on next line
                    c.beginEditBlock()
                    c.insertText("\n" + " " * spaces + "• ")
                    c = self.textCursor()
                    self._apply_active_format_to_cursor()
                    c.endEditBlock()
                    self.setTextCursor(c)
                    self.ensureCursorVisible()
                    return
            else:
                super().keyPressEvent(e)
                self._apply_active_format_to_cursor()
                self.ensureCursorVisible()
                return

        elif e.key() == Qt.Key_Backspace:
            if not c.hasSelection() and c.positionInBlock() <= spaces + (2 if has_bullet else 0) and (spaces > 0 or has_bullet):
                if has_bullet:
                    if spaces == 0 and c.blockNumber() > 0:
                        # No indentation, not first block: remove bullet and merge with previous line
                        cursor = self.textCursor()
                        cursor.beginEditBlock()
                        cursor.setPosition(start)
                        cursor.setPosition(start + 2, QTextCursor.KeepAnchor)
                        cursor.removeSelectedText()
                        cursor.deletePreviousChar()  # delete preceding newline to merge blocks
                        cursor.endEditBlock()
                        self.setTextCursor(cursor)
                    else:
                        # Indented bullet, or first block: remove bullet only, cursor stays at content start
                        new_line = " " * spaces + line[spaces + 2:]
                        self._set_line_text(start, new_line, start + spaces)
                else:
                    new_spaces = max(0, spaces - 2)
                    new_line = " " * new_spaces + line[spaces:]
                    self._set_line_text(start, new_line, start + new_spaces)
                self.ensureCursorVisible()
                return
            else:
                # Regular backspace: one character per undo step
                cursor = self.textCursor()
                cursor.beginEditBlock()
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                else:
                    cursor.deletePreviousChar()
                cursor.endEditBlock()
                self.setTextCursor(cursor)
                self.ensureCursorVisible()
                return

        elif e.key() == Qt.Key_Delete:
            # Delete key: one character per undo step
            cursor = self.textCursor()
            cursor.beginEditBlock()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            else:
                cursor.deleteChar()
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            return

        # Regular printable character: one character per undo step.
        # Using beginEditBlock/endEditBlock prevents Qt from merging consecutive insertions.
        if e.text() and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            cursor = self.textCursor()
            fmt = self.currentCharFormat()
            cursor.beginEditBlock()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            cursor.insertText(e.text(), fmt)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            return

        super().keyPressEvent(e)
        self.ensureCursorVisible()

    # -------- Keys inside Markdown structure --------
    def _structure_key(self, e) -> bool:
        """Enter, Backspace and Tab where the paragraph has a structure of
        its own (a heading, a list item). True when the key was handled."""
        cursor = self.textCursor()
        block = cursor.block()
        plain = e.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)
        key = e.key()
        if key in (Qt.Key_Tab, Qt.Key_Backtab) and e.modifiers() in (
                Qt.NoModifier, Qt.ShiftModifier):
            delta = -1 if key == Qt.Key_Backtab or e.modifiers() == Qt.ShiftModifier else 1
            if rich_text.change_indent(cursor, delta):
                self.setTextCursor(cursor)
                return True
            # Tab at the start of a plain paragraph starts a list (the
            # habit typed bullets taught), where lists can be kept.
            if (delta > 0 and not cursor.hasSelection() and cursor.atBlockStart()
                    and self._holds_structure()
                    and not self._indent_level_and_has_bullet(block.text())[0]
                    and not self._indent_level_and_has_bullet(block.text())[1]):
                self.toggle_list(rich_text.BULLET)
                return True
            return False
        if cursor.hasSelection():
            return False
        if rich_text.is_divider(block):
            return self._divider_key(e, cursor, block)
        previous = block.previous()
        if (key == Qt.Key_Backspace and plain and cursor.atBlockStart()
                and previous.isValid() and rich_text.is_divider(previous)):
            # Backspace just below a divider takes the divider away.
            rich_text.remove_divider(previous)
            return True
        if (rich_text.quote_depth(block) and block.textList() is None and plain and (
                (key in (Qt.Key_Return, Qt.Key_Enter) and not block.text())
                or (key == Qt.Key_Backspace and cursor.atBlockStart()))):
            # Return on an empty quoted line, or Backspace at the start of
            # a quoted paragraph: one level of quote less.
            cursor.beginEditBlock()
            rich_text.set_quote_depth(block, rich_text.quote_depth(block) - 1)
            cursor.endEditBlock()
            self.viewport().update()
            return True
        if block.textList() is not None and plain and (
                (key in (Qt.Key_Return, Qt.Key_Enter) and not block.text())
                or (key == Qt.Key_Backspace and cursor.atBlockStart())):
            # Return on an empty item, or Backspace at an item's start: one
            # level up, and out of the list from the top level.
            rich_text.change_indent(cursor, -1)
            self.setTextCursor(cursor)
            return True
        heading = block.blockFormat().headingLevel()
        if heading and e.key() in (Qt.Key_Return, Qt.Key_Enter) and plain:
            # Return at the end of a heading starts a Body paragraph, the
            # way Pages and every Markdown editor continue after a title;
            # anywhere else in it, both halves stay headings.
            cursor.beginEditBlock()
            fmt = block.blockFormat()
            if cursor.atBlockEnd():
                fmt = rich_text.heading_block_format(fmt, 0)
                char = rich_text.body_char_format(cursor.charFormat(), own_weight=False)
            else:
                char = cursor.charFormat()
            cursor.insertBlock(fmt, char)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            self.setCurrentCharFormat(char)
            self._update_active_format()
            return True
        if heading and e.key() == Qt.Key_Backspace and plain and cursor.atBlockStart():
            # Backspace at the start of a heading makes it Body first.
            self.set_heading(0)
            return True
        return False

    def _divider_key(self, e, cursor, block) -> bool:
        """A divider holds no text: typing on it goes into the paragraph
        below (made when there is none), Backspace and Delete take it
        away."""
        key = e.key()
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            rich_text.remove_divider(block)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter) or (
                e.text() and e.text().isprintable()
                and not e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            below = block.next()
            if not below.isValid() or rich_text.is_divider(below) or \
                    key in (Qt.Key_Return, Qt.Key_Enter):
                cursor.beginEditBlock()
                cursor.movePosition(QTextCursor.EndOfBlock)
                cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
                cursor.endEditBlock()
            else:
                cursor.setPosition(below.position())
            self.setTextCursor(cursor)
            # Return is done; a character is typed where the caret is now.
            return key in (Qt.Key_Return, Qt.Key_Enter)
        return False

    def _apply_active_format_to_cursor(self):
        """Apply the active formatting state to the current cursor position."""
        fmt = QTextCharFormat()
        if self.active_format['bold']:
            fmt.setFontWeight(700)
        if self.active_format['italic']:
            fmt.setFontItalic(True)
        if self.active_format['underline']:
            fmt.setFontUnderline(True)
        if self.active_format['color']:
            fmt.setForeground(self.active_format['color'])
        self.mergeCurrentCharFormat(fmt)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if (event.mimeData().hasUrls()
                and self.isSignalConnected(QMetaMethod.fromSignal(self.urls_dropped))):
            self.urls_dropped.emit(list(event.mimeData().urls()))
            event.acceptProposedAction()
        else:
            # Text goes where it was dropped, the way a paste would put it
            # there (insertFromMimeData), as one step on the undo stack.
            super().dropEvent(event)

    # -------- Paste --------
    def paste_from_clipboard(self):
        """Paste (Edit > Paste, the context menu): the same as the
        keyboard's paste, which Qt sends to insertFromMimeData."""
        self.insertFromMimeData(QApplication.clipboard().mimeData())

    def paste_normalized(self):
        """Paste and Match Style: the clipboard's text, in the style of the
        text where it goes."""
        self._insert_plain(paste.plain_text(QApplication.clipboard().mimeData()))

    def canInsertFromMimeData(self, source):
        return source.hasFormat(paste.MARKDOWN_MIME) or super().canInsertFromMimeData(source)

    def createMimeDataFromSelection(self):
        """Copy: the text and HTML other apps read, and, where the document
        holds Markdown structure, the selection as this editor writes
        Markdown, so a paste here keeps every structure exactly. (Qt's own
        copy would offer Qt's Markdown, which reads a heading's weight as
        bold, and builds an OpenDocument copy of every selection.)"""
        if not self._holds_structure():
            return super().createMimeDataFromSelection()
        cursor = self.textCursor()
        fragment = cursor.selection()
        mime = QMimeData()
        mime.setText(fragment.toPlainText())
        mime.setHtml(fragment.toHtml())
        mime.setData(paste.MARKDOWN_MIME, paste.selection_markdown(cursor).encode("utf-8"))
        return mime

    def insertFromMimeData(self, source):
        """Every paste and every drop of text arrives here.

        - A web address pasted over words makes them a link to it (over
          an address, it replaces it: the words would say one address
          and the link go to another).
        - Pictures (image files copied in a file manager, a picture copied
          on its own) go to whoever handles media, when someone does.
        - In a document that holds Markdown structure, outside a code
          block, the paste comes with what Markdown can say of it
          (paste.py); elsewhere, and for text that is not Markdown, it is
          plain text in the style of the text where it goes."""
        if source is None:
            return
        cursor = self.textCursor()
        text = source.text() if source.hasText() else ""
        address = text.strip()
        if (cursor.hasSelection() and link_url.is_bare_http_url(address)
                and self._holds_structure() and "\u2029" not in cursor.selectedText()
                and not link_url.is_an_address(cursor.selectedText())):
            rich_text.set_link(cursor, "", address)
            cursor.setPosition(cursor.selectionEnd())
            self.setTextCursor(cursor)
            self._leave_link_at_end()
            return
        if self._pasted_media(source, text):
            return
        if self._holds_structure() and not rich_text.is_code_block(cursor.block()):
            pasted = paste.from_mime(source)
            if pasted is not None:
                if pasted.left_out:
                    self.notice.emit(_n_pictures_left_out(pasted.left_out))
                if not pasted.document.isEmpty():
                    rich_text.insert_document(cursor, pasted.document)
                    self.setTextCursor(cursor)
                    self.ensureCursorVisible()
                    return
        if getattr(self, "_markdown_source", False) and source.hasFormat(paste.MARKDOWN_MIME):
            # Markdown opened as its text: what was copied, as Markdown.
            text = bytes(source.data(paste.MARKDOWN_MIME)).decode("utf-8", "replace")
        self._insert_plain(text or paste.plain_text(source))

    def _pasted_media(self, source, text: str) -> bool:
        """Report pasted pictures to whoever handles media. True when
        someone took them."""
        if source.hasUrls() and self.isSignalConnected(
                QMetaMethod.fromSignal(self.urls_dropped)):
            urls = list(source.urls())
            if urls and all(u.isLocalFile() and image_safety.is_image_file(u.toLocalFile())
                            for u in urls):
                # Image files copied in the Finder or Explorer.
                self.urls_dropped.emit(urls)
                return True
        if source.hasImage() and not text.strip() and self.isSignalConnected(
                QMetaMethod.fromSignal(self.image_pasted)):
            image = source.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if isinstance(image, QImage) and not image.isNull():
                self.image_pasted.emit(image)
                return True
        return False

    def _insert_plain(self, text: str) -> None:
        """Insert plain text in the style of the text where it goes (the
        way typing would: not into a link it only touches). A table cell
        holds one line, so line breaks become spaces there."""
        if not text:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self.setTextCursor(cursor)
        if cursor.currentTable() is not None and self._holds_structure():
            text = re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", text.strip())
        self._leave_link_at_end()
        cursor.insertText(text, self.currentCharFormat())
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
