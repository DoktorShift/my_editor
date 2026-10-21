# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing imported Markdown (``nostr.rss.markdown``)."""

from __future__ import annotations

import unittest

from nostr.rss.markdown import (
    code_block_spans,
    code_spans,
    escape,
    image_destinations,
    protect,
    restore,
    unescape,
    without_code_blocks,
)


def texts(markdown, spans):
    return [markdown[start:end] for start, end in spans]


class CodeTests(unittest.TestCase):
    def test_fences_and_inline_code(self) -> None:
        md = "a `x` b\n\n```py\ncode\n\nmore\n```\n\n~~~\nt\n~~~\nc ``y ` z`` d"
        self.assertEqual(texts(md, code_spans(md)),
                         ["`x`", "```py\ncode\n\nmore\n```\n", "~~~\nt\n~~~\n", "``y ` z``"])

    def test_an_unclosed_fence_runs_to_the_end(self) -> None:
        md = "Intro\n\n```\nnever closed"
        self.assertEqual(texts(md, code_block_spans(md)), ["```\nnever closed"])

    def test_inline_code_does_not_cross_a_paragraph(self) -> None:
        self.assertEqual(code_spans("a `x\n\ny` b"), [])

    def test_a_fence_in_a_list_item_or_a_component_is_code(self) -> None:
        for md in ("1. Install:\n\n    ```js\n    import x\n\n    <div>hi</div>\n    ```\n",
                   "<Tabs>\n    ```js\n    x\n    ```\n</Tabs>"):
            self.assertEqual(len(code_block_spans(md)), 1, md)

    def test_prose_keeps_inline_code_without_blocks(self) -> None:
        self.assertEqual(without_code_blocks("A `b` c.\n\n```\nx\n```\n").strip(), "A `b` c.")

    def test_a_placeholder_nobody_set_aside_stays_as_it_is(self) -> None:
        self.assertEqual(restore("a \ue0007\ue001 b", []), "a \ue0007\ue001 b")

    def test_protected_code_comes_back_as_it_was(self) -> None:
        md = "x `[[a]]` y\n\n```\n[[b]]\n```\n"
        kept = []
        text = protect(md, code_spans(md), kept)
        self.assertNotIn("[[", text)
        self.assertEqual(restore(text, kept), md)


class EscapeTests(unittest.TestCase):
    def test_text_reads_back_as_written(self) -> None:
        for text in ("a *b* _c_ [d] `e` <f> ~g~", "path\\to", "2 * 3"):
            self.assertEqual(unescape(escape(text)), text)


class ImageTests(unittest.TestCase):
    def test_two_images_sharing_a_reference_name_its_address_once(self) -> None:
        md = "![a][r] ![b][r]\n\n[r]: https://x.example/a.png"
        self.assertEqual([url for _s, _e, url in image_destinations(md)],
                         ["https://x.example/a.png"])


if __name__ == "__main__":
    unittest.main()
