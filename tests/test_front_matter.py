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
    ArticleDetails, from_front_matter, front_matter_with)
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
    assert out == HUGO.replace('title: "My post"', "title: A new title")


def test_a_changed_date_keeps_its_form_and_time_zone():
    front, body = front_matter.split(HUGO)
    later = from_front_matter(front).with_changes(
        published_at=moment(2024, 3, 2, 8, 0, offset=1))
    out = front_matter.join(front_matter_with(later, front), body)
    assert 'date: "2024-03-02T08:00:00+01:00"' in out
    for original, expected in (("2026-10-01", '"2026-10-02"'), ("1700000000", '"1700086400"'),
                               ("2026-10-01T08:00:00Z", '"2026-10-02T08:00:00Z"')):
        front, _ = front_matter.split(f"---\ndate: {original}\n---\n")
        details = from_front_matter(front)
        moved = details.with_changes(published_at=details.published_at + 86400)
        assert f"date: {expected}" in front_matter.join(front_matter_with(moved, front), "")


def test_a_detail_whose_entry_cant_be_read_is_written_beside_it_never_over_it():
    front, body = front_matter.split(HUGO)
    details = from_front_matter(front).with_changes(image="https://cdn.example/c.jpg")
    out = front_matter.join(front_matter_with(details, front), body)
    assert '  image: "images/hero.jpg"\n  alt: "A hero"\n' in out          # cover: kept
    assert "image: https://cdn.example/c.jpg\n" in out


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
        'tags = ["a", "b"]  # for the site', 'tags = ["a", "b", "Extra"]').replace(
        "draft = false\n", 'draft = false\nsummary = "Short"\n')
    import tomllib
    assert tomllib.loads(out.split("+++\n")[1])["params"] == {"toc": True}   # still a table
    assert "---" not in out


def test_pandoc_front_matter_closed_by_dots_is_front_matter():
    front, body = front_matter.split(PANDOC)
    assert body == "Body text.\n" and from_front_matter(front).title == "Pandoc"
    assert unchanged_save(PANDOC) == PANDOC
