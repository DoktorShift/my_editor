# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Syntax highlighting starts off.

The setting is not persisted, so the default is what the user meets on
every launch, not just the first. It lives in two places: the flag the
editor reads and the menu item's check state. They have to agree,
because one is what the user is looking at and the other is what is
actually happening.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = pathlib.Path(__file__).resolve().parent.parent


def assignments(path, target):
    """Every literal assigned to ``target`` in the file, in order."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for lhs in node.targets:
            if (
                isinstance(lhs, ast.Attribute)
                and lhs.attr == target
                and isinstance(node.value, ast.Constant)
            ):
                found.append(node.value.value)
    return found


def test_the_editor_flag_starts_off():
    values = assignments("main_window.py", "syntax_highlighting")
    assert values, "syntax_highlighting is never assigned a literal"
    assert values[0] is False


def registered_checked(path, command_id):
    """The literal ``checked=`` a command is registered with (commands.py),
    for the call whose Command names ``command_id``."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Call) and getattr(first.func, "id", "") == "Command"
                and first.args and isinstance(first.args[0], ast.Constant)
                and first.args[0].value == command_id):
            continue
        for keyword in node.keywords:
            if keyword.arg == "checked" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value
    return None


def test_the_menu_item_starts_unchecked():
    assert registered_checked("main_window.py", "view.syntax_highlighting") is False


def test_it_matches_line_numbers_which_were_already_off():
    # The two live side by side and are the same kind of decision.
    assert assignments("main_window.py", "show_line_numbers")[0] is False


def test_the_toggle_still_exists_so_this_is_a_default_not_a_removal():
    source = (ROOT / "main_window.py").read_text(encoding="utf-8")
    assert "_toggle_syntax_highlighting" in source
    assert '"view.syntax_highlighting"' in source
