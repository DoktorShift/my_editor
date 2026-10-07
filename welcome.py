#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later

import html

from constants import APP_DISPLAY_NAME
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


# The keys the page names, by command, as Windows and Linux write them;
# the window passes each platform's own (⌘N on a Mac, Strg+N in German).
DEFAULT_KEYS = {"file.new": "Ctrl+N", "file.open": "Ctrl+O", "file.save": "Ctrl+S",
                "search.find": "Ctrl+F"}


def _page_text(message: str) -> str:
    """A plain (translated) text, made safe to put in the page."""
    return html.escape(message, quote=False)


def welcome_html(keys=None) -> str:
    """Build the HTML shown in the first-run Welcome tab. ``keys`` maps a
    command to its keys as the platform writes them (DEFAULT_KEYS).

    It says how to write, and where publishing lives when someone wants
    it: the Nostr menu, which is the one way in to everything Nostr (the
    page itself shows nothing of it). Colors are not advertised: they
    stay in local files and never reach a published article."""
    keys = {**DEFAULT_KEYS, **(keys or {})}

    def key_line(command: str, words: str) -> str:
        return _page_text(words).format(keys=f"<b>{_page_text(keys[command])}</b>")

    title = _page_text(_("Welcome to {app}").format(app=APP_DISPLAY_NAME))
    intro = _page_text(_("A minimal, distraction-free text editor - just you and your words."))
    get_started = _page_text(_("Get started"))
    # Each line names its keys in bold.
    lines = [
        key_line("file.new", _("{keys} - new file")),
        key_line("file.open", _("{keys} - open a file")),
        key_line("file.save", _("{keys} - save")),
        key_line("search.find", _("{keys} - find")),
        _page_text(_("View > Appearance - light, dark, or the same as your computer")),
        _page_text(_("Help > Keyboard Shortcuts - for the full list")),
    ]
    items = "\n".join(f"        <li>{line}</li>" for line in lines)
    publish = _page_text(_("When you want to publish"))
    publishing = _page_text(_(
        "Everything for it is in the Nostr menu, whenever you are ready: create an "
        "account or connect your signer app, then Publish as Article shows how your "
        "article will look before it goes out. Drafts keeps unfinished work private to "
        "your account, and Imports brings in posts from your blog or feed as drafts."))
    closing = _page_text(_("That's it. This tab is just a note like any other - close it, "
                           "edit it, or make it your own."))
    return f"""
    <h1>{title}</h1>
    <p>{intro}</p>
    <h2>{get_started}</h2>
    <ul>
{items}
    </ul>
    <h2>{publish}</h2>
    <p>{publishing}</p>
    <p>{closing}</p>
    """
