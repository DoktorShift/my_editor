# Spell checking

MyEditor checks spelling with each system's own spell checker: the one on macOS (it tells the language of each paragraph by itself), the one in Windows, and Enchant with the system's dictionaries on Linux. Where a system has none, spell checking is unavailable and its commands are dimmed. Everything lives in the `spelling` package; it holds data only. Drawing the underlines, the context menu and Edit > Spelling belong to the editor.

## The pieces

| File | What it does |
|---|---|
| `spelling/service.py` | `SpellChecker`, the app's one link to the system checker, and `DocumentSpelling`, which keeps the misspelled words of one document current as it changes. |
| `spelling/words.py` | Finds the words of Markdown text that are checked, block by block. |
| `spelling/backends.py` | The interface every system checker meets, the null backend, language tags, and `create_backend()`, which picks the one for the platform. |
| `spelling/macos.py`, `windows.py`, `enchant.py` | The three system checkers. |

## Using it in the editor

Make one `SpellChecker` for the app, on the main thread, and one `DocumentSpelling` for each document while Check Spelling While Typing is on:

```python
from spelling import DocumentSpelling, SpellChecker

checker = SpellChecker()                                  # once, at start
spelling = DocumentSpelling(checker, editor.document())   # per document
spelling.misspellingsChanged.connect(underline_blocks)    # (first, last) block numbers
spelling.set_visible_blocks(first, last)                  # on scroll and resize
```

- `spelling.misspellings(block, typing_position=...)` gives the misspelled words of a block, empty until it is checked. Each `Misspelling` has `start` and `length` in UTF-16 units from the start of the block (what `QTextCursor` counts), so the underline runs from `block.position() + m.start` to `block.position() + m.end`. Pass the cursor's position as `typing_position` while the person types there: the word under it is not finished, and macOS and Word do not underline it either.
- Draw underlines as extra selections with `QTextCharFormat.UnderlineStyle.SpellCheckUnderline`, which Qt draws as each platform does (dotted on macOS, wavy elsewhere).
- For the context menu, `spelling.misspelling_at(position)` checks the block right away if needed. Then `checker.suggestions(m.word, m.language)` (best first; show a few), `checker.learn(m.word, m.language)` and `checker.ignore(m.word, m.language)`. macOS calls them Ignore Spelling and Learn Spelling; Word and LibreOffice call them Ignore All and Add to Dictionary.
- Dim Edit > Spelling > Check Spelling While Typing when `checker.is_available()` is False. `checker.availabilityChanged` says when a system checker stops working; its underlines are already gone then.
- `spelling.close()` when spell checking is turned off. A `DocumentSpelling` is a child of its document and goes with it.
- `checker.find_misspellings(text)` checks a short plain text at once (a title, a summary), with offsets from the start of the text.
- `spelling.set_language(tag)` checks a document in another language; `checker.languages()` lists those the system has. There is no language menu yet: macOS tells languages apart by itself, Windows and Linux use the system language.

Only prose documents should get a `DocumentSpelling`, not source code.

## What is checked

A word is letters of any script with their combining marks ("Grüße", "naïve" typed decomposed, Hindi), joined by apostrophes ("don't", "geht’s"), hyphens ("E-Mail-Adresse") and soft hyphens. Left alone: single letters, abbreviations in capitals ("NASA", as macOS does), anything with a digit, an underscore or a dot inside ("mp3", "snake_case", "example.com", "z.B."), web, e-mail and Nostr addresses, `nostr:` references and bare `npub1…` keys, mentions, hashtags, inline code and code blocks (fenced or indented), front matter at the top, HTML tags and comments, link and image addresses (their text is checked), character references, paths and emoji shortcodes. Code and mentions that are formatted as such in the editor (a fixed-pitch font, a link to a `nostr:` address) are left alone too.

Word-by-word checkers (Enchant) also accept an abbreviation listed with its dot ("bzw.") and a compound whose parts are words; only the parts that are not are underlined.

## How it keeps up with typing

Blocks are checked in slices of about 8 milliseconds from the event loop, the visible blocks first, then the rest of the document. An edit marks only the blocks it touched; inserting or removing lines keeps every other block's result. What a line leaves open (a code fence, front matter, an HTML comment) is tracked for the whole document by a cheap pass, so opening a fence turns the lines below into code at once and closing it brings them back. `misspellingsChanged` is not emitted when a check finds the same words again. Measured with the macOS checker: 2,200 paragraphs checked in 1.2 seconds of slices (median slice 8.3 ms), and a keystroke costs under a millisecond to track and to check again.

Everything runs on the thread that made it, without worker threads: the system checkers expect that, Windows COM objects above all. A backend asked from another thread answers neutrally and logs a warning.

## Never in the way

A system checker that fails does not raise into the app. It logs one warning (with the trace, in the log file Help > Show Log Files opens), says why in `backend.problem`, and from then on is unavailable and answers neutrally: every word is right, so a broken checker never underlines the whole document. A missing one (no Enchant, no dictionary) says so once, without a trace.

## Learn and Ignore

- Learn adds the word to the person's own dictionary for good: on macOS the one every app shares, on Windows the person's dictionary, on Linux Enchant's word list in `~/.config/enchant`, shared with the other apps that use Enchant.
- Ignore accepts the word in every document until MyEditor quits (on Windows and Linux, in the language it was ignored in).

Both check again only the blocks where the word was, in every open document.

## The three backends, and why these ways to reach them

- **macOS: NSSpellChecker**, through [rubicon-objc](https://github.com/beeware/rubicon-objc), the BeeWare project's Objective-C bridge: pure Python over ctypes, a 64 KB wheel, about 240 KB installed. PyObjC does the same with compiled bridges: 6.3 MB of wheels and about 27 MB installed, for a dozen calls. With "Automatic by Language" in System Settings (the default) a paragraph is checked the way TextEdit does it, in one call that also tells its language; checking word by word would cost a round trip to the spelling service per word, and with automatic identification would accept a word that is right in any of the person's languages.
- **Windows: the Spell Checking API** (`ISpellChecker`, Windows 8 and later), through ctypes alone: three small COM interfaces, called in the order `spellcheck.h` declares them. comtypes would add about a megabyte, and code it generates at run time, for them. A paragraph is checked in one call; the repeated words Windows also reports ("the the") are left out.
- **Linux: Enchant** (libenchant-2), through ctypes to the system's own library. pyenchant (56 KB) would only wrap the same calls, and its installer hook would bundle the build machine's copy, which then finds no providers or dictionaries.

## Packaging

- `requirements.txt`: `rubicon-objc>=0.5.0; sys_platform == "darwin"`. Nothing for Windows or Linux.
- `packaging/my_editor.spec` names the platform's backend module, and on macOS rubicon's modules and its package metadata (rubicon reads its own version when imported). A frozen test build checks words with the real NSSpellChecker.
- Linux: Enchant is never bundled. The `.deb` recommends `libenchant-2-2`, which brings an English dictionary; the AppImage uses what the system has.

## Tests

- `tests/test_spelling_words.py`, `test_spelling_service.py`, `test_spelling_backends.py`: the scanner, the service and the interface, with stand-in checkers (`tests/spelling_fakes.py`). They run everywhere.
- `tests/test_spelling_macos.py`: the real NSSpellChecker, on macOS.
- `tests/test_spelling_enchant.py`: the real Enchant where libenchant-2 is installed. The Linux test job installs it with English and German dictionaries. To run it from a Mac: `docker run` with `python:3.12-slim`, `apt-get install libenchant-2-2 hunspell-en-us hunspell-de-de` and Qt's runtime libraries, then the spelling tests.
- `tests/test_spelling_windows.py`: the Windows backend's COM calls against stand-in COM objects with real function tables (`tests/spelling_com_fakes.py`), on every platform; the real Windows checker only in the Windows test job.
