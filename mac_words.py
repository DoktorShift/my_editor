# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a word ends for Option-Delete on a Mac: where the Mac's own text
view says.

Option-Delete and Fn-Option-Delete delete a word in every Mac text field,
and AppKit decides what a word is: punctuation, apostrophes, Japanese and
Chinese without spaces, emoji. Qt's own word boundaries differ on many of
them, so the editor asks AppKit: a text view kept off screen is given the
paragraph and deletes the word itself, and the editor deletes the same
range in its own text (HtmlEditor._delete_reach).

Reached through rubicon-objc, as the spell checker is (spelling/macos.py).
Where AppKit cannot be reached, ``deleted_range`` answers None and the
editor uses Qt's boundaries.
"""

from __future__ import annotations

from typing import Optional, Tuple

_view = None
_unavailable = False


def _text_view():
    """The off-screen text view, made the first time it is asked for."""
    global _view, _unavailable
    if _view is None and not _unavailable:
        try:
            from rubicon.objc import NSMakeRect, ObjCClass
            from rubicon.objc.runtime import load_library
            load_library("AppKit")
            _view = ObjCClass("NSTextView").alloc().initWithFrame(
                NSMakeRect(0, 0, 100_000, 1_000))
        except (ImportError, OSError, ValueError, NameError):     # no AppKit here
            _unavailable = True
    return _view


_UTF16_LITTLE_ENDIAN = 0x94000100      # NSUTF16LittleEndianStringEncoding


def deleted_range(units: bytes, position: int, forward: bool) -> Optional[Tuple[int, int]]:
    """The range (start, end), in UTF-16 units of the paragraph ``units``
    (its UTF-16 little-endian bytes, exactly as Qt holds it, a lone
    surrogate included), that Option-Delete (or, ``forward``,
    Fn-Option-Delete) at ``position`` deletes in a Mac text view; None
    without AppKit."""
    view = _text_view()
    if view is None:
        return None
    from rubicon.objc import NSRange, ObjCClass
    from rubicon.objc.runtime import autoreleasepool
    with autoreleasepool():
        # The text view is given the very units, not a Python str (which
        # could not hold a lone surrogate on the way to AppKit).
        view.string = ObjCClass("NSString").alloc().initWithBytes(
            units, length=len(units), encoding=_UTF16_LITTLE_ENDIAN)
        before = int(view.string.length)
        view.setSelectedRange(NSRange(position, 0))
        if forward:
            view.deleteWordForward(None)
        else:
            view.deleteWordBackward(None)
        removed = before - int(view.string.length)
        start = int(view.selectedRange.location)
        view.string = ""
    return start, start + removed
