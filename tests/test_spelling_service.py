# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spell checking a document as it changes, without blocking typing."""

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTextEdit

from spelling.backends import AUTOMATIC
from spelling.service import DocumentSpelling, Misspelling, SpellChecker
from tests.spelling_fakes import Editor, FakeBackend, FakeTextBackend, drain


class Clock:
    """Time that moves only when asked: ``step`` seconds per reading."""

    def __init__(self, step=0.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


def document(*lines):
    doc = QTextDocument()
    doc.setPlainText("\n".join(lines))
    return doc


def follow(doc, backend=None, **options):
    backend = backend or FakeBackend()
    checker = SpellChecker(backend)
    spelling = DocumentSpelling(checker, doc, **options)
    changes = []
    spelling.misspellingsChanged.connect(lambda first, last: changes.append((first, last)))
    return spelling, checker, backend, changes


def wrong(spelling, doc):
    """Every block's misspelled words, as text."""
    out = []
    block = doc.begin()
    while block.isValid():
        text = block.text()
        out.append([text[m.start:m.end] for m in spelling.misspellings(block)])
        block = block.next()
    return out


def type_at(doc, position, text):
    cursor = QTextCursor(doc)
    cursor.setPosition(position)
    cursor.insertText(text)


# -- the checker --------------------------------------------------------------

def test_plain_text_is_checked_line_by_line_in_utf16_units():
    checker = SpellChecker(FakeBackend())
    text = "😀 helo world\n```\nnot chcked\n```\nthe wrld"
    found = checker.find_misspellings(text)
    assert [(m.word, m.language) for m in found] == [("helo", "en-US"), ("wrld", "en-US")]
    first = found[0]
    assert (first.start, first.length) == (3, 4)          # the emoji counts twice
    utf16 = text.encode("utf-16-le")
    last = found[1]
    assert utf16[2 * last.start:2 * last.end].decode("utf-16-le") == "wrld"


def test_word_by_word_knows_abbreviations_compounds_and_apostrophes():
    checker = SpellChecker(FakeBackend())
    assert checker.find_misspellings("das ist bzw. ein Haus", "de-DE") == []
    found = checker.find_misspellings("E-Mail-Adresse und Mail-Adrese", "de-DE")
    assert [m.word for m in found] == ["Adrese"]
    assert checker.find_misspellings("don’t it's", "en-US") == []


def test_each_word_is_asked_once_until_it_is_learned():
    backend = FakeBackend()
    checker = SpellChecker(backend)
    accepted = []
    checker.wordAccepted.connect(accepted.append)
    for _ in range(3):
        assert checker.check("Nostr", "en-US") is False
    assert backend.checked_words().count("Nostr") == 1
    assert checker.learn("Nostr", "en-US") is True
    assert accepted == ["Nostr"]
    assert checker.check("Nostr", "en-US") is True
    assert backend.checked_words().count("Nostr") == 2


def test_suggestions_keep_a_typographic_apostrophe():
    backend = FakeBackend({"en-US": ["don't", "dent"]})
    checker = SpellChecker(backend)
    assert checker.suggestions("don\u2019", "en-US") == ["dent", "don\u2019t"]
    assert ("suggestions", "don'", "en-US") in backend.calls


def test_a_paragraph_checker_reads_only_prose_and_only_words_count():
    backend = FakeTextBackend()
    checker = SpellChecker(backend)
    found = checker.find_misspellings("the wrod at https://exmple.com and NASA `cde`", "en-US")
    assert [m.word for m in found] == ["wrod"]
    sent = backend.checked_texts()[0]
    assert "exmple" not in sent and "cde" not in sent and len(sent) == 45


def test_a_paragraph_checker_finds_mistakes_inside_underscore_emphasis():
    # Like macOS, the fake reads "_" as part of a word: shown "_Wrot_", it
    # flags "_Wrot_", which is not inside the word "Wrot", and the mistake
    # was dropped. The underscores are markup and are blanked now.
    checker = SpellChecker(FakeTextBackend())
    found = checker.find_misspellings("das ist ein _Wrot_ und __Fehlr__ hier", "de-DE")
    assert [m.word for m in found] == ["Wrot", "Fehlr"]


def test_a_paragraph_checker_tells_the_language():
    checker = SpellChecker(FakeTextBackend(default=AUTOMATIC))
    assert checker.default_language() == AUTOMATIC
    found = checker.find_misspellings("das ist ein Huas\nthis is a wrod")
    assert [(m.word, m.language) for m in found] == [("Huas", "de-DE"), ("wrod", "en-US")]


def test_an_unavailable_checker_finds_nothing_and_says_so_once():
    backend = FakeBackend()
    checker = SpellChecker(backend)
    seen = []
    checker.availabilityChanged.connect(seen.append)
    assert checker.is_available() is True
    backend.fail = "check"
    assert checker.find_misspellings("helo wrld") == []
    assert checker.is_available() is False
    assert checker.find_misspellings("helo wrld") == []
    assert seen == [False]


# -- a document -----------------------------------------------------------------

def test_blocks_are_checked_from_the_event_loop_not_at_once():
    doc = document("the helo", "a wrld")
    spelling, _checker, backend, changes = follow(doc)
    assert backend.calls == []                       # nothing checked while setting up
    assert wrong(spelling, doc) == [[], []]
    drain(spelling)
    assert wrong(spelling, doc) == [["helo"], ["wrld"]]
    assert changes and changes[0][0] == 0 and changes[-1][1] == 1


def test_offsets_count_utf16_units_like_qt():
    doc = document("😀 helo")
    spelling, *_ = follow(doc)
    spelling.check_all()
    (misspelling,) = spelling.misspellings(doc.begin())
    assert (misspelling.start, misspelling.length, misspelling.word) == (3, 4, "helo")
    cursor = QTextCursor(doc)
    cursor.setPosition(doc.begin().position() + misspelling.start)
    cursor.setPosition(doc.begin().position() + misspelling.end, QTextCursor.MoveMode.KeepAnchor)
    assert cursor.selectedText() == "helo"


def test_visible_blocks_are_checked_first():
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
             "india", "juliett"]
    doc = document(*names)
    spelling, _checker, backend, _changes = follow(doc, clock=Clock(step=1.0))
    spelling.set_visible_blocks(6, 7)
    for _ in range(4):
        spelling._run()                              # one block per slice with this clock
    assert backend.checked_words() == ["golf", "hotel", "alpha", "bravo"]


def test_a_slice_stops_when_its_time_is_up():
    doc = document(*[f"word{i} wrod" for i in range(20)])
    spelling, _checker, backend, _changes = follow(doc, clock=Clock(step=0.003))
    spelling._run()
    checked_blocks = sum(1 for call in backend.calls if call[1] == "wrod")
    assert 1 <= checked_blocks <= 3                  # 8 ms at 3 ms a reading
    assert spelling.is_checking()
    drain(spelling)
    assert not spelling.is_checking()


def test_an_edit_checks_only_the_block_it_touched():
    doc = document("the helo", "a wrld", "the end")
    spelling, _checker, backend, changes = follow(doc)
    spelling.check_all()
    backend.calls.clear()
    changes.clear()
    type_at(doc, doc.findBlockByNumber(1).position() + len("a wrld"), " here")
    spelling.check_all()
    assert sorted(set(backend.checked_words())) == ["here"]   # "a" and "wrld" were known
    assert wrong(spelling, doc) == [["helo"], ["wrld"], []]
    # Named although its misspellings stayed the same: underlines kept as
    # text cursors grow with text typed right after them.
    assert changes == [(1, 1)]


def test_typing_before_a_misspelling_moves_it_and_says_so():
    doc = document("helo world")
    spelling, _checker, _backend, changes = follow(doc)
    spelling.check_all()
    changes.clear()
    type_at(doc, 0, "the ")
    spelling.check_all()
    assert changes == [(0, 0)]
    assert spelling.misspellings(doc.begin())[0].start == 4


def test_new_and_removed_lines_keep_the_others_checked():
    doc = document("the helo", "a wrld", "the end")
    spelling, _checker, backend, _changes = follow(doc)
    spelling.check_all()
    backend.calls.clear()
    type_at(doc, doc.findBlockByNumber(1).position(), "one lnie\n")
    spelling.check_all()
    assert wrong(spelling, doc) == [["helo"], ["lnie"], ["wrld"], []]
    assert "helo" not in backend.checked_words() and "end" not in backend.checked_words()
    cursor = QTextCursor(doc.findBlockByNumber(1))
    cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    spelling.check_all()
    assert wrong(spelling, doc) == [["helo"], ["wrld"], []]


def found_now(spelling, doc):
    """Every block's misspellings as the service has them."""
    out, block = [], doc.begin()
    while block.isValid():
        out.append([(m.start, m.length, m.word) for m in spelling.misspellings(block)])
        block = block.next()
    return out


def found_fresh(doc, checker):
    """What checking a copy of the document from scratch finds."""
    copy = doc.clone()
    spelling = DocumentSpelling(checker, copy)
    spelling.check_all()
    return found_now(spelling, copy)


def test_an_undo_qt_reports_short_still_gets_its_paragraphs_checked():
    # Recorded: formatted text pasted over selections, then Undo. Qt's
    # report of the undo leaves out the start of the next paragraph, which
    # the undo changed too ("a wrldtexthelo th" became "texthelo th").
    editor = QTextEdit()
    editor.setMarkdown("the end\n\nhelo there\n\n- one wrld\n- two\n")
    doc = editor.document()
    spelling, checker, *_ = follow(doc)
    spelling.check_all()

    def select(start, end):
        cursor = QTextCursor(doc)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def copy(start, end, to):
        fragment = select(start, end).selection()
        cursor = QTextCursor(doc)
        cursor.setPosition(to)
        cursor.insertFragment(fragment)

    def markdown(at, text):
        cursor = QTextCursor(doc)
        cursor.setPosition(at)
        cursor.insertMarkdown(text)

    markdown(8, "> quoet\n\ntext")
    copy(4, 35, 39)
    markdown(15, "- a wrld\n- b mroe")
    copy(44, 84, 74)
    select(29, 59).insertHtml("<pre>cde\nfnction</pre>")
    markdown(31, "- a wrld\n- b mroe")
    copy(102, 114, 35)
    select(38, 45).insertHtml("<ul><li>item wrld</li><li>mroe</li></ul>")
    select(46, 76).insertHtml("<p>the helo</p><p>a wrld</p>")
    spelling.check_all()
    doc.undo()
    spelling.check_all()
    assert found_now(spelling, doc) == found_fresh(doc, checker)
    assert any("texthelo" in [m[2] for m in block] for block in found_now(spelling, doc))


def test_a_block_changed_without_a_report_is_checked_again_when_read():
    doc = document("the end", "a wrld")
    spelling, *_ = follow(doc)
    spelling.check_all()
    doc.blockSignals(True)                         # the service hears nothing
    type_at(doc, doc.findBlockByNumber(1).position(), "mroe ")
    doc.blockSignals(False)
    block = doc.findBlockByNumber(1)
    assert spelling.misspellings(block) == ()       # stale: not shown
    assert spelling.is_checking()                   # and checked again
    spelling.check_all()
    assert wrong(spelling, doc) == [[], ["mroe", "wrld"]]
    # The context menu checks a stale block on the spot.
    doc.blockSignals(True)
    type_at(doc, block.position(), "zzq ")
    doc.blockSignals(False)
    assert spelling.misspelling_at(block.position() + 1).word == "zzq"


def test_opening_a_fence_turns_the_lines_below_into_code_and_closing_it_back():
    doc = document("the helo", "a wrld", "the cde", "end")
    spelling, *_ = follow(doc)
    spelling.check_all()
    assert wrong(spelling, doc) == [["helo"], ["wrld"], ["cde"], []]
    type_at(doc, doc.findBlockByNumber(1).position(), "```\n")
    spelling.check_all()
    assert wrong(spelling, doc) == [["helo"], [], [], [], []]
    type_at(doc, doc.findBlockByNumber(3).position(), "```\n")
    spelling.check_all()
    assert wrong(spelling, doc) == [["helo"], [], [], [], ["cde"], []]


def test_formatted_code_and_mentions_are_not_checked():
    doc = QTextDocument()
    doc.setMarkdown("A [lnk txt](https://x.org) and `inlne cde`\n\n"
                    "```\nfnction cde\n```\n\n    indnted cde\n\nthe end wrod\n")
    cursor = QTextCursor(doc.begin())
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    mention = QTextCharFormat()
    mention.setAnchor(True)
    mention.setAnchorHref("nostr:npub1qqqqqqqq")
    cursor.insertText(" @Jak Dorsy", mention)
    spelling, *_ = follow(doc)
    spelling.check_all()
    found = [word for block in wrong(spelling, doc) for word in block]
    assert found == ["lnk", "txt", "wrod"]


def test_the_word_being_typed_is_not_underlined_until_it_is_finished():
    editor = Editor()
    editor.type("the hel")
    assert editor.underlined() == [[]]
    editor.type("lo here ")
    assert editor.underlined() == [[]]


def test_a_word_finished_with_a_period_gets_its_underline():
    editor = Editor()
    editor.type("the helo")
    assert editor.underlined() == [[]]
    editor.type(".")
    assert editor.underlined() == [["helo"]]


def test_a_word_finished_with_return_gets_its_underline():
    editor = Editor()
    editor.type("the helo\nthe end")
    assert editor.underlined() == [["helo"], []]


def test_a_word_finished_with_return_in_a_paragraph_checker_gets_its_underline():
    editor = Editor(FakeTextBackend())
    editor.type("the end helo\nthe end")
    assert editor.underlined() == [["helo"], []]


def test_typing_right_after_an_underlined_word_redraws_its_underline():
    editor = Editor()
    editor.type("a wrld ")
    assert editor.underlined() == [["wrld"]]
    QTest.keyClick(editor.edit, Qt.Key.Key_Left)
    editor.type("!")                                 # the underline must not grow onto "!"
    assert editor.underlined() == [["wrld"]]
    QTest.keyClick(editor.edit, Qt.Key.Key_Left)
    editor.type("\n")                               # nor onto the line break
    assert editor.underlined() == [["wrld"], []]


def test_a_pause_in_typing_finishes_the_word():
    editor = Editor()
    editor.spelling.TYPING_PAUSE_SECONDS = 0.01
    editor.type("the helo")
    for _ in range(100):
        QTest.qWait(5)
        if editor.underlined() == [["helo"]]:
            break
    assert editor.underlined() == [["helo"]]


def test_moving_the_cursor_away_finishes_the_word_and_back_keeps_it():
    editor = Editor()
    editor.type("the helo")
    QTest.keyClick(editor.edit, Qt.Key.Key_Home)
    assert editor.underlined() == [["helo"]]
    QTest.keyClick(editor.edit, Qt.Key.Key_End)      # back to the end, not typing
    assert editor.underlined() == [["helo"]]


def test_formatting_a_word_is_not_typing_it():
    editor = Editor()
    editor.type("the helo.")
    cursor = editor.edit.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    editor.edit.setTextCursor(cursor)
    bold = QTextCharFormat()
    bold.setFontWeight(700)
    editor.edit.mergeCurrentCharFormat(bold)
    drain(editor.spelling)
    assert editor.underlined() == [["helo"]]


def test_the_context_menu_finds_the_word_being_typed():
    editor = Editor()
    editor.type("the helo")
    editor.spelling.check_all()
    assert editor.spelling.misspelling_at(6).word == "helo"


def test_the_misspelling_under_a_click_is_found_at_once():
    doc = document("the end", "a wrld here")
    spelling, _checker, _backend, changes = follow(doc)
    position = doc.findBlockByNumber(1).position() + 4
    found = spelling.misspelling_at(position)
    assert found == Misspelling(2, 4, "wrld", "en-US")
    assert changes == [(1, 1)]
    assert spelling.misspelling_at(doc.findBlockByNumber(1).position() + 8) is None


def test_learning_a_word_checks_again_only_where_it_was():
    doc = document("the Nostr", "a wrld", "Nostr and nostr")
    spelling, checker, backend, changes = follow(doc)
    spelling.check_all()
    backend.calls.clear()
    changes.clear()
    checker.learn("Nostr", "en-US")
    spelling.check_all()
    assert wrong(spelling, doc) == [[], ["wrld"], ["nostr"]]
    assert "wrld" not in backend.checked_words()          # the middle line was not asked
    assert changes == [(0, 2)]


def test_a_word_with_a_typographic_apostrophe_is_learned_as_typed():
    doc = document("the Ku\u2019damm", "Ku'damm")
    spelling, checker, backend, _changes = follow(doc)
    spelling.check_all()
    assert wrong(spelling, doc) == [["Ku\u2019damm"], ["Ku'damm"]]
    checker.learn("Ku\u2019damm", "en-US")
    assert backend.learned == ["Ku'damm"]        # one form for every dictionary
    spelling.check_all()
    assert wrong(spelling, doc) == [[], []]


def test_ignoring_a_word_reaches_every_document():
    first, second = document("helo here"), document("see helo")
    checker = SpellChecker(FakeBackend())
    spellings = [DocumentSpelling(checker, doc) for doc in (first, second)]
    for spelling in spellings:
        spelling.check_all()
    checker.ignore("helo", "en-US")
    for spelling, doc in zip(spellings, (first, second)):
        spelling.check_all()
        assert wrong(spelling, doc) == [[]]


def test_another_language_checks_everything_again():
    doc = document("das Haus", "the house")
    spelling, *_ = follow(doc)
    spelling.check_all()
    assert wrong(spelling, doc) == [["das", "Haus"], ["house"]]
    spelling.set_language("de-DE")
    assert spelling.language() == "de-DE"
    spelling.check_all()
    assert wrong(spelling, doc) == [[], ["the", "house"]]


def test_each_paragraph_is_checked_in_its_own_language():
    doc = document("das ist ein Huas", "this is a wrod")
    spelling, *_ = follow(doc, FakeTextBackend(default=AUTOMATIC))
    spelling.check_all()
    found = [m for block in (doc.begin(), doc.begin().next())
             for m in spelling.misspellings(block)]
    assert [(m.word, m.language) for m in found] == [("Huas", "de-DE"), ("wrod", "en-US")]


def test_a_checker_that_breaks_takes_its_underlines_with_it():
    doc = document("the helo", "a wrld")
    spelling, checker, backend, changes = follow(doc)
    spelling.check_all()
    changes.clear()
    backend.fail = "check"
    type_at(doc, 0, "xyzzy ")
    spelling.check_all()
    assert wrong(spelling, doc) == [[], []]
    assert changes[-1] == (0, 1)
    assert checker.is_available() is False
    type_at(doc, 0, "more ")
    drain(spelling)                                   # nothing runs any more
    assert wrong(spelling, doc) == [[], []]


def test_a_document_loaded_again_is_checked_again():
    doc = document("the helo")
    spelling, *_ = follow(doc)
    spelling.check_all()
    doc.setPlainText("a wrld\nand mroe")
    spelling.check_all()
    assert wrong(spelling, doc) == [["wrld"], ["mroe"]]
    doc.setMarkdown("# Titel\n\nthe ```wrod```\n")
    spelling.check_all()
    assert wrong(spelling, doc) == [["Titel"], []]


def test_closing_stops_following_the_document():
    doc = document("the helo")
    spelling, _checker, backend, _changes = follow(doc)
    spelling.close()
    spelling.close()
    type_at(doc, 0, "wrod ")
    drain(spelling)
    assert backend.calls == []
    assert spelling.misspellings(doc.begin()) == ()
    assert spelling.misspelling_at(0) is None


def test_a_document_that_goes_away_takes_its_spelling_along():
    doc = document("the helo")
    spelling, *_ = follow(doc)
    destroyed = []
    spelling.destroyed.connect(lambda: destroyed.append(True))
    doc.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed == [True]
