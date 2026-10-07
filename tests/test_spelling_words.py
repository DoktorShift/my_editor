# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding the words of Markdown text that spell checking reads."""

import time

from spelling.words import (
    MAX_BLOCK_LENGTH, START, State, advance, checkable, clean, from_utf16, hyphen_parts, masked,
    passed_code, scan, to_utf16, utf16_offsets,
)


def words(text, state=START, skipped=()):
    found = scan(text, state, skipped)
    return [text[start:end] for start, end in found.words]


def blocks(*lines):
    """The words of each block of a document, scanned in order."""
    state = START
    out = []
    for line in lines:
        found = scan(line, state)
        out.append([line[start:end] for start, end in found.words])
        state = found.state
    return out


# -- what a word is -------------------------------------------------------------

def test_words_of_any_script_are_found_whole():
    assert words("Grüße aus der Straße, naïve café") == ["Grüße", "aus", "der", "Straße",
                                                          "naïve", "café"]
    # Typed decomposed (u + combining diaeresis): still one word.
    assert words("Gru\u0308ße") == ["Gru\u0308ße"]
    assert clean("Gru\u0308ße") == "Grüße"
    assert words("नमस्ते दुनिया") == ["नमस्ते", "दुनिया"]


def test_apostrophes_and_hyphens_join_words():
    assert words("don't it’s geht's rock'n'roll") == ["don't", "it’s", "geht's", "rock'n'roll"]
    assert words("'quoted' and Jens' Buch") == ["quoted", "and", "Jens", "Buch"]
    assert words("E-Mail-Adresse well-known") == ["E-Mail-Adresse", "well-known"]
    assert words("Spell\u00adcheck") == ["Spell\u00adcheck"]
    assert clean("Spell\u00adcheck") == "Spellcheck"
    assert words("col·lecció") == ["col·lecció"]


def test_dashes_and_punctuation_separate_words():
    assert words("one\u2013two\u2014three word--word (paren) [bracket] «quote» „Zitat“") == [
        "one", "two", "three", "word", "word", "paren", "bracket", "quote", "Zitat"]
    assert words("Ende. Anfang... weiter!") == ["Ende", "Anfang", "weiter"]


def test_what_is_not_a_word_is_left_alone():
    assert words("I a x NASA BTC E-MAIL ÜBER") == []
    assert words("2026 3.5 mp3 2nd H2O 10% #42") == []
    assert words("snake_case __init__ CONSTANT_NAME") == ["init"]
    assert words("example.com main.py v3.3 z.B. e.g. u.a.") == []
    assert words("x" * 101) == []


def test_markdown_emphasis_keeps_its_words():
    assert words("*one* **two** _three_ __four__ ~~five~~ ==six==") == [
        "one", "two", "three", "four", "five", "six"]


def test_the_underscores_of_emphasis_are_blanked_for_a_system_checker():
    text = "ein _Wrot_ und __Fehlr__ in snake_case"
    out = masked(text, scan(text).skipped)
    assert out == "ein  Wrot  und   Fehlr   in snake_case"


def test_hyphenated_words_split_into_the_parts_that_are_words():
    text = "E-Mail-Adresse"
    assert [text[s:e] for s, e in hyphen_parts(text, 0, len(text))] == ["Mail", "Adresse"]
    text = "x-ray well-known"
    assert [text[s:e] for s, e in hyphen_parts(text, 6, len(text))] == ["well", "known"]


def test_checkable_applies_the_same_rules_to_any_piece():
    assert checkable("Wort") and checkable("don't")
    assert not checkable("A") and not checkable("ABC") and not checkable("a1")
    assert not checkable("'s") and not checkable("a_b") and not checkable("a.b")


# -- what is not prose ------------------------------------------------------------

def test_addresses_are_skipped():
    text = ("See https://example.com/path?q=wrod, www.exmple.org and "
            "ftp://files.exmple.net/a; mail alice@exmple.com or _@exmple.com.")
    assert words(text) == ["See", "and", "mail", "or"]
    assert words("write a.b+c@d.de (or x-y@exmple.org)") == ["write", "or"]


def test_addresses_without_a_scheme_skip_their_path_too():
    assert words("Code auf github.com/rinbal/my_editor und example.com/pfad hier") == [
        "Code", "auf", "und", "hier"]
    # Not addresses: no dot before the slash, or no name after the dot.
    assert words("docs/usage.md and/or km/h") == ["docs", "and", "or", "km"]


def test_a_long_pasted_token_takes_no_longer_than_its_length():
    # A Cashu token, a hex dump, a long invoice: one unbroken run just under
    # the paragraph limit. A pattern tried from every position of such a run
    # took time in the square of its length (a second and more per
    # keystroke); now it takes a few milliseconds.
    size = MAX_BLOCK_LENGTH - 10
    for run in ("cashuAeyJ0b2tlbiI6W3s-_", "0123456789abcdef", "a", "ab.", "ab-", "ab+", "a.b/"):
        text = (run * (size // len(run) + 1))[:size]
        started = time.perf_counter()
        scan(text)
        assert time.perf_counter() - started < 0.25, run


def test_a_paragraph_too_long_for_prose_is_not_read():
    text = "wrod " * (MAX_BLOCK_LENGTH // 5 + 1)
    started = time.perf_counter()
    found = scan(text)
    assert time.perf_counter() - started < 0.25
    assert found.words == () and found.skipped == ((0, len(text)),)
    assert found.state == advance(text)


def test_nostr_references_mentions_and_hashtags_are_skipped():
    text = ("Hi @alice and @npub1abcdefgh, read nostr:nevent1qqsabcdefgh and "
            "npub1qqqqqqqqqqqqqqq or naddr1xyzqwerty #bitcoin #Nostr-Treffen today")
    assert words(text) == ["Hi", "and", "read", "and", "or", "today"]
    assert words("C# and email@domain") == ["and", "email", "domain"]
    assert words("&#123; &amp; &nbsp;word") == ["word"]


def test_inline_code_is_skipped():
    assert words("Run `pip instal foo` now") == ["Run", "now"]
    assert words("A ``code with ` tick`` here") == ["here"]
    assert words("An `unclosed tick stays text") == ["An", "unclosed", "tick", "stays", "text"]


def test_links_keep_their_text_and_skip_their_address():
    assert words("[the lnk text](https://x.org/wrng \"A titel\") and ![alt txt](img/pic.png)") == [
        "the", "lnk", "text", "and", "alt", "txt"]
    assert words("[read more][refrence] and a note[^fotnote]") == ["read", "more", "and", "note"]
    assert words("<https://example.com/wrng> and <alice@exmple.com>") == ["and"]


def test_html_markup_is_skipped_but_its_text_is_read():
    assert words('<span class="hilight">Wrod</span> <br/> <!-- a cmment --> end') == [
        "Wrod", "end"]


def test_paths_and_shortcodes_are_skipped():
    assert words("Open /usr/locl/bin or ~/Dokumente/notz.md or C:\\Usres\\me and/or :zap:") == [
        "Open", "or", "or", "and", "or"]


# -- across lines and blocks --------------------------------------------------------

def test_a_code_fence_skips_every_line_until_it_closes():
    assert blocks("Before", "```python", "def fnction(): pass", "```", "After") == [
        ["Before"], [], [], [], ["After"]]
    assert blocks("~~~~", "~~~", "still cde", "~~~~", "out") == [[], [], [], [], ["out"]]
    # A backtick fence cannot hold a backtick in its info string: that is inline code.
    assert blocks("```not a fence``` here") == [["here"]]


def test_an_unclosed_fence_runs_to_the_end():
    assert blocks("```", "cde", "mor cde") == [[], [], []]


def test_indented_code_needs_a_blank_line_before_it():
    assert blocks("Para", "    not cde, a continuation") == [["Para"], ["not", "cde",
                                                                         "continuation"]]
    assert blocks("Para", "", "    cde lne", "    mor cde", "", "Back") == [
        ["Para"], [], [], [], [], ["Back"]]
    # The editor's own bullets and Markdown list items are not code.
    assert blocks("", "    \u2022 bullt item", "    - nestd item") == [
        [], ["bullt", "item"], ["nestd", "item"]]


def test_front_matter_is_skipped_only_at_the_top():
    assert blocks("---", "title: My Titel", "tags:", "  - wrd", "---", "Text") == [
        [], [], [], [], [], ["Text"]]
    assert blocks("Text", "---", "title: still read") == [["Text"], [], ["title", "still",
                                                                         "read"]]
    # A document that begins with a rule and then prose is read as prose.
    assert blocks("---", "This is a sentnce.", "More") == [[], ["This", "is", "sentnce"],
                                                           ["More"]]


def test_html_comments_can_span_lines():
    assert blocks("Text <!-- strt", "insde", "end --> aftr", "next") == [
        ["Text"], [], ["aftr"], ["next"]]


def test_reference_definitions_are_skipped():
    assert blocks('[label]: https://example.com "Titel"', "Text") == [[], ["Text"]]
    assert blocks("[label]: <https://example.com/a b>", "[other]: /pfad (Titl)") == [[], []]


def test_footnote_definitions_and_lines_that_only_look_like_references_are_prose():
    assert words("[^1]: Eine Fusnote mit einem Fehlr darin.") == [
        "Eine", "Fusnote", "mit", "einem", "Fehlr", "darin"]
    assert words("[Hinweis]: Das ist wichtig mit Fehlr.") == [
        "Hinweis", "Das", "ist", "wichtig", "mit", "Fehlr"]


def test_the_indented_paragraphs_of_a_list_item_are_prose():
    assert blocks("- Erster Punkt", "", "    Ein zweiter Absatz im Punkt mit Fehlr.")[2] == [
        "Ein", "zweiter", "Absatz", "im", "Punkt", "mit", "Fehlr"]
    assert blocks("1. First item", "", "    A second paragraph wrod.")[2] == [
        "second", "paragraph", "wrod"]
    assert blocks("- one", "  - nestd", "", "      a paragraph of it")[3] == [
        "paragraph", "of", "it"]
    # Code inside an item is indented four more; after the list, four is code.
    assert blocks("- item", "", "        cde lne") == [["item"], [], []]
    assert blocks("- item", "", "Back at the margin", "", "    cde lne") == [
        ["item"], [], ["Back", "at", "the", "margin"], [], []]
    # A fence inside a list keeps the list going.
    assert blocks("- item", "", "  ```", "  cde", "  ```", "", "    more of the item")[6] == [
        "more", "of", "the", "item"]


def test_a_rule_then_prose_at_the_top_is_prose():
    assert blocks("---", "Hinweis: Das ist wichtig mit Fehlr.") == [
        [], ["Hinweis", "Das", "ist", "wichtig", "mit", "Fehlr"]]


def test_line_breaks_inside_a_block_are_lines():
    text = "First\u2028```\u2028cde\u2028```\u2028Last"
    assert words(text) == ["First", "Last"]


def test_advance_agrees_with_scan():
    lines = ["---", "a: b", "---", "```", "x", "```", "", "    code", "<!-- c", "d -->", "t"]
    state = START
    for line in lines:
        assert advance(line, state) == scan(line, state).state
        state = scan(line, state).state


def test_a_formatted_code_block_opens_nothing():
    state = State(blank=True, first=False)
    after = passed_code(state)
    assert after.fence == "" and not after.blank and not after.first


def test_spans_the_caller_skips_are_honored():
    text = "Use the wrng function"
    assert words(text, skipped=[(8, 12)]) == ["Use", "the", "function"]


# -- for system checkers ------------------------------------------------------------

def test_masking_keeps_the_length_and_blanks_what_is_skipped():
    text = "See `cde` at https://x.org\u2028next\ufffc"
    found = scan(text)
    out = masked(text, found.skipped)
    assert len(out) == len(text)
    code, address = "`cde`", "https://x.org"
    assert out == "See " + " " * len(code) + " at " + " " * len(address) + " next "


def test_utf16_offsets_count_emoji_twice():
    assert utf16_offsets("plain ascii") is None
    assert utf16_offsets("Grüße") is None
    text = "😀 helo 👍 wrld"
    offsets = utf16_offsets(text)

    def units(prefix):
        return len(prefix.encode("utf-16-le")) // 2

    for word in ("helo", "wrld"):
        index = text.index(word)
        assert to_utf16(offsets, index) == units(text[:index])
        assert from_utf16(offsets, units(text[:index])) == index
    assert to_utf16(offsets, len(text)) == units(text)
    assert from_utf16(None, 7) == 7
