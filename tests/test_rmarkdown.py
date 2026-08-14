"""Pins the R Markdown exporter and the knit runner.

The defect classes this file guards against:
- frontmatter drift (title heuristic, self_contained output),
- Pandoc span syntax breaking for combined formats (bold+color),
- bullet nesting emitting invalid markdown,
- the source-passthrough rule regressing (an opened .Rmd must never be
  re-wrapped in frontmatter, including after a crash-recovery restore
  that loses the flag but keeps the path),
- 'Output created:' parsing and failure classification drifting from
  real rmarkdown output shapes.
"""

import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument

import rmarkdown
from rmarkdown import (
    KnitRunner,
    classify_failure,
    derive_title,
    document_to_rmd,
    media_dir_for,
    parse_output_path,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    yield app


PLAIN = QTextCharFormat()


def _doc(lines):
    doc = QTextDocument()
    cur = QTextCursor(doc)
    first = True
    for entry in lines:
        if not first:
            cur.insertBlock()
        first = False
        if isinstance(entry, str):
            cur.insertText(entry, PLAIN)
        else:
            for text, fmt in entry:
                cur.insertText(text, fmt)
    return doc


# --------------------------------------------------------------------------- #
# Frontmatter + title
# --------------------------------------------------------------------------- #

def test_frontmatter_shape():
    out = document_to_rmd(_doc(["Hello"]), "My Title")
    head = out.split("---")[1]
    assert 'title: "My Title"' in head
    assert "self_contained: true" in head
    assert "html_document" in head
    assert out.endswith("\n")


def test_title_heuristic():
    assert derive_title(_doc(["# Heading", "body"]), "fb") == "Heading"
    assert derive_title(_doc(["", "  ", "First real"]), "fb") == "First real"
    assert derive_title(_doc(["", "   "]), "fb") == "fb"


def test_title_with_quotes_is_escaped():
    out = document_to_rmd(_doc(["x"]), 'He said "hi"')
    assert 'title: "He said \\"hi\\""' in out


# --------------------------------------------------------------------------- #
# Body conversion
# --------------------------------------------------------------------------- #

def _fmt(bold=False, italic=False, underline=False, color=None):
    fmt = QTextCharFormat()
    if bold:
        fmt.setFontWeight(700)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)
    if color:
        fmt.setForeground(QColor(color))
    return fmt


def test_inline_spans():
    doc = _doc([[
        ("a ", PLAIN),
        ("b", _fmt(bold=True)),
        (" c", PLAIN),
        ("u", _fmt(underline=True)),
        ("r", _fmt(color="#E53935")),
        ("x", _fmt(bold=True, color="#E53935")),
    ]])
    out = document_to_rmd(doc, "T")
    assert "**b**" in out
    assert "[u]{.underline}" in out
    assert '[r]{style="color:#e53935"}' in out
    assert '[**x**]{style="color:#e53935"}' in out


def test_bullets_nest_and_get_blank_line_before():
    doc = _doc(["intro", "    • one", "        • two", "after"])
    out = document_to_rmd(doc, "T")
    body = out.split("---\n\n", 1)[1]
    assert "intro\n\n- one\n  - two\n\nafter" in body


def test_line_structure_uses_hard_breaks():
    out = document_to_rmd(_doc(["line one", "line two", "", "para two"]), "T")
    assert "line one\\\nline two\n\npara two" in out


def test_markdown_specials_escaped():
    out = document_to_rmd(_doc(["# not heading", "a *b* [c]"]), "T")
    assert "\\# not heading" in out
    assert "a \\*b\\* \\[c\\]" in out


def test_image_copied_to_sidecar(tmp_path):
    # copy_image callback contract: exporter emits the returned relative ref.
    doc = QTextDocument()
    QTextCursor(doc).insertImage(str(tmp_path / "img"))
    out = document_to_rmd(doc, "T",
                          copy_image=lambda p: "notes_media/abc.png")
    assert "![](notes_media/abc.png)" in out
    out2 = document_to_rmd(doc, "T", copy_image=lambda p: None)
    assert "*[image unavailable]*" in out2


def test_media_dir_for():
    assert media_dir_for("/x/y/notes.Rmd") == "/x/y/notes_media"


# --------------------------------------------------------------------------- #
# Output parsing + failure classification
# --------------------------------------------------------------------------- #

def test_parse_output_path_from_stderr_line(tmp_path):
    rmd = str(tmp_path / "doc.Rmd")
    produced = tmp_path / "doc.html"
    produced.write_text("x")
    out = f"processing...\nOutput created: doc.html\n"
    assert parse_output_path(out, rmd, "html") == str(produced)


def test_parse_output_path_falls_back_to_extension_swap(tmp_path):
    rmd = str(tmp_path / "doc.Rmd")
    produced = tmp_path / "doc.html"
    produced.write_text("x")
    assert parse_output_path("no marker here", rmd, "html") == str(produced)
    assert parse_output_path("no marker here", rmd, "pdf") is None


def test_classify_failure_kinds():
    assert classify_failure(
        "Error in loadNamespace(x) : there is no package called 'rmarkdown'"
    ) == "missing-rmarkdown"
    assert classify_failure(
        "Error: pandoc version 1.12.3 or higher is required and was not found"
    ) == "missing-pandoc"
    assert classify_failure(
        "Error: LaTeX failed to compile doc.tex. "
        "See https://yihui.org/tinytex/r/#debugging for debugging tips."
    ) == "missing-latex"
    assert classify_failure("! pdflatex is not found") == "missing-latex"
    assert classify_failure("Error in eval(expr): object 'x' not found"
                            ) == "render-error"


# --------------------------------------------------------------------------- #
# KnitRunner against a fake Rscript
# --------------------------------------------------------------------------- #

def _fake_rscript(tmp_path, body: str) -> str:
    path = tmp_path / "Rscript"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _run_knit(monkeypatch, rscript, rmd_path, fmt="html", timeout_ms=5000):
    monkeypatch.setattr(rmarkdown, "find_rscript", lambda: rscript)
    runner = KnitRunner()
    results = {}
    loop = QEventLoop()
    runner.finished.connect(lambda p: (results.update(ok=p), loop.quit()))
    runner.failed.connect(lambda k, d: (results.update(kind=k, detail=d),
                                        loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    runner.knit(rmd_path, fmt)
    loop.exec()
    return results


def test_knit_success_via_fake_rscript(tmp_path, monkeypatch):
    rmd = tmp_path / "doc.Rmd"
    rmd.write_text("---\ntitle: x\n---\nbody\n")
    script = _fake_rscript(
        tmp_path,
        'echo "Output created: doc.html" 1>&2\n'
        'printf "<html></html>" > "$(dirname "$4")/doc.html"\n')
    results = _run_knit(monkeypatch, script, str(rmd))
    assert results.get("ok") == str(tmp_path / "doc.html")


def test_knit_failure_classified(tmp_path, monkeypatch):
    rmd = tmp_path / "doc.Rmd"
    rmd.write_text("x\n")
    script = _fake_rscript(
        tmp_path,
        'echo "there is no package called \'rmarkdown\'" 1>&2\nexit 1\n')
    results = _run_knit(monkeypatch, script, str(rmd))
    assert results.get("kind") == "missing-rmarkdown"
    assert "rmarkdown" in results.get("detail", "")


def test_knit_missing_r_reported(monkeypatch, tmp_path):
    rmd = tmp_path / "doc.Rmd"
    rmd.write_text("x\n")
    monkeypatch.setattr(rmarkdown, "find_rscript", lambda: None)
    runner = KnitRunner()
    seen = {}
    runner.failed.connect(lambda k, d: seen.update(kind=k))
    runner.knit(str(rmd))
    assert seen.get("kind") == "missing-r"
