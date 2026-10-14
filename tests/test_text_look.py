"""Code blocks and quotes look alike wherever a text is shown: the editor
and the preview paint one box behind a code block and a bar beside each
level of a quote (text_look), and set code in the system's monospace font."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QColor, QTextCursor, QTextFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import fonts  # noqa: E402
import rich_text  # noqa: E402
import text_look  # noqa: E402
from nostr.preview import NostrPreview, body_document, palette  # noqa: E402


# Every window a test makes, closed and freed when it ends: a shown window
# left behind would be painted, and freed, during a later test.
_made: list = []


@pytest.fixture(autouse=True)
def _close_windows():
    yield
    while _made:
        widget = _made.pop()
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _app():
    return QApplication.instance() or QApplication([])


def _preview(markdown: str, dark: bool = False) -> NostrPreview:
    _app()
    view = NostrPreview(dark=dark)
    _made.append(view)
    view.resize(700, 500)
    view.show_markdown(markdown)
    view.show()
    QApplication.processEvents()
    return view


def _blocks(doc):
    block = doc.begin()
    while block.isValid():
        yield block
        block = block.next()


def _near(color: QColor, expected: QColor, slack: int = 6) -> bool:
    return all(abs(a - b) <= slack for a, b in zip(color.getRgb()[:3], expected.getRgb()[:3]))


def _block_rect(view, block):
    layout = view.document().documentLayout()
    rect = layout.blockBoundingRect(block)
    rect.translate(-view.horizontalScrollBar().value(), -view.verticalScrollBar().value())
    return rect


def test_the_preview_sets_code_in_the_systems_monospace_font():
    doc = body_document("Some `inline code` here.\n\n```\nfirst line\nsecond line\n```\n")
    families = []
    for block in _blocks(doc):
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and ("code" in fragment.text() or "line" in fragment.text()):
                if rich_text.is_code_block(block) or fragment.charFormat().fontFixedPitch():
                    families.append(fragment.charFormat().fontFamilies()[0])
            it += 1
    assert families and all(family == fonts.monospace_family() for family in families)


def test_a_code_block_in_the_preview_is_one_box_without_gaps():
    view = _preview("Before.\n\n```\nfirst line\nsecond line\nthird line\n```\n\nAfter.\n")
    code = [block for block in _blocks(view.document()) if rich_text.is_code_block(block)]
    assert len(code) == 3
    top = _block_rect(view, code[0]).top()
    bottom = _block_rect(view, code[-1]).bottom()
    left = _block_rect(view, code[0]).left() + code[0].blockFormat().leftMargin()
    image = view.viewport().grab().toImage()
    box = QColor(palette(False).code_background)
    # A column just inside the box, left of the text: box color all the way down.
    x = int(left - text_look.CODE_PAD_X / 2)
    column = [image.pixelColor(x, y) for y in range(int(top) + 1, int(bottom) - 1)]
    assert column and all(_near(color, box) for color in column)
    for block in code:
        assert not block.blockFormat().hasProperty(QTextFormat.Property.BackgroundBrush)


def test_a_quote_in_the_preview_has_a_bar_beside_it():
    view = _preview("Before.\n\n> A quoted thought\n> over two lines.\n\nAfter.\n")
    quote = next(block for block in _blocks(view.document()) if rich_text.quote_depth(block))
    rect = _block_rect(view, quote)
    fmt = quote.blockFormat()
    x = int(rect.left() + text_look.quote_bar_x(fmt.leftMargin(), 1, 0)) + 1
    image = view.viewport().grab().toImage()
    # The whole height of both lines, not a stub.
    column = [image.pixelColor(x, y) for y in range(int(rect.top()) + 1, int(rect.bottom()) - 1)]
    assert column and all(_near(color, text_look.quote_bar_color(False)) for color in column)


def test_list_items_in_the_preview_sit_closer_than_paragraphs():
    doc = body_document("- one\n- two\n- three\n\nA paragraph.\n\nAnother.\n")
    items = [block for block in _blocks(doc) if block.textList() is not None]
    assert len(items) == 3
    for item in items[1:]:
        fmt = item.blockFormat()
        assert fmt.topMargin() == 0 and fmt.bottomMargin() < 12


def test_the_editor_pads_its_code_box_as_the_preview_does(tmp_path):
    _app()
    from editor import HtmlEditor
    ed = HtmlEditor()
    _made.append(ed)
    ed.resize(700, 400)
    ed.set_writing_font(True)
    ed.document().setMarkdown("Before.\n\n```\nfirst line\nsecond line\n```\n\nAfter.\n")
    ed.show()
    QApplication.processEvents()
    code = [block for block in _blocks(ed.document()) if rich_text.is_code_block(block)]
    assert code
    rect = _block_rect(ed, code[0])
    image = ed.viewport().grab().toImage()
    x = int(rect.left() + code[0].blockFormat().leftMargin() - text_look.CODE_PAD_X / 2)
    y = int(rect.center().y())
    assert _near(image.pixelColor(x, y), text_look.code_box_color(ed._is_dark()))


def test_a_table_after_a_code_block_reads_like_any_other_in_the_editor():
    """Qt's Markdown reader puts an empty line into the first header cell
    of a table right after a code block, and the header's weight on it:
    the cell's text sat a line low and plain. Both views read the file
    through read_markdown, which tidies it; the file writes back the same."""
    _app()
    from PySide6.QtGui import QFont, QTextDocument
    import markdown_writer
    source = "Text.\n\n```\nx = 1\n```\n\n| Name | Value |\n| --- | --- |\n| a | 1 |\n"
    doc = QTextDocument()
    markdown_writer.read_markdown(doc, source)
    table = next(iter(rich_text.tables_of(doc)))
    for column in range(table.columns()):
        cell = table.cellAt(0, column)
        first = doc.findBlock(cell.firstPosition())
        assert first.text() in ("Name", "Value")
        cursor = QTextCursor(doc)
        cursor.setPosition(cell.firstPosition() + 1)
        assert cursor.charFormat().fontWeight() >= QFont.Weight.DemiBold
    assert markdown_writer.document_to_markdown(doc) == source


def test_the_editors_text_margin_is_no_edit():
    """The margin around the text is set when the editor is made: nothing
    to undo, and a new document has no changes to save."""
    _app()
    from editor import HtmlEditor
    ed = HtmlEditor()
    _made.append(ed)
    assert ed.document().documentMargin() == text_look.CODE_PAD_X + 4
    assert ed.document().availableUndoSteps() == 0
    assert not ed.document().isModified()
