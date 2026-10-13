# Keyboard shortcuts, menus, and save formats

## Keyboard shortcuts

On macOS, `Ctrl` in these tables is the Command key (`⌘`) and `Alt` is Option (`⌥`). Where a Mac app uses a different key than Windows and Linux, both are given.

### File

| Shortcut | Action |
|---|---|
| `Ctrl+N` | New tab |
| `Ctrl+O` | Open file |
| `Ctrl+S` | Save (local file, or silent re-save of a draft tab) |
| `Ctrl+Shift+S` | Save As (choose local file or Nostr draft) |
| `Ctrl+P` | Print the current tab (`Cmd+P` on macOS) |
| `Ctrl+Shift+K` | Knit R Markdown to HTML (`.Rmd` tabs) |
| `Ctrl+W` | Close tab |
| `Ctrl+Q` | Quit |

### Formatting

| Shortcut | Action |
|---|---|
| `Ctrl+B` | Bold |
| `Ctrl+I` | Italic |
| `Ctrl+U` | Underline, in HTML documents only (Markdown and Nostr have no underline, so it is dimmed elsewhere) |
| `Ctrl+Shift+X` on macOS, `Alt+Shift+5` on Windows and Linux | Strikethrough |
| | Inline Code (`Format` menu) |
| `Ctrl+\` | Clear Formatting (a link stays a link) |
| `Ctrl+Shift+B` on macOS, `Ctrl+0` on Windows and Linux | Style: Body |
| `Ctrl+Shift+T`, `Ctrl+Shift+H`, `Ctrl+Shift+J` on macOS (Notes' Title, Heading and Subheading), `Ctrl+1` to `Ctrl+3` on Windows and Linux | Style: Heading 1 to 3 (the same heading again makes it Body) |
| `Ctrl+Shift+7` on macOS, `Ctrl+Shift+8` on Windows and Linux | Bulleted List (again: no list) |
| `Ctrl+Shift+9` on macOS, `Ctrl+Shift+7` on Windows and Linux | Numbered List (again: no list) |
| `Ctrl+Shift+L` on macOS, `Ctrl+Shift+9` on Windows and Linux | Checklist: boxes to tick (again, or Bulleted List: plain list items). Click a box to tick it; `Return` starts the next item unticked |
| `Ctrl+Shift+U` on macOS (no shortcut on Windows and Linux, where it types Unicode characters) | Mark as Checked: ticks the checklist items at the caret, or unticks them when all are ticked. Screen readers hear whether an item is checked as the caret enters it |
| `Ctrl+]` / `Ctrl+[` | Increase / Decrease Indent of list items |
| `Ctrl+'` on macOS (no shortcut on Windows and Linux) | Quote (again: no quote) |
| `Ctrl+K` | Add Link (Edit Link when the caret is in a link) |
| | `Insert > Image…`: a picture file chosen in the file dialog, put in at the caret exactly as dropping it there would (one `Undo` takes it back; while a Nostr account is in use, uploading it is offered, as for a drop). Markdown, HTML and new documents keep pictures; elsewhere it is dimmed |
| | `Insert > Divider`: a horizontal rule (`---`) after the paragraph |
| | `Insert > Code Block`: the paragraph becomes a block of code, and a short list at the caret asks for its language (`Escape` keeps it plain text). `Format > Code Language` changes it later |
| | `Insert > Table…`: a table, its size chosen on a grid at the caret; `Format > Table` adds and deletes its rows and columns |
| | `Insert > Footnote`: a footnote's mark at the caret and its note at the end of the document |

Writing (Markdown files, new documents and drafts) is set in your system's own text font at a reading size, in a column of a comfortable line length, with the thin insertion point of a word processor; plain text and code files keep the monospace font across the whole window, and its block caret. `View > Use Monospace Font for Writing` sets all writing in monospace.

The status bar says, quietly, how long the document is: its words and how long it takes to read (225 words a minute, as the long-form Nostr readers count), or how many words are selected. In code and plain text it also shows the language and the line the caret is on. Where a file is saved is in its tab's tooltip, and in the window's title bar as its name.

The **format toolbar** above the document holds the controls used most: Undo and Redo, the paragraph style (Body, Heading 1 to 3), Bold, Italic, Strikethrough, Add Link, Bulleted List, Numbered List, Checklist and Quote, and the **Insert** button (+), which opens the Insert menu itself: the same items in the same order (Nostr's only while an account is in use), dimmed where they don't apply. Undo and Redo say what they take back when you hold the pointer over them ("Undo Typing (⌘Z)" on a Mac, "Undo Typing (Ctrl+Z)" on Windows and Linux), and are dimmed when there is nothing to take. The arrow beside each lists the steps, newest first, by name (up to 15): choose one to go back, or forward, to it in one go, exactly as that many Undos or Redos would. A right-click (Control-click on a Mac) opens the list too, and so does `Down` when the button has the keyboard focus. In the find field or another text field, the list holds the one step the button takes there. Everything else is in the `Format` and `Insert` menus. `View > Show Toolbar` (`Ctrl+Alt+T` on macOS) hides it; a PDF tab has none.

**The format bar.** Select words with the pointer (drag across them, or double- or triple-click) and a small bar shows above them, as on an iPad and in Word: the paragraph style pop-up, Bold, Italic, Strikethrough, Code and Link. They are the same commands as in the Format menu and the toolbar, checked and dimmed the same way. A click on one applies it and keeps the words selected and the bar up, so you can make them bold and then italic. It shows below the words when there is no room above them, and never takes the keyboard from the text. Typing, `Escape` (which does nothing else: the words stay selected), changing or clearing the selection, scrolling, switching tabs or windows, or the words changing all close it. A selection made with the keyboard (`Shift` and the arrows, Select All) shows no bar: the keys of the commands are there for it. It shows where text can be formatted (Markdown and HTML documents, new documents), not in plain-text or code files, nor over a code block, where none of its buttons applies; Settings turns it off.

The keys follow each platform's own conventions: Apple Notes and Pages on macOS, Google Docs and LibreOffice on Windows and Linux. A command no convention has a key for has none. One exception on macOS: Strikethrough and Clear Formatting, which Apple's apps give no key, keep the keys writers know from Google Docs (`Ctrl+Shift+X`, `Ctrl+\`).

**Code blocks.** A code block holds code as it is: set in the monospace font on a tinted box, its lines never wrapped into each other. Make one with `Insert > Code Block`, with `/code` in the slash menu, or by typing ```` ``` ```` and the language (```` ```python ````) and then `Return`. The Style pop-up shows its language ("Code: Python"). Code in a language MyEditor knows (Python, JavaScript, TypeScript, HTML, CSS, JSON, Shell, C, C++, Rust, Go, Java, YAML, TOML and SQL) is colored in the colors code files get, whatever name the block gives it (`py`, `sh`, `c++`). Inside a block:

- `Tab` indents by four spaces and `Shift+Tab` takes up to four away, on every line selected; `Backspace` in the indent goes back four;
- `Return` always starts the next line, as far in as the one before, so blank lines between functions stay in the block; every line the block shows is saved, blank ones too;
- `Cmd+Return` on macOS or `Ctrl+Return` on Windows and Linux, from any line, `Down` on the last line of the document, or `Backspace` at the start of the block takes you out of it, onto a Body paragraph; choosing Body in the Style pop-up makes a line text again.

Words in code have no styles, so Bold, Italic, Inline Code and links are dimmed there, and a paste is plain text. Markdown writes the block fenced, with its language, as Nostr readers expect it.

**Tables.** `Insert > Table…` (or `/table` in the slash menu) opens a small grid at the caret: move the pointer over it, or use the arrow keys, to choose how many columns and rows (the size shows under the grid, "3 × 2" for three columns and two rows), and click or press `Return` to insert the table; `Escape` changes nothing. The first row is the header, in bold. Inside a table:

- `Tab` goes to the next cell, and after the last cell adds a row; `Shift+Tab` goes back;
- `Return` goes to the cell below, and after the last row adds a row (a Markdown cell holds one line, so it has no line breaks);
- `Cmd+Return` on macOS, `Ctrl+Return` on Windows and Linux, leaves the table, into a paragraph below it.

Screen readers hear the row, the column and the column's header as the caret enters a cell. `Format > Table` (also in the context menu inside a table) adds a row above or below, a column before or after, and deletes the row, the column or the whole table. A cell holds words, links and code, but no headings, lists or quotes, so those commands are dimmed in a table. Markdown tables have no merged cells, so MyEditor offers none. A file whose tables align their columns or emphasize words in their header row opens as its Markdown text, so saving it keeps them as written.

**Footnotes.** `Insert > Footnote` (or `/foot` in the slash menu) puts a footnote's mark at the caret (`[^1]`, the next number) and its note at the end of the document (`[^1]: `), where the caret goes so you can write it. Footnotes are the text Markdown writes for them, so you can also type them, with any label (`[^source]`). Marks show raised in the link color and notes quieter; hold the pointer over a mark to read its note. `Cmd`-click (`Ctrl`-click on Windows and Linux) on a mark goes to its note, and on a note back to the mark; `Edit > Go to Note` (`Go to Mark` in a note) and the context menu do the same from the keyboard. The preview draws them as most reading apps do, numbered in the text with the notes listed at the end; some apps (Amethyst, Coracle, Damus) show them as typed, which the preview says while a Nostr account is in use.

Links: `Ctrl+K` opens a small panel under the words with the link's text and address. It accepts web addresses (`https://` is added when you leave it out), email addresses and, while a Nostr account is in use, Nostr links; it says right there why anything else cannot be a link. Pasting a web address over selected words links them. `Ctrl`-click (`Cmd`-click on macOS) opens a link: web pages in your browser, Nostr links through njump.me. Typing right after a link is not part of it.

Return at the end of a heading starts a Body paragraph; Backspace at the start of a heading makes it Body. Strikethrough, inline code and the paragraph styles are Markdown, so they are offered in documents that can hold Markdown structure (Markdown, HTML and new documents), not in plain-text and code files. Text colors are in `Format > Color`.

### Edit

The `Edit` menu has what every Mac app has there, in the same order:

| Shortcut | Action |
|---|---|
| `Ctrl+Z` | Undo |
| `Ctrl+Shift+Z` (`Ctrl+Y` on Windows) | Redo |
| `Ctrl+X` / `Ctrl+C` / `Ctrl+V` | Cut / Copy / Paste |
| `Ctrl+Alt+Shift+V` (`Ctrl+Shift+V` on Windows and Linux) | Paste and Match Style: paste as plain text in the style around it |
| `Ctrl+A` | Select All |
| `Option+Delete` / `Fn+Option+Delete` on macOS (`Ctrl+Backspace` / `Ctrl+Delete` on Windows and Linux) | Delete the word before / after the caret, in lists, quotes, code and tables too; a mention goes whole. On a Mac a word is what the Mac's own text fields take for one (punctuation, Japanese and Chinese without spaces, emoji) |
| `Cmd+Delete` / `Fn+Cmd+Delete` on macOS | Delete to the start / end of the line |

Cut, Copy, Paste and Select All act on whatever has the focus: the document, the find field, or the PDF reader.

**Undo.** One Undo takes back a whole run of typing, as in TextEdit, Pages and Word: what you typed one character after another, new paragraphs among it (in a list, a checklist, a code block or after a heading too), and characters composed with a dead key or an input method, or typed with Option or AltGr. A run ends when you pause for two seconds, move the caret or the selection, format, paste, use any command or Undo, or save. A run of Backspace, or of Delete, is one step of its own; typing after it, or deleting the other way, starts a new one. Every step has a name. On a Mac, Edit > Undo and Redo say what they take back ("Undo Typing", "Undo Paste", "Undo Bold", "Undo Replace All", "Undo Correction", a slash command by its name, "Undo Drag" for text or pictures dragged in), as the Mac's own apps do. A command is named by its title ("Undo Increase Indent", also for `Tab` in a list, "Undo Add Row Above", "Undo Remove Link", "Undo Code Language", "Undo Delete"), what an Insert command puts in by what it is ("Undo Image", "Undo Audio or Video", "Undo Note or Article"), and a checklist box clicked "Undo Checklist", as Mark as Checked is. What keys do on their own is typing: `Tab` in a code block, `Backspace` or `Delete` taking a divider, a mention or audio and video away, and the row `Tab` or `Return` adds at the end of a table. What a Markdown Shortcut makes is named as the command that makes the same: "Undo Paragraph Style" for `# `, "Undo List" for `- ` or `1. `, "Undo Quote", "Undo Checklist", "Undo Bold" or "Undo Italic" for `**words**` and `*words*`, "Undo Divider" and "Undo Code Block". In German the Mac's Edit menu says "Tippen widerrufen", "Einsetzen widerrufen", "Ziehen widerrufen" and so on; on Windows and Linux they say Undo and Redo, as Notepad and GNOME's Text Editor do.

**What a paste brings in.** In a document that can hold Markdown structure, a paste keeps what Markdown can say and nothing else: headings, bold, italic, strikethrough, code, links, lists and checklists, quotes, code blocks (with their language, from Stack Overflow, GitHub or VS Code), tables, dividers and pictures that are on the web. Fonts, sizes and colors stay behind, so text from Google Docs, Word, Pages or a web page looks like the rest of your document. A link that would not work for your readers (a path on the website it came from, a script) keeps its words without the link. A picture that is not on the web (inside the clipboard or on a disk) cannot come along with the text; the status bar says so, and you can copy the picture on its own and paste it. What a page shows as text stays text: a page that shows `<div>`, `**stars**` or `[x](y)` pastes those characters, not a tag, bold words or a link. Plain text is read as Markdown when it clearly is Markdown (a heading and a list, a table), and a tag in it stays text; other text arrives as it is. Text copied in MyEditor pastes back exactly as it was, every word and every structure, whether you paste it, cut and paste it or drag it.

A paste goes in the way typing it would: inside a sentence the pasted words become part of it, into an empty line the first pasted paragraph keeps its style (a heading stays a heading), code and dividers get lines of their own, pasted list items join the list they land next to, and a table cell gets one line. One `Undo` takes the whole paste back. Inside a code block, and in plain-text and code files, a paste is always plain text. Image files copied in the Finder or Explorer are added like dropped ones. A plain-text or code file keeps its text only, so there a picture file dropped or pasted goes in as its path, as in TextEdit's plain text, and a picture on the clipboard is not pasted: nothing goes in that saving would leave out. Dropping text on the document works the same way as pasting it.

**Paste and Match Style** pastes the clipboard's plain text in the style of the text where it goes: inside a heading it is heading text, inside bold words it is bold.

**Markdown Shortcuts** (`Edit > Substitutions > Markdown Shortcuts`, off until you turn it on, and remembered) turn Markdown into what it means as you type it, in documents that can hold Markdown structure:

| Type | You get |
|---|---|
| `#`, `##` or `###` and a space, at the start of a paragraph | Heading 1, 2 or 3 |
| `-`, `*` or `+` and a space | A bulleted list |
| `1.` or `1)` and a space | A numbered list, starting at the number you typed (right below a list, it goes on from it) |
| `>` and a space | A quote |
| `[ ]` or `[x]` and a space, at the start of a paragraph or a list item | A checklist item, ticked for `[x]` |
| `---`, `***` or `___`, then Return | A divider |
| ```` ``` ```` or `~~~`, with a language or none (```` ```python ````), then Return | A code block in that language |
| `**words**` | Bold |
| `*words*`, or `_words_` and then a space or a punctuation mark | Italic (`snake_case` and `_private_name` stay as typed) |
| `~~words~~` | Strikethrough |
| `` `words` `` | Code |

`Undo` right after a change brings back exactly what you typed, and what you type next is in the style you were typing in before the marks. Nothing changes inside a code block or code words, inside code you are still typing (after an opening `` ` ``), across a link, after a backslash (`\*`), or in plain-text and code files; marks that are themselves code (`` `#` ``) make no heading, list or divider. The words between two marks never hold the mark itself, so an earlier `**` you kept as typed stays as it is.

**Spelling.** MyEditor checks your spelling with your system's own spell checker, the one your other apps use: misspelled words get the system's underline (dotted on macOS, wavy elsewhere) once you have finished typing them. Right-click a misspelled word for what it might be (choose one to put it in place; `Undo` takes it back), `Ignore Spelling` (accepted everywhere until MyEditor quits) and `Learn Spelling` (added to your own dictionary for good); on Windows and Linux they are called `Ignore All` and `Add to Dictionary`. Right-click a word you learned to take it back: `Unlearn Spelling` (`Remove from Dictionary` on Windows and Linux; Windows 10 and later, for words learned since MyEditor started). `Edit > Spelling > Check Spelling While Typing` turns it off and on, and is remembered; it is dimmed where the system has no spell checker. macOS tells the language of each paragraph by itself; on Windows and Linux, `Edit > Spelling > Language` chooses the language (your system's at first), also remembered. Documents and Markdown and plain-text files are checked; code files, code blocks and inline code, web and Nostr addresses, hashtags and mentions are not.

### Find

`Edit > Find`:

| Shortcut | Action |
|---|---|
| `Ctrl+F` | Find (opens the find bar) |
| `Ctrl+Alt+F` on macOS, `Ctrl+H` on Windows and Linux | Find and Replace: the find bar with a Replace row (Replace, Replace All) |
| `Enter` / `Shift+Enter` | Next / previous match, while the find bar is open (`Enter` in the Replace field replaces) |
| `Ctrl+G` / `Ctrl+Shift+G` on macOS, `F3` / `Shift+F3` elsewhere | Find Next / Find Previous |
| `Ctrl+E` (macOS) | Use Selection for Find: the selected words become what Find Next looks for |
| `Escape` | Close find bar and return to editor |

The find bar's Options menu offers Match Case and Whole Words. A replacement takes the style of the text it replaces (a bold word stays bold, a link stays the same link), and Replace All is one step to undo.

### Editor

| Shortcut | Action |
|---|---|
| `Tab` | In a list: nest the item one level deeper. At the start of a paragraph: start a bulleted list |
| `Shift+Tab` | In a list: one level up, and out of the list from the top level |
| `Enter` | New paragraph (a new item in a list) |
| `Enter` (on an empty item) | One level up, and out of the list from the top level |
| `Backspace` (at an item's start) | The same as `Shift+Tab` |
| `Enter` (on an empty quoted line), `Backspace` (at a quote's start) | One level of quote less |
| `Backspace` (just below a divider) | Removes the divider |

Lists are real lists, written to Markdown as `- item` and `1. item`, nested by four spaces. In plain-text files (`.txt`, code, R Markdown), `Tab` types a bullet as text (`    • item`) instead, as before.

**The slash menu.** Type `/` at the start of a word to insert structure without leaving the keyboard: a short list opens under the caret with the paragraph styles, the lists, the blocks (quote, divider) and what can be inserted (a link, a picture, and while a Nostr account is in use, a file from your media library). It holds the same commands as the menus, with their keys. Type a few letters to narrow it (`/he` for the headings, `/list`, `/rule`), move with `Up` and `Down`, and press `Return` or `Tab` to choose: the `/` and your letters go, and the command acts where they were. One `Undo` brings them back. `Escape` closes the list and keeps what you typed; so does moving the caret away, or letters that match nothing. A slash in a sentence stays a slash: a space right after it ("either / or") closes the list, and `Return` right after a bare `/` ends the paragraph as always, unless you moved to an item with `Up` or `Down`. The pointer chooses the item it is over, as in every menu, and screen readers hear the list open, each item you move to ("Heading 2, 3 of 9") and what was done. It opens only where a document can hold structure, and never in a code block or a table.

**Write, Preview and Split.** A document that holds Markdown can be shown as you write it, as it reads, or both side by side: `View > Write`, `View > Preview` and `View > Split`, also the switch at the right end of the format toolbar. The preview is drawn from the document's Markdown, the same way the publish preview draws it, and follows your typing. While a Nostr account is in use it shows what will be published, as an article (cover, title, summary, byline, the text and hashtags) or as a note (`Preview As`), and says what your readers will not see the way it looks in the editor: pictures not uploaded yet, a first heading that repeats the title. Split needs room for two readable columns; in a narrower window the side you used last shows, the switch flips between writing and reading, and the two come back side by side when the window is wide enough again. Your choice is remembered. In Preview, nothing edits the hidden text (not even Undo; Copy and Select All work on the preview), and Find works in Write and Split. A Markdown file that opens as its text (one with raw HTML in it, for example) has its preview too, drawn from that text. Plain-text, code and R Markdown files show only their text.

The **`View`** menu holds the appearance options: `View > Appearance` (Automatic follows your computer's light or dark appearance as it changes; Light and Dark stay as chosen), line numbers, syntax highlighting, background style (lined, dashed, dotted, grid), paper mode, and highlight current line. Nothing around the document repeats a menu: the window shows the format toolbar, the tabs and the text.

**Settings** (`MyEditor > Settings…` on a Mac, `Cmd+,`; `Edit > Settings…` on Windows and Linux, `Ctrl+,`) gathers the choices you make once: the appearance, the language, the writing font, whether the format bar shows over text you select (`Show the format bar when selecting text`, on at first), and, while a Nostr account is in use, what happens to a pasted image (ask every time, upload it, or keep it local). Each choice applies at once, and each is also in its menu.

`Help > Keyboard Shortcuts` lists every shortcut as it is on the computer in use: on a Mac in Apple's symbols (`⇧⌘S`), elsewhere with your keyboard's key names (`Strg+Umschalt+S` on a German one). It is built from the same command list as the menus (`commands.py`), so the two always agree; the Nostr shortcuts appear there once an account is in use.

### Nostr

The Nostr commands have no shortcuts of their own: no platform has a convention for them. They are in the `Nostr` menu (and in Help > Keyboard Shortcuts' search): Publish as Note…, Publish as Article…, Media Library…, Insert Image from Media Library…, Insert Mention…, Insert Note or Article…, Insert Audio or Video…, Article Details, Drafts. In the Insert menu, which they join while an account is in use, they go without the verb its name already says, as in Pages: Mention…, Note or Article…, Image from Media Library…, Audio or Video…; Add Link… is Link… there too. Two answer a key of the system's or of the app it follows: Refresh Drafts the system's own Refresh key, and Article Details Option-Command-I on a Mac, the key Pages, Numbers and Keynote give their inspector.

While a Nostr account is in use, the editor offers Nostr where you write, and nowhere else:

- **Insert > Note or Article…** (also in the slash menu): paste a link to a note or an article (a `nostr:` link, a bare `nevent1…` or `naddr1…`, or the address a reader shows it at, such as njump.me, primal.net or yakihonne.com) into the small panel at the caret. Why a link can't be used is said under the field. It goes into the text as written, in a paragraph of its own, where readers show it as a card.
- **Insert > Audio or Video…** (also in the slash menu), or an audio or video file dropped on the window or pasted: the file goes into the text as an object of its own, as a picture does, shown as a card with a waveform or a strip of film and its name, and to your media servers. The card says how its upload is going ("Uploading… 42 %"); finishing changes nothing you wrote and adds no step to Undo. The card plays nothing. It stays a paragraph of its own wherever it goes, pasted or dragged into a sentence too: typing or Return with the caret before it opens a paragraph above it, after it one below, and Backspace from the paragraph below removes it whole (Undo brings it back). Copied into another app, it is its address (its name while it uploads), in every format; a picture not uploaded yet goes as its bytes. Typing at it, the paragraph made for the words is one step of Undo with them. A large file is read in the background, so the window keeps answering. Published or saved as Markdown, it is its address on a line of its own, where readers show a player (the preview shows "▶ Audio: name"); an HTML file, a PDF and a printed page link to it, and R Markdown links to it, or to a copy beside the document while it uploads. Until it is on a media server, nothing is published or saved as a draft (asking to publish starts a failed upload again), and a Markdown, HTML or PDF file saved meanwhile is written without it: the status bar says so, and when the upload is done the document is edited again, so saving it puts the file in (as Pages does when something you inserted finishes loading). The same file twice is one upload. A file larger than your media servers take is refused at once, saying the limit. Closing the document while it uploads asks first (Close Anyway: the file still reaches your media library); so does quitting, which stops the upload. With unsaved changes, that is said in the one question that asks whether to save them.
- **View > Article Details** (Option-Command-I on a Mac): an inspector beside the document, as Pages has, for the article in the tab in front: its title, summary, cover image (an address, or Choose or Upload… from the Media Library), hashtags (separated by commas), co-authors, and under Advanced its slug and when it first went out. What you type there is the article's at once; the publish window shows and changes the same details. A saved draft's or a published article's slug is shown, not edited: another slug would make another article. **Add Co-Author…** chooses a person the way a mention does; **Share zaps equally with co-authors** splits zaps to the article evenly between you and them. A co-author whose profile has no lightning address can't receive zaps and gets no share; a line under the option says so. You are named the article's first author, before your co-authors. A note has no article details, and says so.

A Markdown file keeps its article's details in its front matter, the block at its top that Jekyll, Hugo, Obsidian and iA Writer use: YAML between `---` lines (or closed by `...`, as Pandoc writes it), or TOML between `+++` lines, as Hugo's new posts have it, read and written in TOML. The details are `title`, `summary` (or `description`), `image` (or `cover`), `tags`, `slug`, `date`, and `coauthors` and `zap_split`. They show in Article Details, never in the text, and are never published as text. The front matter as written is what counts: saved without a change to the details, it stays byte for byte (comments, quoting, order, nested entries like Hugo's `cover:` and all); a changed detail rewrites its own line only, where it was, in the file's own style: the spaces after the key, the kind of quotes, a comment after the value, a block list or an inline list as it was, and a date in the form and time zone it was written in. Tags keep their case, and a tag holding a comma stays one tag (Article Details shows it in double quotes). Keys are read as their tools read them: Hugo's in any case ("Title:"), Zola's tags under `[taxonomies]`, Jekyll's space-separated `tags: ruby jekyll`; a key left empty, `~` or `null` is no value, and a `date:` that is no date (Eleventy's "Last Modified") is kept as written. A detail the file writes in a way MyEditor can't read safely (Hugo's `cover:` with `image` under it, an anchor) stays as written: Article Details shows it read-only, saying it is kept as written in the file. MyEditor writes front matter only when the article has details or the file had front matter. A `---` block without any `key:` line is a divider, not front matter.

| Shortcut | Action |
|---|---|
| `Ctrl+R` (Command-R) on macOS, `F5` elsewhere | Refresh the Drafts panel's view (Nostr > Refresh Drafts, named for the view shown) |
| `Ctrl+Shift+S` | Save current document (chooser: local file or private Nostr draft) |

The **`Nostr`** menu also holds `Drafts`, `Refresh Drafts`, `Draft Relays…`, the ways to an account (`Create Account…`, `Connect Signer App…`, `Restore Account…`) and the account in use (`Edit Profile…`, `Back Up Account…`, `Sign Out`). Until an account is in use, the commands that act for one are dimmed. While an account is in use, its avatar sits at the right end of the tab row: a one-click account switcher. See the [Nostr guide](nostr.md) for the full workflow.

---

## PDF reading

Opening a `.pdf` (via `Ctrl+O`, drag and drop, double-click from the file
manager, or Recent Files) shows it in the built-in read-only viewer: a slim
toolbar with a contents toggle, a page box, and zoom controls, the document,
and nothing else. Password-protected files prompt for their password. The
viewer remembers the page and zoom you left off at per file, and if the PDF is
regenerated on disk (a LaTeX build, a re-export) it reloads in place at the
same position.

Drag over text to select it and copy with `Ctrl+C`; the selection snaps to
characters like a text editor and pastes cleanly into any tab. Links work the
way you expect: web links open in your browser, internal references (table of
contents entries, "see section 4.2") jump to their page, and the cursor shows
a pointing hand over both.

Navigation follows the muscle memory of readers like SumatraPDF:

| Shortcut | Action |
|---|---|
| `Ctrl+F` | Find in PDF (`Enter` / `Shift+Enter` step through matches) |
| `Ctrl+C` | Copy selected text |
| `Esc` | Clear the selection |
| `Space` / `Shift+Space` | Next / previous screenful |
| `j` / `k` | Scroll down / up |
| `n` / `p` | Next / previous page |
| `g` | Go to page (focuses the toolbar page box) |
| `PageDown` / `PageUp` | Scroll page-wise |
| `Home` / `End` | First / last page |
| `Ctrl+=` / `Ctrl+-` / `Ctrl+wheel` | Zoom in / out |
| `Ctrl+0` | Fit page width |
| `Ctrl+1` | Actual size |
| `Ctrl+2` | Fit whole page |
| `F12` | Toggle the table of contents |
| `F11` | Full screen (whole window; `Ctrl+Cmd+F` on macOS) |

Type a page number into the toolbar's page box and press `Enter` to jump
straight there. **Fit Width** and **Fit Page** in the toolbar switch scaling
modes; searching starts from the page you are reading, not from page one.
Documents with an embedded outline get a **Contents** sidebar (toolbar button
or `F12`); the button stays greyed out when the PDF has no outline.

---

## Right-click menu

Right-clicking in the editor opens a context menu with the same commands as the menus:

- on a misspelled word: what it might be, Ignore Spelling, Learn Spelling (Ignore All and Add to Dictionary on Windows and Linux)
- on a footnote's mark or a note's label: Go to Note / Go to Mark
- on a link: Open Link, Edit Link, Copy Link, Remove Link
- Cut / Copy / Paste / Paste and Match Style
- in a table: Table (add rows and columns, delete them or the table)
- Bold / Italic / Underline / Strikethrough / Inline Code
- **Color**: one of six text colors (Red, Green, Orange, Yellow, Blue, Purple), or **Remove Color**
- **Clear Formatting**: every style and color at once; links stay links

Right-clicking a **tab** opens a context menu with:

- **Rename**: rename the file on disk and update the tab (greyed out for unsaved files)
- **Delete File**: move the file to system trash with a confirmation dialog (greyed out for unsaved files)

---

## Printing

`File > Print…` (`Ctrl+P`, `Cmd+P` on macOS) prints the current tab through the
system's print dialog, where you pick the printer, the pages and the number of
copies. A note prints exactly as the `.pdf` export lays it out: the paper size,
orientation and margins from `File > Page Setup…`, images scaled to the page,
and a "Page N of M" footer. A PDF tab prints its own pages, each scaled to fit.
The printer you chose and its options stay selected until you quit.

macOS shows a preview inside its print dialog. On Windows and Linux,
`File > Print Preview…` shows the pages before they print.

---

## Save formats

| Format | Notes |
|---|---|
| `.txt` | Plain text, no formatting |
| `.html` | Clean semantic HTML5: headings, lists (bulleted, numbered, checklists), links, strikethrough and inline code are kept, and bullets typed as text become real lists; images are embedded as data URIs so the single file is shareable; adapts to the reader's light/dark mode. Opened again, its lists are real lists |
| `.pdf` | Native PDF export: document metadata, locale-aware page size (A4/Letter), page-number footer, images scaled to the printable width; configure via `File > Page Setup…` |
| `.md` | Markdown: bold, italic, strikethrough, inline code, links, headings, lists, quotes, code blocks, tables, task lists and footnotes are kept; underline and colors are not (MyEditor asks first when the document has them). It is the same Markdown an article publishes. |
| `.rtf` | Rich Text Format |
| `.Rmd` | R Markdown: YAML frontmatter plus Pandoc markdown with headings, lists, links, strikethrough and inline code; colors and underline use Pandoc spans, images go into a `<name>_media/` folder next to the file |

## Backups

Files are saved when you choose Save. What you type is backed up as you go (two seconds after you stop typing, and every fifteen seconds while you type), in `~/.cache/my_editor/backups/`, and offered back exactly as it was after a crash. The status bar says nothing about it while it works. When a document's backup cannot be written (a full or read-only disk), it says "Couldn't keep a backup of this document." with **Try Again**, until a backup is written again; a document too large to back up says so instead.

---

## R Markdown knitting

`.Rmd` tabs get `File > R Markdown > Knit to HTML` (`Ctrl+Shift+K`) and `File > R Markdown > Knit to PDF`.
Knitting saves the tab, then renders the file through `rmarkdown::render` and
opens the result.

If R, pandoc, or the rmarkdown package are missing, the editor offers to install
them from their official sources (CRAN and the pandoc GitHub releases) into a
private app library. Nothing is downloaded without confirmation, and an existing
system R is always preferred over downloading one. Knit to PDF additionally
needs LaTeX, offered as an optional TinyTeX install (about 100 MB).

`File > R Markdown > R Markdown Toolchain…` shows the status of all components at any time.
