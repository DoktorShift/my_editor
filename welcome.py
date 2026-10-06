#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later

import html

from constants import APP_DISPLAY_NAME, TEXT_COLORS
from i18n import _


def is_pristine_welcome(editor) -> bool:
    """The welcome tab just as MyEditor opened it: never saved to a file and
    not edited since.

    Only then is it titled Welcome, and reopened as the welcome tab after an
    update. Once saved or changed it is a document like any other: its
    title is its file name, and an update restart brings back that file.
    """
    return (bool(getattr(editor, "_is_welcome", False))
            and not getattr(editor, "_file_path", None)
            and not editor.document().isModified())


def _page_text(message: str) -> str:
    """A plain (translated) text, made safe to put in the page."""
    return html.escape(message, quote=False)


def welcome_html() -> str:
    """Build the HTML shown in the first-run Welcome tab."""
    colors = " ".join(
        f'<span style="color:{color.name()}">{_page_text(_(name))}</span>'
        for name, color in TEXT_COLORS.items()
    )
    title = _page_text(_("Welcome to {app}").format(app=APP_DISPLAY_NAME))
    intro = _page_text(_("A minimal, distraction-free text editor - just you and your words."))
    get_started = _page_text(_("Get started"))
    # Each line names its keys in bold.
    lines = [
        _("<b>Ctrl+N</b> - new file"),
        _("<b>Ctrl+O</b> - open a file"),
        _("<b>Ctrl+S</b> - save"),
        _("<b>Ctrl+F</b> - find"),
        _("<b>Ctrl+Shift+T</b> - switch between dark and light theme"),
        _page_text(_("Help > Keyboard Shortcuts - for the full list")),
    ]
    items = "\n".join(f"        <li>{line}</li>" for line in lines)
    color_line = _page_text(_("Colors, right where you need them: {colors}")).format(
        colors=colors)
    closing = _page_text(_("That's it. This tab is just a note like any other - close it, "
                           "edit it, or make it your own."))
    return f"""
    <h1>{title}</h1>
    <p>{intro}</p>
    <h2>{get_started}</h2>
    <ul>
{items}
    </ul>
    <p>{color_line}</p>
    <p>{closing}</p>
    """
