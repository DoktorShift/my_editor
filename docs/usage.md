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
| `Ctrl+U` | Underline (stays in local files: Markdown and Nostr have no underline) |
| `Ctrl+Shift+X` on macOS, `Alt+Shift+5` on Windows and Linux | Strikethrough |
| | Inline Code (`Format` menu) |
| `Ctrl+\` | Clear Formatting (a link stays a link) |
| `Ctrl+Alt+0` on macOS, `Ctrl+0` on Windows and Linux | Style: Body |
| `Ctrl+Alt+1` to `Ctrl+Alt+3` on macOS, `Ctrl+1` to `Ctrl+3` on Windows and Linux | Style: Heading 1 to 3 (the same heading again makes it Body) |
| `Ctrl+Shift+7` on macOS, `Ctrl+Shift+8` on Windows and Linux | Bulleted List (again: no list) |
| `Ctrl+Shift+9` on macOS, `Ctrl+Shift+7` on Windows and Linux | Numbered List (again: no list) |
| `Ctrl+]` / `Ctrl+[` | Increase / Decrease Indent of list items |
| `Ctrl+'` on macOS (no shortcut on Windows and Linux) | Quote (again: no quote) |
| `Ctrl+K` | Add Link (Edit Link when the caret is in a link) |
| | `Insert > Divider`: a horizontal rule (`---`) after the paragraph |

The keys follow each platform's own conventions: Apple Notes and Pages on macOS, Google Docs and LibreOffice on Windows and Linux. A command no convention has a key for has none.

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

Cut, Copy, Paste and Select All act on whatever has the focus: the document, the find field, or the PDF reader.

### Find

`Edit > Find`:

| Shortcut | Action |
|---|---|
| `Ctrl+F` | Find (opens the find bar) |
| `Enter` / `Shift+Enter` | Next / previous match, while the find bar is open |
| `Ctrl+G` / `Ctrl+Shift+G` on macOS, `F3` / `Shift+F3` elsewhere | Find Next / Find Previous |
| `Ctrl+E` (macOS) | Use Selection for Find: the selected words become what Find Next looks for |
| `Escape` | Close find bar and return to editor |

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
| `Ctrl+Shift+L` | Toggle line numbers |
| `Ctrl+Shift+T` | Toggle dark / light theme |
| `Ctrl+Shift+H` | Toggle syntax highlighting |

The **`View`** menu holds the appearance options: theme, line numbers, syntax highlighting, background style (lined, dashed, dotted, grid), paper mode, and highlight current line.

`Help > Keyboard Shortcuts` lists every shortcut. It is built from the same command list as the menus (`commands.py`), so the two always agree; the Nostr shortcuts appear there once an account is in use.

### Nostr

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+P` | Publish current document as a short note (kind 1) |
| `Ctrl+Shift+A` | Publish current document as a long-form article (kind 30023) |
| `Ctrl+Shift+M` | Open the Media Library (Blossom) |
| `Ctrl+Shift+I` | Insert image from the Media Library at the cursor |
| `Ctrl+Shift+D` | Open or close the Drafts panel |
| `Ctrl+Shift+S` | Save current document (chooser: local file or private Nostr draft) |

The **`Nostr`** menu also exposes `Drafts…`, `Connect Signer…`, and `Sign Out Active Profile` for managing identities. The avatar chip at the far right of the header is a one-click profile switcher. See the [Nostr guide](nostr.md) for the full workflow.

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

- on a link: Open Link, Edit Link, Copy Link, Remove Link
- Cut / Copy / Paste / Paste and Match Style
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
| `.md` | Markdown: bold, italic, strikethrough, inline code, links, headings, lists, quotes, code blocks, tables and task lists are kept; underline and colors are not (MyEditor asks first when the document has them). It is the same Markdown an article publishes. |
| `.rtf` | Rich Text Format |
| `.Rmd` | R Markdown: YAML frontmatter plus Pandoc markdown with headings, lists, links, strikethrough and inline code; colors and underline use Pandoc spans, images go into a `<name>_media/` folder next to the file |

---

## R Markdown knitting

`.Rmd` tabs get `File > Knit to HTML` (`Ctrl+Shift+K`) and `File > Knit to PDF`.
Knitting saves the tab, then renders the file through `rmarkdown::render` and
opens the result.

If R, pandoc, or the rmarkdown package are missing, the editor offers to install
them from their official sources (CRAN and the pandoc GitHub releases) into a
private app library. Nothing is downloaded without confirmation, and an existing
system R is always preferred over downloading one. Knit to PDF additionally
needs LaTeX, offered as an optional TinyTeX install (about 100 MB).

`File > R Markdown Toolchain…` shows the status of all components at any time.
