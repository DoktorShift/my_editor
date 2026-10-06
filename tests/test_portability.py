# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Code that behaves the same on Windows, macOS and Linux.

A text file opened without an explicit encoding is read and written in
the system's default encoding, which is UTF-8 on macOS and Linux but a
legacy code page on Windows. Notes, translations and settings full of
umlauts would then turn into garbage or fail to load on Windows only,
so every text file the app (or a test) opens names its encoding.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".claude", ".git", "build", "dist", "node_modules", "__pycache__"}


def python_files():
    for path in sorted(ROOT.rglob("*.py")):
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            yield path


def text_io_without_encoding(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = node.args[1].value if (len(node.args) >= 2
                                          and isinstance(node.args[1], ast.Constant)) else None
            for k in node.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if isinstance(mode, str) and "b" in mode:
                continue
            yield node.lineno, "open"
        elif isinstance(func, ast.Attribute) and func.attr == "read_text" and not node.args:
            yield node.lineno, "read_text"
        elif isinstance(func, ast.Attribute) and func.attr == "write_text" and len(node.args) < 2:
            yield node.lineno, "write_text"


def test_every_text_file_is_opened_with_an_explicit_encoding():
    found = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, call in text_io_without_encoding(tree):
            found.append(f"{path.relative_to(ROOT)}:{line} {call}")
    assert found == [], "add encoding=\"utf-8\" to:\n" + "\n".join(found)
