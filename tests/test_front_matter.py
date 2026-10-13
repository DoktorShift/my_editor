# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins a Markdown file's front matter (front_matter.py, the article
details it holds in nostr/article_details.py, and a .md file's way in and
out of the window).

What must hold (decision Q-F, and the checkpoint 4 review's H2, M4, L7):

  The front matter as written is the source of truth. An article's details
  are read from it (``title``, ``summary`` or ``description``, ``image`` or
  ``cover``, ``tags``, ``slug``, ``date``, ``coauthors``, ``zap_split``),
  shown in Article Details, never in the text, never published as text. A
  file saved with no detail changed keeps its front matter byte for byte:
  unknown keys, nested mappings (Hugo's ``cover:``), comments, quoting, key
  order, block values, the byte order mark and its line endings. A changed
  detail rewrites its own entry only, where it was; a date in the form and
  time zone it was written in. Tags keep their case.

  Values are read the way front matter in the wild writes them, block
  values (``>-``, ``|``) included; a value that can't be read fully (a
  mapping) is never shown as a detail and never replaced. Values written
  are quoted when YAML would read them as another type, list items when
  they hold "," or brackets.

  A ``---`` block without a key is no front matter: a document opening
  with a divider keeps its first section.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import front_matter  # noqa: E402
from nostr.article_details import (  # noqa: E402
    ArticleDetails, from_front_matter, front_matter_with, hashtags_from, hashtags_text,
    kept_in_file)
from tests.app_process import run_window_script  # noqa: E402

HUGO = ('---\ntitle: "My post"\ndate: 2024-03-01T00:30:00+01:00\ncover:\n'
        '  image: "images/hero.jpg"\n  alt: "A hero"\ntags: ["Go", "Hugo"]\n---\n\nBody text.\n')
JEKYLL = ("---\nlayout: post   # Jekyll's\ntitle: Hi\ndescription: >-\n  A long description\n"
          "  over two lines.\nimage:\n  path: /assets/a.png\n  alt: An image\n---\n\nBody text.\n")
CRLF_BOM = "﻿---\r\ntitle: Hello\r\nlayout: post\r\n---\r\n\r\nBody text.\r\n"
NO_GAP = "---\ntitle: Hello\n---\nBody text right under it.\n"
DIVIDER = "---\n\nThe first section of my essay.\n\n---\n\nThe second section.\n"


def moment(*parts, offset=0) -> int:
    from datetime import timedelta
    return int(datetime(*parts, tzinfo=timezone(timedelta(hours=offset))).timestamp())


def unchanged_save(text: str) -> str:
    front, body = front_matter.split(text)
    return front_matter.join(front_matter_with(from_front_matter(front), front), body)


@pytest.mark.parametrize("text", [HUGO, JEKYLL, CRLF_BOM, NO_GAP])
def test_a_file_saved_with_no_detail_changed_keeps_its_front_matter_byte_for_byte(text):
    assert unchanged_save(text) == text


def test_without_front_matter_the_text_is_all_body():
    assert front_matter.split("# Title\n\nText") == (None, "# Title\n\nText")
    assert front_matter.split("Text\n---\nmore\n---\n")[0] is None   # not at the top


def test_an_empty_block_is_front_matter_and_stays_as_it_was():
    empty = "---\n---\n\nBody text.\n"
    front, body = front_matter.split(empty)
    assert front is not None and body == "Body text.\n"
    assert unchanged_save(empty) == empty
    titled = from_front_matter(front).with_changes(title="T")
    assert front_matter.join(front_matter_with(titled, front), body) == (
        "---\ntitle: T\n---\n\nBody text.\n")


def test_a_block_without_a_key_is_text():
    assert front_matter.split(DIVIDER) == (None, DIVIDER)
    commented, _ = front_matter.split("---\n# a comment\ntitle: A\n---\nText")
    assert from_front_matter(commented).title == "A"


def test_values_are_read_as_written_in_the_wild():
    hugo = from_front_matter(front_matter.split(HUGO)[0])
    assert hugo.title == "My post" and hugo.hashtags == ("Go", "Hugo")     # case kept
    assert hugo.image == ""                                  # the cover: mapping is not read
    assert hugo.published_at == moment(2024, 3, 1, 0, 30, offset=1)
    jekyll = from_front_matter(front_matter.split(JEKYLL)[0])
    assert jekyll.summary == "A long description over two lines."
    assert jekyll.image == ""                                 # image: path/alt mapping
    entries, _ = front_matter.split("---\na: 'it''s'\nb: [x, \"y, z\"]\nc: |\n  one\n  two\n"
                                    "d: plain # comment\ne: \"My \\\"post\\\"\"   # on the site\n"
                                    "f: 'it''s' # too\ng: [Go, \"a # b\"]  # tags\n---\n")
    assert [entry.value for entry in entries.entries] == [
        "it's", ["x", "y, z"], "one\ntwo", "plain", 'My "post"', "it's", ["Go", "a # b"]]


def test_a_changed_detail_rewrites_its_entry_only():
    front, body = front_matter.split(HUGO)
    details = from_front_matter(front).with_changes(title="A new title")
    out = front_matter.join(front_matter_with(details, front), body)
    assert out == HUGO.replace('title: "My post"', 'title: "A new title"')


def test_a_changed_date_keeps_its_form_and_time_zone():
    front, body = front_matter.split(HUGO)
    later = from_front_matter(front).with_changes(
        published_at=moment(2024, 3, 2, 8, 0, offset=1))
    out = front_matter.join(front_matter_with(later, front), body)
    assert "date: 2024-03-02T08:00:00+01:00\n" in out
    for original, expected in (("2026-10-01", "2026-10-02"), ("1700000000", "1700086400"),
                               ("2026-10-01T08:00:00Z", "2026-10-02T08:00:00Z"),
                               ('"2026-10-01"', '"2026-10-02"'),
                               ("2015-11-10 15:41:45 +0100", "2015-11-11 15:41:45 +0100")):
        front, _ = front_matter.split(f"---\ndate: {original}\n---\n")
        details = from_front_matter(front)
        moved = details.with_changes(published_at=details.published_at + 86400)
        assert f"date: {expected}" in front_matter.join(front_matter_with(moved, front), "")


def test_a_date_written_new_is_a_date_to_yaml_not_a_text():
    front, body = front_matter.split("---\ntitle: T\n---\n\nB\n")
    details = from_front_matter(front).with_changes(published_at=1791876318)
    out = front_matter.join(front_matter_with(details, front), body)
    assert "date: 2026-10-13T07:25:18Z\n" in out
    yaml = pytest.importorskip("yaml")
    assert type(yaml.safe_load(out.split("---\n")[1])["date"]).__name__ == "datetime"


@pytest.mark.parametrize("text, change, kept", [
    (HUGO, {"image": "https://cdn.example/c.jpg"}, {"image"}),             # Hugo's cover:
    ("---\ntitle: T\nimage:\n  path: /a.png\n  alt: A\n---\n\nB\n",
     {"image": "https://cdn.example/c.jpg"}, {"image"}),                    # Jekyll Chirpy
    ("---\ntitle: &t Real\nog: *t\n---\n\nB\n", {"title": "New"}, {"title"}),
])
def test_a_detail_whose_entry_cant_be_read_is_left_as_written_never_doubled(text, change, kept):
    front, body = front_matter.split(text)
    assert kept_in_file(front) == kept
    details = from_front_matter(front).with_changes(**change)
    assert front_matter.join(front_matter_with(details, front), body) == text


def test_an_emptied_detail_is_left_out_and_none_written_without_details():
    front, body = front_matter.split(HUGO)
    out = front_matter.join(front_matter_with(
        from_front_matter(front).with_changes(title=""), front), body)
    assert "title:" not in out and "cover:" in out
    assert front_matter_with(ArticleDetails(), None) is None
    assert front_matter.join(None, "Body") == "Body"


@pytest.mark.parametrize("value", ["plain words", "true", "123", "0x1F", "1e3", "2026-10-06",
                                   "a: b", "#hash", " padded ", "Ünïcödé", "\"quoted\"", "",
                                   "- dash", "[bracket]", "trailing:", "a #b"])
def test_what_is_written_reads_back_as_the_same_text(value):
    entries, _ = front_matter.split(front_matter.join(
        front_matter.FrontMatter((front_matter.entry("k", value),)), "x"))
    assert entries.entries[0].value == value


def test_list_items_with_commas_or_brackets_are_quoted():
    items = ["c++", "a]b", "x,y", "plain"]
    entries, _ = front_matter.split(front_matter.join(
        front_matter.FrontMatter((front_matter.entry("tags", items),)), "x"))
    assert entries.entries[0].value == items


def test_the_details_are_read_from_other_apps_keys():
    front, _ = front_matter.split("---\ndescription: A short one\ncover: https://c/x.jpg\n"
                                  "hashtags: [a, B]\npublished_at: 1700000000\n---\n")
    details = from_front_matter(front)
    assert (details.summary, details.image, details.hashtags, details.published_at) == (
        "A short one", "https://c/x.jpg", ("a", "B"), 1700000000)


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
r = {}
for path in sys.argv[2:]:
    w.open_path(path)
    ed = w.current_editor()
    name = os.path.basename(path)
    r[name + " shown"] = ed.toPlainText()
    r[name + " published"] = w._publish_text(ed, "markdown")
    c = ed.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    c.insertText(" More.")
    w._save_to(path)
    r[name + " saved"] = open(path, "rb").read().decode("utf-8")
    ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_markdown_files_in_the_window(tmp_path):
    paths = []
    for name, text in (("hugo.md", HUGO), ("jekyll.md", JEKYLL), ("divider.md", DIVIDER),
                       ("toml.md", TOML), ("pandoc.md", PANDOC)):
        path = tmp_path / name
        path.write_bytes(text.encode("utf-8"))
        paths.append(str(path))
    r = run_window_script(SCRIPT, tmp_path, *paths)
    assert r["hugo.md shown"] == "Body text."
    assert "cover" not in r["hugo.md published"] and "---" not in r["hugo.md published"]
    assert r["hugo.md saved"] == HUGO.replace("Body text.\n", "Body text. More.\n")
    assert r["jekyll.md saved"] == JEKYLL.replace("Body text.\n", "Body text. More.\n")
    assert "The first section of my essay." in r["divider.md shown"]
    assert r["divider.md saved"] == DIVIDER.replace("second section.\n", "second section. More.\n")
    for name, text in (("toml.md", TOML), ("pandoc.md", PANDOC)):      # review N3
        assert r[name + " shown"] == "Body text."
        assert "+++" not in r[name + " published"] and "title" not in r[name + " published"]
        assert r[name + " saved"] == text.replace("Body text.\n", "Body text. More.\n")



def test_comment_and_blank_lines_after_a_changed_entry_stay():
    text = ("---\ntitle: Hello\n# the tags below are for the site\ntags: [a]\n"
            "summary: S\n\nlayout: post\n---\n\nB\n")
    front, body = front_matter.split(text)
    details = from_front_matter(front).with_changes(title="New", summary="Other")
    assert front_matter.join(front_matter_with(details, front), body) == text.replace(
        "title: Hello", "title: New").replace("summary: S", "summary: Other")
    crlf = "---\r\ntitle: T\r\n# a comment\r\nlayout: post\r\n---\r\n\r\nB\r\n"
    front, body = front_matter.split(crlf)
    details = from_front_matter(front).with_changes(title="U")
    assert front_matter.join(front_matter_with(details, front), body) == crlf.replace(
        "title: T", "title: U")


def test_a_block_value_keeps_its_own_lines():
    text = "---\nsummary: |+\n  Line one\n  Line two\n\nslug: keep-me\n---\n\nB\n"
    front, _ = front_matter.split(text)
    assert from_front_matter(front).summary == "Line one\nLine two"
    assert unchanged_save(text) == text


# -- TOML front matter (Hugo's own archetype writes it), and Pandoc's "..." ----

TOML = ('+++\ntitle = "My TOML post"\ndate = 2024-03-01T08:00:00+01:00\n'
        'tags = ["a", "b"]  # for the site\ndraft = false\n\n[params]\ntoc = true\n+++\n\n'
        'Body text.\n')
PANDOC = "---\ntitle: Pandoc\nauthor: [A, B]\n...\n\nBody text.\n"


def test_toml_front_matter_is_read_and_kept_as_written():
    front, body = front_matter.split(TOML)
    assert body == "Body text.\n" and front.syntax == "toml"
    details = from_front_matter(front)
    assert (details.title, details.hashtags) == ("My TOML post", ("a", "b"))
    assert details.published_at == moment(2024, 3, 1, 8, 0, offset=1)
    assert unchanged_save(TOML) == TOML


def test_a_changed_detail_is_written_into_the_toml_in_place_in_toml():
    front, body = front_matter.split(TOML)
    details = from_front_matter(front)
    changed = details.with_changes(title='A "new" title', hashtags=("a", "b", "Extra"),
                                   published_at=moment(2024, 3, 2, 8, 0, offset=1),
                                   summary="Short")
    out = front_matter.join(front_matter_with(changed, front), body)
    assert out == TOML.replace('title = "My TOML post"', 'title = "A \\"new\\" title"').replace(
        'date = 2024-03-01T08:00:00+01:00', "date = 2024-03-02T08:00:00+01:00").replace(
        'tags = ["a", "b"]  # for the site', 'tags = ["a", "b", "Extra"]  # for the site').replace(
        "draft = false\n", 'draft = false\nsummary = "Short"\n')
    import tomllib
    assert tomllib.loads(out.split("+++\n")[1])["params"] == {"toc": True}   # still a table
    assert "---" not in out


def test_pandoc_front_matter_closed_by_dots_is_front_matter():
    front, body = front_matter.split(PANDOC)
    assert body == "Body text.\n" and from_front_matter(front).title == "Pandoc"
    assert unchanged_save(PANDOC) == PANDOC


KEPT_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

path = sys.argv[2]
w = main_window.MainWindow()
w.show()
w.nostr_state._active = True               # Article Details is a Nostr feature
w._update_nostr_commands()
w.open_path(path)
ed = w.current_editor()
w.act_article_details.setChecked(True)
app.processEvents()
panel = w._article_details_panel
r = {"cover read-only": panel.cover.url_edit.isReadOnly(),
     "title read-only": panel.title_edit.isReadOnly(),
     "notes": [l.text() for l in panel.findChildren(type(panel.zaps_note))
               if l.isVisibleTo(panel) and l.text().startswith("Kept")]}
w._set_article_details(ed, w._article_details_of(ed).with_changes(
    image="https://cdn.example/c.jpg"))
w._save_to(path)
r["saved"] = open(path, encoding="utf-8").read()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_cover_the_file_keeps_is_read_only_in_article_details(tmp_path):
    chirpy = "---\ntitle: T\nimage:\n  path: /a.png\n  alt: A\n---\n\nBody text.\n"
    path = tmp_path / "chirpy.md"
    path.write_text(chirpy, encoding="utf-8")
    r = run_window_script(KEPT_SCRIPT, tmp_path, str(path))
    assert r["cover read-only"] is True and r["title read-only"] is False
    assert r["notes"] == ["Kept as written in the file."]
    assert r["saved"] == chirpy


# -- only what changed changes, in the file's own style ----------------------

def test_a_tag_holding_a_comma_stays_one_tag():
    front, body = front_matter.split('---\ntags: [a, "b, c", \'d\']\n---\n\nB\n')
    details = from_front_matter(front)
    assert details.hashtags == ("a", "b, c", "d")
    assert hashtags_from(hashtags_text(details.hashtags)) == details.hashtags
    added = details.with_changes(hashtags=details.hashtags + ("Extra",))
    out = front_matter.join(front_matter_with(added, front), body)
    assert "tags: [a, \"b, c\", 'd', Extra]\n" in out


def test_a_block_list_stays_a_block_list():
    text = '---\naliases:\n  - My Note\ntags:\n  - project/alpha\n  - "#idea"\npublish: true\n---\n\nB\n'
    front, body = front_matter.split(text)
    details = from_front_matter(front)
    added = details.with_changes(hashtags=details.hashtags + ("Extra",))
    assert front_matter.join(front_matter_with(added, front), body) == text.replace(
        '  - "#idea"\n', '  - "#idea"\n  - Extra\n')
    fewer = details.with_changes(hashtags=("idea",))
    assert front_matter.join(front_matter_with(fewer, front), body) == text.replace(
        "  - project/alpha\n", "")


def test_a_rewritten_line_keeps_its_alignment_quotes_and_comment():
    text = ('---\nlayout: post\ntitle:  "Welcome to Jekyll!"   # on the site\n'
            "date:   2015-11-10 15:41:45 +0100\nsummary: 'It''s here'\n---\nB\n")
    front, body = front_matter.split(text)
    details = from_front_matter(front).with_changes(
        title="A new title", summary="Now it's new",
        published_at=from_front_matter(front).published_at + 86400)
    assert front_matter.join(front_matter_with(details, front), body) == (
        '---\nlayout: post\ntitle:  "A new title"   # on the site\n'
        "date:   2015-11-11 15:41:45 +0100\nsummary: 'Now it''s new'\n---\nB\n")


def test_a_block_text_stays_a_block_text():
    text = "---\ndescription: >-\n  A long description\n  over two lines.\nlayout: post\n---\nB\n"
    front, body = front_matter.split(text)
    details = from_front_matter(front).with_changes(summary="A shorter one.")
    assert front_matter.join(front_matter_with(details, front), body) == (
        "---\ndescription: >-\n  A shorter one.\nlayout: post\n---\nB\n")


def test_toml_keeps_its_own_style_too():
    text = "+++\ntitle   = 'Literal'  # c\ntags = ['a', 'b']\n+++\nB\n"
    front, body = front_matter.split(text)
    details = from_front_matter(front)
    changed = details.with_changes(title="New", hashtags=("a", "b", "c"))
    assert front_matter.join(front_matter_with(changed, front), body) == (
        "+++\ntitle   = 'New'  # c\ntags = ['a', 'b', 'c']\n+++\nB\n")


# -- keys left empty to fill: Obsidian templates, Hugo and Jekyll archetypes ----

TEMPLATES = {
    "obsidian": "---\ntitle:\ndate:\ntags:\n---\n\nBody text.\n",
    "templater": "---\ntitle: \ndescription:\ndate: \n---\nBody text.\n",
    "hugo": '---\ntitle:\ndate:\ndraft: true\ntags: []\n---\n\nBody text.\n',
    "jekyll": "---\nlayout: post\ntitle:\ndate:\ncategories:\n---\nBody text.\n",
}


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_a_detail_set_on_an_empty_key_is_valid_yaml_and_reads_back(name):
    text = TEMPLATES[name]
    front, body = front_matter.split(text)
    assert unchanged_save(text) == text
    details = from_front_matter(front).with_changes(
        title="Hello world", summary="A summary", published_at=1791876318,
        hashtags=("one", "two"))
    out = front_matter.join(front_matter_with(details, front), body)
    assert "title: Hello world\n" in out and "date: 2026-10-13T07:25:18Z\n" in out
    again = from_front_matter(front_matter.split(out)[0])
    assert (again.title, again.summary, again.published_at, again.hashtags) == (
        "Hello world", "A summary", 1791876318, ("one", "two"))
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(out.split("---\n")[1])["title"] == "Hello world"


@pytest.mark.parametrize("written", ["~", "null", "Null", "NULL", ""])
def test_a_yaml_null_is_no_value(written):
    front, body = front_matter.split(
        f"---\ntitle: {written}\nsummary: {written}   # to fill\ntags: {written}\n---\n\nB\n")
    details = from_front_matter(front)
    assert (details.title, details.summary, details.hashtags) == ("", "", ())
    tags = details.with_changes(identifier="post").draft_tags("B")
    assert not [tag for tag in tags if tag[0] in ("title", "summary", "t")]


def test_a_quoted_null_is_the_word():
    front, _ = front_matter.split('---\ntitle: "null"\n---\n')
    assert from_front_matter(front).title == "null"


def test_a_value_set_on_a_key_with_only_a_comment_keeps_the_comment():
    front, body = front_matter.split("---\nsummary:    # to fill\ntitle: ~\n---\nB\n")
    details = from_front_matter(front).with_changes(summary="Filled", title="Set")
    assert front_matter.join(front_matter_with(details, front), body) == (
        "---\nsummary:    Filled # to fill\ntitle: Set\n---\nB\n")


# -- more shapes from the wild (review L4) -------------------------------------

def changed(text, **change):
    front, body = front_matter.split(text)
    details = from_front_matter(front).with_changes(**change)
    return front_matter.join(front_matter_with(details, front), body)


def test_a_toml_multi_line_list_stays_multi_line():
    text = '+++\ntitle = "T"\ntags = [\n  "go",\n  "hugo",\n]\n+++\n\nB\n'
    assert changed(text, hashtags=("go", "hugo", "extra")) == text.replace(
        '  "hugo",\n', '  "hugo",\n  "extra",\n')


def test_a_tags_comment_is_never_a_tag():
    front, _ = front_matter.split('---\ntags:\n  - post\n  - "#idea"   # with a hash\n---\n')
    assert from_front_matter(front).hashtags == ("post", "idea")


def test_jekyll_space_separated_tags_are_read_as_jekyll_reads_them():
    front, _ = front_matter.split("---\ntags: ruby jekyll\n---\n")
    assert from_front_matter(front).hashtags == ("ruby", "jekyll")
    front, _ = front_matter.split('---\ntags: "a, b"\n---\n')
    assert from_front_matter(front).hashtags == ("a", "b")


def test_hugo_capitalised_keys_are_read_and_rewritten_in_place():
    text = '---\nTitle: "Caps Keys"\nDescription: "Hugo keys"\nTags: ["a"]\n---\n\nB\n'
    front, _ = front_matter.split(text)
    details = from_front_matter(front)
    assert (details.title, details.summary, details.hashtags) == ("Caps Keys", "Hugo keys", ("a",))
    assert changed(text, title="New") == text.replace('Title: "Caps Keys"', 'Title: "New"')


def test_zola_taxonomy_tags_are_read_and_written_in_their_table():
    text = ('+++\ntitle = "Zola post"\ndate = 2024-03-01\n\n[taxonomies]\n'
            'tags = ["rust", "ssg"]\n\n[extra]\ncover = "x.png"\n+++\n\nB\n')
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == ("rust", "ssg")
    assert changed(text, hashtags=("rust", "ssg", "web")) == text.replace(
        'tags = ["rust", "ssg"]', 'tags = ["rust", "ssg", "web"]')


def test_a_date_that_is_no_date_is_kept_as_written():
    text = "---\ntitle: Eleventy\ndate: Last Modified\n---\n\nB\n"
    front, _ = front_matter.split(text)
    assert from_front_matter(front).published_at is None and "date" in kept_in_file(front)
    assert changed(text, published_at=1791876318) == text


def test_tags_written_as_a_string_stay_a_string():
    assert changed("---\ntags: ruby jekyll\n---\nB\n", hashtags=("ruby", "jekyll", "web")) == \
        "---\ntags: ruby jekyll web\n---\nB\n"
    assert changed('---\ntags: "a, b"\n---\nB\n', hashtags=("a", "b", "c")) == \
        '---\ntags: "a, b, c"\n---\nB\n'
    # A tag the string can't hold whole: a list then.
    assert changed("---\ntags: ruby jekyll\n---\nB\n", hashtags=("ruby", "two words")) == \
        "---\ntags: [ruby, two words]\n---\nB\n"


# -- TOML arrays over several lines, as people write them (review N1) ----------

MULTI_LINE_TOML = {
    "trailing commas": '+++\ntags = [\n  "a",\n  "b",\n]\n+++\nB\n',
    "no last comma": '+++\ntags = [\n  "a",\n  "b"\n]\n+++\nB\n',
    "comment on an item": '+++\ntags = [\n  "a",  # first\n  "b",\n]\n+++\nB\n',
    "single quotes": "+++\ntags = [\n  'a',\n  'b',\n]\n+++\nB\n",
    "two on a line": '+++\ntags = [\n  "a", "b",\n  "c",\n]\n+++\nB\n',
    "leading commas": '+++\ntags = [\n  "a"\n  , "b"\n]\n+++\nB\n',
}


@pytest.mark.parametrize("shape", sorted(MULTI_LINE_TOML))
def test_a_tag_added_to_a_toml_array_over_lines_is_valid_toml(shape):
    import tomllib
    text = MULTI_LINE_TOML[shape]
    front, _ = front_matter.split(text)
    tags = from_front_matter(front).hashtags
    out = changed(text, hashtags=tags + ("Extra",))
    assert tomllib.loads(out.split("+++\n")[1])["tags"] == list(tags) + ["Extra"]
    assert from_front_matter(front_matter.split(out)[0]).hashtags == tags + ("Extra",)
    fewer = changed(text, hashtags=tags[1:])
    assert tomllib.loads(fewer.split("+++\n")[1])["tags"] == list(tags[1:])


def test_a_toml_array_over_lines_keeps_its_lines_and_comments():
    out = changed(MULTI_LINE_TOML["no last comma"], hashtags=("a", "b", "Extra"))
    assert out == '+++\ntags = [\n  "a",\n  "b",\n  "Extra"\n]\n+++\nB\n'
    out = changed(MULTI_LINE_TOML["comment on an item"], hashtags=("a", "b", "Extra"))
    assert out == '+++\ntags = [\n  "a",  # first\n  "b",\n  "Extra",\n]\n+++\nB\n'


@pytest.mark.parametrize("written", ["", " ~", " idea"])
def test_tags_left_to_fill_or_one_word_become_a_list(written):
    """Review N2: Hugo and Eleventy read "tags: idea draft" as one tag."""
    yaml = pytest.importorskip("yaml")
    text = f"---\ntitle: x\ntags:{written}\n---\n\nB\n"
    out = changed(text, hashtags=("idea", "draft"))
    assert yaml.safe_load(out.split("---\n")[1])["tags"] == ["idea", "draft"]


def test_a_yaml_flow_list_over_lines_is_read_and_rewritten_whole():
    """Review N3: "tags: [a,\\n  b]" was read as the tag "[a"."""
    yaml = pytest.importorskip("yaml")
    text = "---\ntitle: x\ntags: [a,\n  b]   # the tags\nlayout: post\n---\n\nB\n"
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == ("a", "b")
    assert unchanged_save(text) == text
    out = changed(text, hashtags=("a", "b", "new"))
    assert out == "---\ntitle: x\ntags: [a, b, new]   # the tags\nlayout: post\n---\n\nB\n"
    assert yaml.safe_load(out.split("---\n")[1])["tags"] == ["a", "b", "new"]


@pytest.mark.parametrize("header", ["[taxonomies]  # the terms", "[ taxonomies ]"])
def test_zola_taxonomies_header_with_a_comment_or_spaces(header):
    import tomllib
    text = f'+++\ntitle = "Z"\n{header}\ntags = ["rust", "web"]\n+++\n\nB\n'
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == ("rust", "web")
    out = changed(text, hashtags=("rust", "web", "zola"))
    assert out == text.replace('["rust", "web"]', '["rust", "web", "zola"]')
    assert tomllib.loads(out.split("+++\n")[1])["taxonomies"]["tags"] == ["rust", "web", "zola"]


# -- comments in and after YAML flow lists (round 5, M1 and L4) -----------------

@pytest.mark.parametrize("text, tags", [
    ("---\ntags: [a,  # first\n  b]\n---\nB\n", ("a", "b")),
    ("---\ntags: [a,  # see [1]\n  b]   # the tags\n---\nB\n", ("a", "b")),
    ('---\ntags: ["a # not a comment",\n  b]\n---\nB\n', ("a # not a comment", "b")),
    ("---\ntags: [a, b]  # see [1]\n---\nB\n", ("a", "b")),
])
def test_a_comment_in_or_after_a_flow_list_is_no_tag(text, tags):
    yaml = pytest.importorskip("yaml")
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == tags
    assert unchanged_save(text) == text
    out = changed(text, hashtags=tags + ("new",))
    assert yaml.safe_load(out.split("---\n")[1])["tags"] == list(tags) + ["new"]
    assert from_front_matter(front_matter.split(out)[0]).hashtags == tags + ("new",)


def test_the_comments_of_a_flow_list_over_lines_are_kept_after_it():
    out = changed("---\ntags: [a,  # first\n  b]   # the tags\n---\nB\n",
                  hashtags=("a", "b", "new"))
    assert out == "---\ntags: [a, b, new]   # the tags  # first\n---\nB\n"
    out = changed("---\ntags: [a, b]  # see [1]\n---\nB\n", hashtags=("a", "b", "new"))
    assert out == "---\ntags: [a, b, new]  # see [1]\n---\nB\n"


# -- an apostrophe inside a word is no quote (round 6, H1 and L1) -------------

@pytest.mark.parametrize("typed, tags", [
    ("nostr, don't panic, books", ("nostr", "don't panic", "books")),
    ("women's history, art, music", ("women's history", "art", "music")),
    ("rock'n'roll, jazz", ("rock'n'roll", "jazz")),
    ('a, "b, c", d', ("a", "b, c", "d")),
])
def test_an_apostrophe_in_a_typed_hashtag_is_part_of_it(typed, tags):
    assert hashtags_from(typed) == tags
    assert hashtags_from(hashtags_text(tags)) == tags


@pytest.mark.parametrize("text, tags", [
    ("---\ntags: o'reilly, books\n---\nB\n", ("o'reilly", "books")),
    ("---\ntags: [women's history, art]\n---\nB\n", ("women's history", "art")),
    ("---\ntags: [don't,\n  panic]\n---\nB\n", ("don't", "panic")),
    ("---\ntags: ['it''s', b]\n---\nB\n", ("it's", "b")),
])
def test_an_apostrophe_in_a_files_hashtag_is_part_of_it(text, tags):
    yaml = pytest.importorskip("yaml")
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == tags
    out = changed(text, hashtags=tags + ("Extra",))
    parsed = yaml.safe_load(out.split("---\n")[1])["tags"]
    parsed = parsed if isinstance(parsed, list) else [t.strip() for t in parsed.split(",")]
    assert parsed == list(tags) + ["Extra"]


@pytest.mark.parametrize("text", [
    "---\ntags: [a, b]\n  # a note on the tags\nlayout: post\n---\nB\n",
    "---\ntags: [a,\n  b]\n  # a note on the tags\nlayout: post\n---\nB\n",
])
def test_a_comment_line_after_a_flow_list_stays_after_it(text):
    """Round 6, L2: such a list was kept read-only, its tags unread."""
    yaml = pytest.importorskip("yaml")
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == ("a", "b")
    out = changed(text, hashtags=("a", "b", "new"))
    assert out == "---\ntags: [a, b, new]\n  # a note on the tags\nlayout: post\n---\nB\n"
    assert yaml.safe_load(out.split("---\n")[1])["tags"] == ["a", "b", "new"]


@pytest.mark.parametrize("text", [
    "---\ntags: [a, [b, c], d]\n---\nB\n",
    "---\ntags: [a, {b: c}]\n---\nB\n",
])
def test_a_list_nested_in_the_tags_keeps_them_as_written(text):
    """Round 6, L3: "[b" and "c]" were read, and would be published, as tags."""
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == () and "tags" in kept_in_file(front)
    assert changed(text, hashtags=("x",)) == text


@pytest.mark.parametrize("text, expected", [
    ('---\n"tags": [a, b]\n---\nB\n', '---\n"tags": [a, b, Extra]\n---\nB\n'),
    ("---\n'tags': [a, b]\n---\nB\n", "---\n'tags': [a, b, Extra]\n---\nB\n"),
    ('+++\n"tags" = ["a", "b"]\n+++\nB\n', '+++\n"tags" = ["a", "b", "Extra"]\n+++\nB\n'),
])
def test_a_quoted_key_is_its_key(text, expected):
    """Round 6, L4: a second "tags" was written beside a quoted one."""
    front, _ = front_matter.split(text)
    assert from_front_matter(front).hashtags == ("a", "b")
    assert changed(text, hashtags=("a", "b", "Extra")) == expected
