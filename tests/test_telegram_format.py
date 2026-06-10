"""Unit tests for the markdown -> Telegram HTML converter."""

from __future__ import annotations

import re

import pytest

from publishers.telegram.format import (
    CAPTION_LIMIT,
    TEXT_LIMIT,
    format_for_send,
)


def test_plain_text_round_trips_unchanged():
    out = format_for_send("hello world")
    assert out.chunks == ("hello world",)
    assert out.photo is None
    assert not out.is_threaded


def test_html_special_chars_escaped():
    out = format_for_send("a < b & c > d")
    assert out.chunks == ("a &lt; b &amp; c &gt; d",)


def test_bold_italic_strike_spoiler():
    out = format_for_send("**bold** _italic_ ~~strike~~ ||spoiler||")
    body = out.chunks[0]
    assert "<b>bold</b>" in body
    assert "<i>italic</i>" in body
    assert "<s>strike</s>" in body
    assert "<tg-spoiler>spoiler</tg-spoiler>" in body


def test_links_preserve_url_escaping():
    out = format_for_send('Read [this](https://example.com/?q=a&b=1) now')
    body = out.chunks[0]
    assert '<a href="https://example.com/?q=a&amp;b=1">this</a>' in body


def test_fenced_code_with_language():
    md = "```python\nprint('hi')\n```"
    out = format_for_send(md)
    body = out.chunks[0]
    assert '<pre><code class="language-python">' in body
    assert "print(&#x27;hi&#x27;)" in body or "print('hi')" in body


def test_fenced_code_without_language():
    md = "```\nplain\n```"
    body = format_for_send(md).chunks[0]
    assert "<pre>plain</pre>" in body


def test_inline_code():
    body = format_for_send("call `os.path.join(a, b)` to join").chunks[0]
    assert "<code>os.path.join(a, b)</code>" in body


def test_inline_code_contents_html_escaped():
    body = format_for_send("type `<div>` then close").chunks[0]
    assert "<code>&lt;div&gt;</code>" in body


def test_heading_becomes_bold_uppercase():
    body = format_for_send("# Hello world\n\nbody").chunks[0]
    assert "<b>HELLO WORLD</b>" in body


def test_bullets_become_dots():
    body = format_for_send("- one\n- two").chunks[0]
    assert "• one" in body
    assert "• two" in body


def test_blockquote_short_uses_blockquote():
    body = format_for_send("> a quote\n> second line").chunks[0]
    assert "<blockquote>" in body and "</blockquote>" in body
    assert "expandable" not in body


def test_blockquote_long_becomes_expandable():
    lines = "\n".join(f"> line {i}" for i in range(12))
    body = format_for_send(lines).chunks[0]
    assert "<blockquote expandable>" in body


def test_first_image_extracted_and_removed_from_body():
    md = "![hero](https://example.com/x.png)\n\nrest of body"
    out = format_for_send(md)
    assert out.photo is not None
    assert out.photo.src == "https://example.com/x.png"
    assert out.photo.alt == "hero"
    body = out.chunks[0]
    assert "rest of body" in body
    assert "![hero]" not in body


def test_local_image_path_detected_as_local():
    out = format_for_send("![](/tmp/x.png)\n\nbody")
    assert out.photo is not None
    assert out.photo.is_local


def test_url_image_detected_as_remote():
    out = format_for_send("![](https://x.com/y.png)\n\nbody")
    assert out.photo is not None
    assert not out.photo.is_local


def test_auto_split_threads_over_limit():
    body = ("paragraph one.\n\n" + "filler\n\n" * 1500).strip()
    out = format_for_send(body)
    assert out.is_threaded
    assert all(len(c) <= TEXT_LIMIT for c in out.chunks)
    assert out.chunks[0].startswith("(1/")


def test_auto_split_can_be_disabled():
    body = "x" * (TEXT_LIMIT + 100)
    out = format_for_send(body, auto_split=False)
    # When disabled we still cap at TEXT_LIMIT chars in a single chunk.
    assert len(out.chunks) == 1
    assert len(out.chunks[0]) <= TEXT_LIMIT


def test_fits_caption_flag_under_limit():
    out = format_for_send("![](https://x.com/y.png)\n\nshort")
    assert out.fits_caption


def test_long_text_with_photo_does_not_fit_caption():
    body = "x " * (CAPTION_LIMIT // 2 + 50)
    out = format_for_send(f"![](https://x.com/y.png)\n\n{body}")
    assert not out.fits_caption


def test_no_unclosed_tags_per_chunk_after_split():
    body = "**heading**\n\n" + ("para body. " * 500 + "\n\n") * 8
    out = format_for_send(body)
    for chunk in out.chunks:
        opens = re.findall(r"<([a-zA-Z]+)[^>]*?(?<!/)>", chunk)
        closes = re.findall(r"</([a-zA-Z]+)\s*>", chunk)
        assert sorted(opens) == sorted(closes), (
            f"chunk has unbalanced tags: opens={opens} closes={closes}"
        )


def test_html_tag_text_passed_through_does_not_break_parser():
    # User typed literal <script>. Output must escape it.
    body = format_for_send("see <script>alert('x')</script>").chunks[0]
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
