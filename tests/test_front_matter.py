# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins a Markdown file's front matter (front_matter.py, the article
details it holds in nostr/article_details.py, and a .md file's way in and
out of the window).

What must hold (decision Q-F):

  An article's details live in the file's front matter, at the top, the
  way Jekyll, Hugo, Obsidian and iA Writer keep them: written only when
  there are details (or the file had front matter), never shown in the
  text, never published as text. Entries MyEditor does not know stay
  exactly as written, comments and nested lines too. A file whose front
  matter used to show as a heading and a divider now opens with its
  text alone.

  Values are read as front matter in the wild writes them (plain or
  quoted texts, lists in brackets or as "- item" lines; ``description``,
  ``cover`` and ``hashtags`` too) and written so that YAML reads them back
  as the same text.
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

FILE = """---
title: "Hello: World"
description: A short one
cover: https://cdn.example/c.jpg
tags:
  - bitcoin
  - Nostr
layout: post   # Jekyll's
params:
  toc: true
date: 2026-10-01T08:30:00Z
---

# The text

Body.
"""


def test_without_front_matter_the_text_is_all_body():
    assert front_matter.split("# Title\n\nText") == (None, "# Title\n\nText")
    assert front_matter.split("Text\n---\nmore\n---\n")[0] is None   # not at the top


def test_the_block_is_found_with_a_byte_order_mark_and_windows_line_ends():
    entries, body = front_matter.split("﻿---\r\ntitle: A\r\n---\r\nText")
    assert [entry.key for entry in entries] == ["title"] and body == "Text"
    assert front_matter.split("---\n---\nText") == ([], "Text")


def test_values_are_read_as_written_in_the_wild():
    entries, body = front_matter.split(FILE)
    values = {entry.key: entry.value for entry in entries}
    assert values["title"] == "Hello: World"
    assert values["tags"] == ["bitcoin", "Nostr"]
    assert values["layout"] == "post   # Jekyll's"
    assert body.startswith("\n# The text")
    one, _ = front_matter.split("---\na: 'it''s'\nb: [x, \"y, z\"]\nc: \"line\\nbreak\"\n---\n")
    assert [entry.value for entry in one] == ["it's", ["x", "y, z"], "line\nbreak"]


@pytest.mark.parametrize("value", ["plain words", "true", "123", "a: b", "#hash", " padded ",
                                   "Ünïcödé", "\"quoted\"", "", "- dash", "[bracket]"])
def test_what_is_written_reads_back_as_the_same_text(value):
    entry = front_matter.entry("k", value)
    entries, _ = front_matter.split(front_matter.join([entry], "x"))
    assert entries[0].value == value


def test_the_details_are_read_from_other_apps_keys():
    details = from_front_matter(front_matter.split(FILE)[0])
    assert details.title == "Hello: World" and details.summary == "A short one"
    assert details.image == "https://cdn.example/c.jpg"
    assert details.hashtags == ("bitcoin", "nostr")
    assert details.published_at == int(datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc).timestamp())
    assert from_front_matter(None) == ArticleDetails()


@pytest.mark.parametrize("written, expected", [
    ("1700000000", 1700000000),
    ("2026-10-01", int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())),
    ("2026-10-01 10:30:00+02:00", int(datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc).timestamp())),
    ("soon", None), ("", None)])
def test_dates_are_read_in_their_common_forms(written, expected):
    entries, _ = front_matter.split(f"---\ndate: {written}\n---\n")
    assert from_front_matter(entries).published_at == expected


def test_written_back_each_detail_stays_where_it_was_and_unknown_entries_as_they_were():
    entries, _ = front_matter.split(FILE)
    details = from_front_matter(entries).with_changes(
        summary="", title="New title", identifier="new-title")
    out = front_matter_with(details, entries)
    keys = [entry.key for entry in out]
    assert keys == ["title", "cover", "tags", "layout", "params", "date", "slug"]
    text = front_matter.join(out, "Body.\n")
    assert "layout: post   # Jekyll's\n" in text and "params:\n  toc: true\n" in text
    assert "description" not in text                      # emptied: left out
    assert "tags: [bitcoin, nostr]\n" in text
    assert 'date: "2026-10-01T08:30:00Z"\n' in text
    assert text.endswith("---\n\nBody.\n")


def test_no_details_and_no_front_matter_write_none():
    assert front_matter_with(ArticleDetails(), None) == []
    assert front_matter.join([], "Body") == "Body"


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.article_details import ArticleDetails

path, plain = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
w.open_path(path)
ed = w.current_editor()
r = {"text": ed.toPlainText(), "title": w._article_details_of(ed).title,
     "published": w._publish_text(ed, "markdown")}
w._set_article_details(ed, w._article_details_of(ed).with_changes(summary="Set here"))
w.save()
r["saved"] = open(path, encoding="utf-8").read()
w.open_path(plain)
ed2 = w.current_editor()
w.save()
r["plain_saved"] = open(plain, encoding="utf-8").read()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_markdown_file_in_the_window(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(FILE, encoding="utf-8")
    plain = tmp_path / "plain.md"
    plain.write_text("# Just text\n\nNo details.\n", encoding="utf-8")
    r = run_window_script(SCRIPT, tmp_path, str(path), str(plain))
    assert r["text"].startswith("The text") and "title:" not in r["text"]
    assert r["title"] == "Hello: World"
    assert "---" not in r["published"] and "layout" not in r["published"]
    saved = r["saved"]
    assert saved.startswith('---\ntitle: "Hello: World"\ndescription: Set here\n')
    assert "layout: post   # Jekyll's\n" in saved and "  - " not in saved
    assert saved.endswith("# The text\n\nBody.\n")
    assert r["plain_saved"] == "# Just text\n\nNo details.\n"
