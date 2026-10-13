# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change MyEditor writes to a front matter is read by a YAML parser
(or tomllib) as that change and nothing else, or is not written at all.

Held over front matter collected from the wild and from reviews
(front_matter_corpus/, one shape each, valid or not) and the samples of
test_front_matter.py: for each change, either the detail is kept as written
(read-only, the file untouched) or the parser reads the old front matter
again with only that detail changed, to what was set.
"""

import pathlib
import re
import tomllib
from datetime import date, datetime, timezone

import pytest
import yaml

import front_matter
from nostr.article_details import (
    from_front_matter, front_matter_with, kept_in_file)
from tests.test_front_matter import HUGO, JEKYLL, TOML

CORPUS = pathlib.Path(__file__).parent / "front_matter_corpus"
SAMPLES = [(path.name, path.read_bytes().decode("utf-8")) for path in sorted(CORPUS.iterdir())] \
    + [("hugo", HUGO), ("jekyll", JEKYLL), ("toml", TOML)]
# Each detail's names, as article_details reads them.
NAMES = {"title": {"title"}, "summary": {"summary", "description"},
         "image": {"image", "cover", "cover_image"}, "tags": {"tags", "hashtags"},
         "slug": {"slug"}, "date": {"date", "published_at"}}
WHEN = 1791876318
MISSING = object()


def changes(details):
    """(detail, change, what the parser should read for it) of each change tried."""
    return [
        ("tags", {"hashtags": details.hashtags + ("Extra",)}, details.hashtags + ("Extra",)),
        ("tags", {"hashtags": details.hashtags[1:]}, details.hashtags[1:]),
        ("tags", {"hashtags": ()}, ()),
        ("tags", {"hashtags": ("it's, ok", 'say "hi"')}, ("it's, ok", 'say "hi"')),
        ("title", {"title": "Hello world"}, "Hello world"),
        ("title", {"title": 'Tips #1: "Q" it\'s \\ ok'}, 'Tips #1: "Q" it\'s \\ ok'),
        ("title", {"title": ""}, ""),
        ("summary", {"summary": "New summary: with colon"}, "New summary: with colon"),
        ("summary", {"summary": "Line one\nLine two"}, "Line one\nLine two"),
        ("image", {"image": "https://example.com/a b.png"}, "https://example.com/a b.png"),
        ("slug", {"identifier": "new-slug"}, "new-slug"),
        ("date", {"published_at": WHEN}, WHEN),
    ]


# libyaml, through PyYAML: the reader Jekyll's and Hugo's are like (a tab
# after text is a space), and not the one front_matter.parsed uses.
LIBYAML = getattr(yaml, "CSafeLoader", None)


def parser_read(text):
    """The front matter of ``text`` as libyaml or tomllib reads it; None
    when they read none."""
    front, _body = front_matter.split(text)
    if front is None:
        return {}
    written = "".join(line + "\n" for entry in front.entries for line in entry.lines)
    try:
        read = tomllib.loads(written) if front.syntax == front_matter.TOML \
            else yaml.load(written, Loader=LIBYAML) or {}
    except (yaml.YAMLError, ValueError):
        return None
    return read if isinstance(read, dict) else None


def under(detail, read):
    """``(name, value)`` of each place the parser reads ``detail`` in."""
    found = [(key, value) for key, value in read.items()
             if isinstance(key, str) and key.lower() in NAMES[detail]]
    for table in (value for key, value in read.items()
                  if detail == "tags" and key == "taxonomies" and isinstance(value, dict)):
        found += [(("taxonomies", key), value) for key, value in table.items()
                  if key.lower() in NAMES[detail]]
    return found


def holds(detail, value, meant):
    """Whether ``value``, read by a parser, is the detail as ``meant``."""
    if detail == "tags":
        items = value if isinstance(value, list) else \
            value.split(",") if "," in value else value.split()
        tags = [str(item).strip().lstrip("#").strip() for item in items]
        return [tag.lower() for tag in tags if tag] == [tag.lower() for tag in meant]
    if detail == "date":
        moment = datetime.fromtimestamp(meant, tz=timezone.utc)
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) == moment if value.tzinfo \
                else value == moment.replace(tzinfo=None)
        if isinstance(value, date):
            return value == moment.date()
        written = str(value).strip()
        if written.isdigit():
            return int(written) == meant
        # A date YAML reads as a text ("2026-10-13 02:25:18 -0500", Jekyll's).
        read = datetime.fromisoformat(re.sub(r"\s+([+-]\d)", r"\1", written))
        return read.astimezone(timezone.utc) == moment if read.tzinfo \
            else read == moment.replace(tzinfo=None)
    return isinstance(value, str) and value.strip() == meant


@pytest.mark.parametrize("name, text", SAMPLES, ids=[name for name, _text in SAMPLES])
def test_a_change_is_written_as_a_parser_reads_it_or_not_at_all(name, text):
    if LIBYAML is None:
        pytest.skip("PyYAML without libyaml")
    front, body = front_matter.split(text)
    details = from_front_matter(front)
    kept = kept_in_file(front)
    before = parser_read(text)
    if before is None:
        assert kept == frozenset(NAMES) | {"coauthors", "zap_split"}
    for detail, change, meant in changes(details):
        out = front_matter.join(front_matter_with(details.with_changes(**change), front), body)
        if detail in kept:
            assert out == text, (detail, change)
            continue
        after = parser_read(out)
        assert after is not None, (detail, change, out)
        others = [key for key in before.keys() | after.keys()
                  if before.get(key, MISSING) != after.get(key, MISSING)
                  and not (isinstance(key, str) and key.lower() in NAMES[detail])
                  and not (detail == "tags" and key == "taxonomies")]
        assert others == [], (detail, change, out)
        found = under(detail, after)
        if not meant:
            # Gone, or left as written when it held nothing ("tags: []").
            assert all(value in (None, "", []) for _name, value in found), (detail, change, out)
        else:
            assert len(found) == 1 and holds(detail, found[0][1], meant), (detail, change, out)
            if detail == "tags" and found[0][0] != ("taxonomies", "tags"):
                outside = {key: value for key, value in after.get("taxonomies", {}).items()} \
                    if isinstance(after.get("taxonomies"), dict) else {}
                assert outside == ({key: value for key, value in before["taxonomies"].items()}
                                   if isinstance(before.get("taxonomies"), dict) else {})


# -- a write a parser reads otherwise is never made -----------------------------

TEXT = "---\ntitle: T\ntags: [a, b]\nlayout: post\n---\nB\n"


def broken_on(monkeypatch, word):
    """front_matter.rewritten, made to write a tags entry YAML reads
    otherwise (an unclosed quote, a line taken from the next key) whenever
    the tags hold ``word``."""
    real = front_matter.rewritten

    def rewritten(old, value, **kwargs):
        written = real(old, value, **kwargs)
        if old.key == "tags" and any(word in item for item in value):
            return front_matter.Entry(old.key, (written.lines[0].replace("[", '["', 1),
                                                "layout: page"), old.syntax)
        return written
    monkeypatch.setattr(front_matter, "rewritten", rewritten)


def test_a_detail_whose_change_would_be_read_otherwise_is_kept(monkeypatch):
    """A change to it is refused before one is made: the field is read-only."""
    broken_on(monkeypatch, "")
    front, body = front_matter.split(TEXT)
    assert kept_in_file(front) == frozenset({"tags"})
    details = from_front_matter(front)
    assert details.hashtags == ("a", "b")
    assert front_matter.join(front_matter_with(
        details.with_changes(hashtags=("a", "b", "c")), front), body) == TEXT


def test_a_change_read_otherwise_is_not_written_and_its_detail_is_kept_from_then(monkeypatch):
    """One value written wrong: the file keeps the detail as written, and
    from then on it is read-only; the other changes are written."""
    broken_on(monkeypatch, "Bad")
    front, body = front_matter.split(TEXT)
    assert kept_in_file(front) == frozenset()
    details = from_front_matter(front).with_changes(hashtags=("a", "Bad"), title="New")
    written = front_matter_with(details, front)
    assert front_matter.join(written, body) == TEXT.replace("title: T", "title: New")
    assert kept_in_file(written) == frozenset({"tags"})


REFUSED_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import front_matter
import main_window

real = front_matter.rewritten
def rewritten(old, value, **kwargs):            # wrong for one value only
    written = real(old, value, **kwargs)
    if old.key == "tags" and "Bad" in value:
        return front_matter.Entry(old.key, (written.lines[0].replace("[", '["', 1),),
                                  old.syntax)
    return written
front_matter.rewritten = rewritten

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
r = {"tags read-only before": panel.tags_edit.isReadOnly()}
w._set_article_details(ed, w._article_details_of(ed).with_changes(
    hashtags=("a", "Bad"), title="New"))
w._save_to(path)
app.processEvents()
r["saved"] = open(path, encoding="utf-8").read()
r["said"] = w.status.currentMessage()
r["tags read-only"] = panel.tags_edit.isReadOnly()
r["tags shown"] = panel.tags_edit.text()
r["hashtags"] = list(w._article_details_of(ed).hashtags)
r["modified"] = ed.document().isModified()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_change_refused_on_saving_is_said_and_its_detail_shown_as_in_the_file(tmp_path):
    from tests.app_process import run_window_script
    path = tmp_path / "post.md"
    path.write_text(TEXT, encoding="utf-8")
    r = run_window_script(REFUSED_SCRIPT, tmp_path, str(path))
    assert r["tags read-only before"] is False
    assert r["saved"] == TEXT.replace("title: T", "title: New")
    assert r["said"] == ("Saved. Some article details stay as written in the file: "
                         "MyEditor can’t change them there safely.")
    assert r["tags read-only"] is True and r["hashtags"] == ["a", "b"]
    assert r["tags shown"] == "a, b" and r["modified"] is False
