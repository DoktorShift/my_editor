# Nostr in MyEditor

MyEditor doubles as a Nostr publishing client. Write in the editor, then publish
to Nostr as a short note or a long-form article, signed by your own NIP-46 signer
so your private key never enters the app, or by a key MyEditor keeps for you on
this computer. You can create a new account in the app, back it up and restore
it. You also get private encrypted drafts that sync across your devices, a media
library on your own Blossom servers, imports of posts from websites, files and
links, and EINUNDZWANZIG membership.

- [Publishing](#publishing)
- [Private encrypted drafts](#private-encrypted-drafts)
- [Media library (Blossom)](#media-library-blossom)
- [Imports: follow websites, import files and links](#imports-follow-websites-import-files-and-links)
- [Joining EINUNDZWANZIG in the app](#joining-einundzwanzig-in-the-app)

---

## Publishing

Write in the editor, hit publish, approve on your phone. The document goes out as a Nostr event. With a signer, your private key stays in it (Amber, nsec.app, nsec.bunker, etc.) and never enters this app. An account kept on this computer signs in MyEditor itself, without a prompt (see [Accounts](#accounts-create-restore-back-up)).

### What you can publish

- **Short notes** (kind 1, `Nostr > Publish as Note…`): the editor's content as plain text, since that is what apps show for a note. Bold and italic are left out; list markers and link addresses stay. Publish as Note is a sheet on the document's window with the note's text in a box, where it can still be changed before it goes out, as a share sheet composes what it shares: the document is not changed by it. A mention in the box shows as "@Name", whether it was in the text, pasted as a `nostr:npub1…` link, or chosen by typing `@` at the start of a word; `Backspace` takes it away whole, and it goes out (and is copied) as the person's `nostr:` link. **People** under the box shows, by name, everyone the note will tag.
- **Long-form articles** (NIP-23 kind 30023, `Nostr > Publish as Article…`): the body is the same Markdown a `.md` save writes (`markdown_writer.py`): bold, italic, strikethrough, inline code, links, headings, lists, quotes, code blocks, tables and task lists reach Nostr. Underline and colors have no Markdown and stay in local files. Lines typed one under the other become paragraphs of their own (NIP-23 asks for no hard line breaks), and Markdown typed by hand (`## Heading`, `[label](url)`, a code fence) is published as typed. Footnotes are kept as the text Markdown writes for them (`[^1]` and `[^1]: the note`) and published as such; the preview draws them as most reading apps do and says which apps show them as typed. An article draft or `.md` file that holds something the editor cannot show (raw HTML, an image without alt text, a footnote of two paragraphs, a tab in code, code inside a list item, aligned table columns) opens as its Markdown text and is published exactly as written, so nothing is lost. Title, summary, link name (the `d`-tag identifier), cover image, and hashtags come with it. Re-publishing with the same link name replaces the previous version, so an article stays addressable as one `naddr1…` link across edits.
- **Publish as Article** is a sheet on the document's window (on a Mac it slides down from the title bar), never taller than that window: what does not fit scrolls. What goes out is the document's text as it is written; there is no second copy of it to edit. A card named Preview shows the article as readers see it in their lists (cover, title, summary, you and the reading time) and follows the fields beside it: **Title** (the article's own, else its first heading, else its first line in plain words, a mention as the person's name and never their key: `article_details.default_title`), **Summary**, **Hashtags** (tags, as in Article Details: Return or a comma makes one, × or `Backspace` removes one), **Cover image** (a well with **Choose…** from the Media Library, **Remove**, or a picture file dropped on it, uploaded without where and when it was taken) and **People**: everyone the article will tag, read from the event the publisher builds, by name and avatar, labelled as mentioned in the text, quoted (the author of a note or article it quotes) or co-author, with **Add Co-Author…** and × as in Article Details. **Show Details** holds the article's **link name** (made from the title until you type one, at most 48 characters, never a key, with how it ends the article's address under it: "…/my-article"), the cover's web address, and when the article first went out. An article that has its link name already keeps it as the title changes, exactly as it is written: another would make another article. A file's article starts with a link name made of the file's name; when you choose Publish, and not before, MyEditor asks the relays you publish to whether it is out under the file's own name (versions up to 3.3 published files so, to those relays), also when its front matter has a date, and if it is, publishes it under that name; when the relays give no clear answer, it asks you, and you can publish under the file's own name exactly as it is, or the new one. What you change in the sheet goes back to the document's details when it closes, published or not; its link name and first publication date only once it is published.
- **The date an article first went out** stays with it through every edit: readers date the article by it. MyEditor takes it from the article's draft, or from what this computer remembers of its own publications (`~/.config/my_editor/article_first_published.json`), or asks the relays you publish to, the ones you read from and the indexers. An article none of them holds is new and dated today, as long as at least one of the relays you publish to (and an indexer) answered: one relay that is down does not hold up a new article. When none of the relays you publish to answers, nobody can tell whether the article went out before, so MyEditor asks you: **Try Again**, **Publish as New** (today's date) or **Cancel**. A draft saved after publishing keeps the date, so another device that opens the draft keeps it too.

Both flows display a `Published from MyEditor` client tag so readers that honour NIP-89 can show which app produced the note.

### Connecting a signer app

`Nostr → Connect Signer App…` pairs a signer app (Amber, nsec.app, …) in one of two ways:

1. **Scan Code** (shown first): in the signer app, choose to connect an app (in Amber: *Connect app*, the scan icon) and scan the code, then approve the request. The code works for 90 seconds; **New Code** makes another. **Copy Link** gives the same link as text, for a signer app on this computer. (Behind the code is a NIP-46 `nostrconnect://` link; it is always shown dark on white, which scanners read best.)
2. **Paste Link**: some signer apps show a link starting with `bunker://` instead; paste it and choose **Connect**. A link written by hand from a signer's key, servers and secret works the same way.

Without a signer app, the same window offers **Create an Account** and **Restore from Backup**.

Connected profiles are saved to `~/.config/my_editor/nostr_profiles.json` with `chmod 600`. The local channel keypair lives there too; your real `nsec` does not.

### Accounts: create, restore, back up

For people new to Nostr, `Nostr → Create Account…` makes a new account in a few steps:

1. **Name.** Optional; it becomes the profile's name.
2. **Backup.** Right after the key exists, because nobody can reset it: **Save Backup File…** writes a small text file with the public key and the private key protected by a password (NIP-49 `ncryptsec`, which other Nostr apps import too). Saving it unprotected is a second, explicit choice; skipping the backup asks first.
3. **Where the key lives.** *On this computer*: MyEditor keeps the key and signs with it. *In a signer app*: a guided import into Amber on Android, then pairing, after which the key on this computer is no longer needed.
4. **Setup.** MyEditor publishes a starter relay list (NIP-65), then the profile, and shows each step. If that can't finish now, **Finish Later** keeps the account and MyEditor completes the setup the next time the account is used, creating only what is still missing.

`Nostr → Restore Account…` opens a backup file (or a pasted key), asks for its password, and brings the account back with its existing relay list and profile; nothing on the network is replaced. `Nostr → Back Up Account…` saves a new backup file at any time for an account kept on this computer.

The private key is never shown on screen. It can be copied (marked as a secret for clipboard managers, and cleared again after a minute) or saved in a backup file. The one exception is the code Amber scans, shown only on request.

### Multiple identities

While an account is in use, its avatar sits at the right end of the tab row, as a profile switcher. Add as many profiles as you want, switch with a single click, and the change takes effect on the next publish. Switching mid-write is fine; the active profile is finalised only when you actually press **Publish** inside the publish sheet (which also has its own account menu).

Avatars and display names are pulled from each profile's kind 0 metadata in the background; until they land the chip shows colored initials.

### Tagging people (mentions)

While a Nostr account is in use, type `@` at the start of a word in a document (or choose `Insert > Mention…`, also in the Nostr menu and the slash menu) to mention someone: a short list opens at the caret with the people you follow first ("Following"), then everyone a search finds. Type a name or a Nostr address to narrow it, and choose one: "@Name" takes the place of the `@`, as a link to the person; `Escape` keeps the `@`. `Backspace` right after a mention takes it away whole. A mention is published as the person's Nostr link (`nostr:npub1…`, or `nostr:nprofile1…` with a relay where they can be found), which every reader shows as their name, and gets a `p` tag so they hear about it (NIP-27); opened again, it shows the name. A person's Nostr address shows in the list only once their domain has confirmed it (`nostr/nip05_check.py`): anyone can write any address into a profile. An `@` inside a word (an email address) or in code stays an `@`.

The people a note or an article will tag are shown in the publish sheets under **People**, by name, from the event the publisher builds: those the text mentions, the authors of what it quotes, and an article's co-authors. Mentions are made in the text itself; there is no second list of them. Inline `nostr:n…` links you paste yourself are tagged too, each person once.

A note or article that quotes another one (a `nostr:` link to a note or an article in its text) carries a `q` tag for each (NIP-18: the note's id, a relay hint and its author; the article's address), and a `p` tag for each quoted author, once, also when mentioned. A link inside code is text and quotes nothing. `Insert > Note or Article…` puts such a link into the text as a paragraph of its own (NIP-27), where readers show the note or article as a card.

### Article details, co-authors and zaps

An article's details (title, summary, cover, hashtags, link name, the date it first went out) are one model for each tab (`nostr/article_details.py`), shown and changed in **Article Details** (`View > Article Details`) and in the Publish as Article sheet alike; both add and remove hashtags, the cover and co-authors the same way (`nostr/ui/tag_field.py`, `cover_field.py`, `coauthors.py`). A draft holds them in its inner event; a Markdown file in its front matter. A co-author is tagged `["p", <pubkey>, <relay>, "author"]`, the marker standup writes and reads back for "this person wrote it"; a co-author also mentioned in the text keeps one `p` tag, the one with the marker. With co-authors, the signer is tagged first the same way, since standup takes the first `author` tag as the writer. A co-author tag is credit for writing the article and nothing more (the owner's decision, "Credit, zaps optional"): sharing zaps is a separate choice. Zaps are shared only when you choose to: then each person, you included (with your first write relay), gets a NIP-57 `["zap", <pubkey>, <relay>, "1"]` tag of four parts, as readers of zap splits read them; a co-author whose profile names no lightning address (`lud16` or `lud06`, where a zap is paid) gets none (`nostr/metadata.can_receive_zaps`). Zaps another app shared keep the weights it gave them until you change them in Article Details.

### Audio and video

`Insert > Audio or Video…` makes the file an asset of the document, as a picture is (`AssetManager.adopt_media`: named by its hash, uploaded through the asset queue, kept through a crash or a restart), shown as a card (`nostr/ui/media_card.py`). Its type comes from one table (`media_files.py`), never the system's. Once uploaded it is written as its address, with its extension so readers can tell audio from video (`media_files.address_with_extension`), and described by an `imeta` tag (NIP-92: its type, hash and size); until then it is written nowhere, nothing is published while one is in the text, and a file saved meanwhile is edited again when the upload is done. It stays a paragraph of its own however it comes in (`HtmlEditor._set_media_apart` for pastes and drops).

### Relays and routing

Relay choice follows the **NIP-65 outbox model**, decided in one place (`nostr/outbox`):

- A note or article goes to **your write relays** from your kind 10002 list first, then a membership relay if you have one, then the **read relays of everyone it mentions** (or the relay hint in the mention when their list is unknown). Your relay list goes along to those people's relays, so readers there can find the rest of what you write. While your list is unknown, or has fewer than two write relays, a small curated set fills in.
- NIP-65 asks for all of a mentioned person's read relays; MyEditor sends to the first two of each (`INBOX_PER_MENTION`) and to ten for all mentions together (`INBOX_TOTAL_CAP`), so a note naming many people does not fan out to dozens of relays. The slots go round: everyone's first read relay, then everyone's second, so the people named last are reached too. The relay lists of everyone a note mentions are looked up in one request.
- **Private drafts**, the synced feed list and the private media library are written to your write and read relays, a membership relay, and the signer relays older versions stored drafts on (at most ten; a long relay list never pushes out the last two). While your list is still unknown, the curated set stands in for your own relays. Reading asks every relay writing goes to plus that curated set, so a draft saved before your list was known is still found once it is; deleting a draft reaches all of them too. Drafts can be kept on relays of your own choosing instead (see [Where drafts are kept](#where-drafts-are-kept)).
- **Someone else's article** (an `naddr`, an `npub`'s articles) is read from **their** write relays, never from yours.
- Relay lists are looked up on your own relays and on indexers, verified by signature, and cached: for 30 minutes when found, while your own list is also kept on disk so routing is right from the first second after a launch.
- Relays named by other people (their relay lists, relay hints in mentions and `naddr`/`nevent` links) are used only when they are public `wss://` relays: plain `ws://`, `localhost`, private and link-local addresses, `.local`/`.lan` style names and `.onion` are skipped, so a stranger's event cannot make MyEditor connect into your own network. Your own relay list is used as you wrote it.
- Your own profile and contact list are read the same way, and only a validly signed event counts.
- **Relays that ask you to sign in** (NIP-42). Some relays take or show events only after you sign in to them, for example a relay that keeps your drafts private. When one of your own relays (your relay list, the relays your drafts are kept on, a membership relay) refuses a request until you sign in, MyEditor signs in with your account and asks again; the status bar says "Signing in to wss://…". A signer app asks you to approve that, as for anything it signs. MyEditor never signs in unasked, and never to a relay you didn't choose, since signing in tells the relay who you are. A sign-in lasts as long as the connection. If your signer app declines, or the relay still refuses, the status bar says so and the relay's refusal stands. The relays of a signer app's channel are signed in to with the channel's own key, which says nothing about your account.

The publisher uses eager-first-accept semantics: as soon as one relay acknowledges the event, the dialog shows the result; remaining relays continue in the background and the final count lands in the status bar (e.g. `Published to Nostr: 6/7 relays · note1xxxxx…`).

### Security model

- **With a signer, no private key in the editor.** All signing goes through NIP-46 over NIP-44 v2 encryption. Every `sign_event` call surfaces an approval prompt in your signer.
- **An account kept on this computer** has its key in `~/.config/my_editor/nostr_keys.json` (`chmod 600`, folder `chmod 700`), separate from the profiles. It is never shown on screen, and Sign Out deletes it, after an alert that says what a missing backup costs. A damaged key file is set aside, never overwritten.
- **No secret key goes out by mistake.** While a Nostr account is in use, a paste or drop of a Nostr secret key (`nsec1…`, or an encrypted `ncryptsec1…`) into a document is stopped with a warning; you may paste its public key instead (the link to the key's owner). Nothing is published with a secret key in it. When you choose Publish, MyEditor looks at the note or article as it would go out (its text and link addresses, the title, summary and tags, whatever was typed into the publish sheet) and offers to publish it with each key replaced by its public key, or, for an encrypted key, asks you to remove it. A key in an article's or a draft's link name is shown under the link name before anything is sent, with Publish or Save dimmed (the link name is published as it is, and is never rewritten; one made from the title leaves a key out), and Edit Profile shows a key in a field under that field, with Save dimmed. Underneath all of these, nothing with a secret key in its text or tags is signed at all, by a key kept on this computer or by a signer app, so no other way can get one out; a draft's encrypted text is never affected. Plain-text and code files, and documents without an account, paste as they are.
- **Connection spoof protection.** The `nostrconnect://` flow generates a one-time secret that the editor verifies against the signer's response before completing the handshake.
- **Profile file permissions.** The on-disk profile store is restricted to the owner. The local channel keypair stored there only authorizes the existing bunker session; it cannot sign anything itself.

### What you published, and taking it back

The Drafts panel has a second view, **Published**, beside **Drafts**: the switch at the top of the panel moves between them (where the panel is narrow, or the text large, it is a pop-up button naming the view shown). Published lists the notes and articles the account in use has published, newest first by the day each one first went out. An article shows its title, a note its first line, and each row says Article or Note and the date. Editing an article later keeps its first date. Notes come fifty at a time, and **Load More Notes** at the end of the list brings older ones. Articles are read in one go, up to the newest 500; a relay may send fewer by its own rule (strfry sends at most 500), so an account with more articles than that sees its newest ones.

The list is read from the relays you publish to. Anything you asked to delete is left out, also when one relay still has a copy and another one has your request. The list is read when you first show the view, and again with **Refresh** in the panel's action menu (the ⋯ button at its top) or **Nostr > Refresh Drafts** (Command-R on a Mac, F5 elsewhere), which is named for the view shown. When no relay answers, the view says so and offers **Try Again**; it never looks empty just because nobody answered.

Right-click a row, or press the Menu key, for:

- **Edit** (articles): opens the article in a tab, with its title, summary, cover and hashtags. Publishing it again replaces it, keeps its address and keeps the date it first went out; saving it as a draft offers the same article. If the article (or a draft of it) is open already, its tab comes forward.
- **Open in Browser**: shows it on njump.me, a page any browser can open.
- **Copy Link**: copies that same address, to share.
- **Delete from Nostr…** (also the Delete key): asks first. Then MyEditor asks the relays to remove it with a deletion request signed by your account (NIP-09). The request goes to every relay the item was found on and to every relay you publish to. The row goes as soon as one relay accepts, and the status line says how many did. If none accepts, an alert says so and offers **Try Again**, which sends the same request again without asking your signer app. People and apps that already saved a copy may still have it, and a draft of the item stays.

Return opens a row: an article for editing, a note in the browser.

---

## Private encrypted drafts

In-progress work is saved as a **NIP-37 draft**: a kind 31234 event whose body is NIP-44 encrypted to your own Nostr key. Only you can decrypt it, and the encryption happens inside your signer so the editor never holds the plaintext key.

**Cross-device by design.** Drafts live on your own relays, and every device reads them from exactly where they were written. Sign in with the same Nostr profile on another device and the editor pulls those drafts straight back into the panel. No cloud account, no separate service.

### How to use it

- **`Nostr > Drafts`** shows the Drafts panel on the right, and hides it again.
- **`Ctrl+Shift+S`** asks where to save the current tab: local file or private Nostr draft. The choice can be remembered per tab.
- **`Ctrl+S`** on a draft-bound tab silently re-saves the draft. Same shortcut, no questions, exactly like saving a local file.
- Double-click a row to open the draft in a new tab. Right-click for Publish, Copy event id, or Delete.
- An article draft holds the article's Markdown and opens with its formatting (headings, bold, lists); a note draft opens as typed.
- An article draft also keeps the article's details: its title, summary, cover image, hashtags, the date it was first published and, for an imported post, where it came from. Saving the draft again keeps all of them, and **Publish Article** offers them, so an imported post goes out with its cover, hashtags and original date.

### Where drafts are kept

Drafts are encrypted wherever they are kept. To keep them on relays of your choice only, for example a relay of your own, choose **Nostr > Draft Relays…** (or **Draft Relays…** in the Drafts panel's action menu, the ⋯ button at its top), add the relays and click **Save**. The status bar then says where drafts are saved. An address starts with `wss://`; a relay running on this computer may also use `ws://`. You can choose up to ten.

From then on new drafts are saved only there. Drafts are still looked for on your usual relays too, so drafts saved earlier, or by an app that doesn't know this setting, are found. The choice itself is kept on your relays, encrypted to your own key (NIP-37), so your other devices and apps that read it (Amethyst does) keep drafts in the same place. Apps that don't read it won't see the drafts saved there.

Your drafts move with the setting. When you click **Save**, MyEditor first copies every draft from where drafts are kept now to where they go next, as it is: the drafts are already signed, so your signer app is not asked about them, and drafts you deleted stay deleted. The sheet counts them ("Moving 12 drafts…"). Before the new choice goes out, MyEditor asks the relays you chose for the drafts it just copied there: a relay that takes drafts but doesn't give them back is no place for them, and the sheet names it. A relay that shows your drafts only after you sign in gets that sign-in (see [Relays and routing](#relays-and-routing)); if your signer app declines it, the relay can't give the drafts back, and the sheet says so. Only when every draft is there and can be read back does the new choice go out (your signer app asks for that one), and from then on drafts are saved by it. If every relay set for your drafts asks for a sign-in that doesn't work, MyEditor saves new drafts on your usual relays instead, and the Drafts panel says so. The copies left on relays you no longer use stay there, encrypted; nothing asks those relays to delete them.

If a draft can't be copied, nothing changes: your drafts stay where they are, and the sheet says what to do. When a relay you are leaving doesn't answer, check your internet connection and try again; if that relay is gone for good, **Save Anyway** changes the setting all the same, and the drafts kept only there are no longer shown. If the setting was changed on another device while the sheet was open, nothing is written over it: the sheet shows the setting as it is now.

**Use My Usual Relays** empties the list; after **Save**, your drafts move back to your usual relays and are saved there again.

Another device that is running while you change this follows the new setting within a few minutes, when it next looks for drafts. Drafts it saved in the meantime on a relay you left are not lost: MyEditor keeps looking for drafts on that relay and moves them to where drafts go now, the same way **Save** does. The same holds for a relay you are leaving that stops answering while the change is saved: the sheet names it, and its drafts are moved once it answers again. Each such relay is looked at on its own, so one that is gone for good holds back no other, and it keeps being asked until it answers, in the background: no draft is given up without a word. The Draft Relays sheet lists these relays under **Still looking for drafts on**; if one is gone for good, select it and choose **Stop Looking…**. MyEditor asks first, as the drafts kept only there won't be shown any more.

### Recovery

If your signer (Amber, nsec.app) times out a decrypt approval or you dismiss the prompt by accident, the row shows up as failed with a one-click retry. Double-click the row or right-click **Retry decryption** to send the request again. No relay round-trip needed.

### Storage notes

- Drafts you save with this version never expire. An imported draft you don't change is removed after 90 days (NIP-40); once you change and save it, it stays like any other draft.
- A draft saved with an earlier version of MyEditor keeps the end date it was saved with (90 days after that save) until you save it again. The Drafts list shows that date in the draft's row ("Removed on ... unless you save it again"); open the draft and save it to keep it.
- Notes are tagged with a private UUID; articles use a stable link name, so the draft and its eventual published article share the same address.
- Deleting a draft publishes an empty replacement so your other devices see it removed; it never expires, so a relay that kept an old copy cannot bring the draft back.

---

## Media library (Blossom)

Keep images, video, audio and common files on your own Blossom media servers, browse them in a library that works like a Finder window or Photos, and put them into notes and articles. Files include every document format MyEditor opens or saves: PDF, plain text, Markdown, R Markdown, HTML and RTF. CSV and JSON, Office, OpenDocument and Apple iWork documents, spreadsheets and presentations, archives and EPUB books are supported too. File tiles and Quick Look use the operating system's file-type icons when available, with recognizable document, spreadsheet, presentation, archive and book glyphs as a fallback. Extension labels distinguish individual formats. Servers may restrict accepted types or file sizes; upload failures appear on the file's tile with Retry. Every upload is authorised by a kind 24242 event your signer signs; no separate API key, no third-party account.

### The library

`Nostr > Media Library…` opens it; `Nostr > Insert Image from Media Library…` (in the Insert menu as `Insert > Image from Media Library…` while an account is in use; a picture from your computer is `Insert > Image…`) and an article's cover open the same window to pick a file.

- **Toolbar**: **Add…** (the system's file dialog; several files at once), the kind of file to show (All Items, Images, Videos, Audio, Files), the order (newest, oldest, largest, smallest, name), a search field for names (`Cmd+F`), and an action menu (⋯) with the selected files' commands, **Refresh** and **Media Servers…**.
- **Grid**: square thumbnails that scroll (no pages; pictures load as they come into view), each with its name and the day it was added. Media servers keep no file names, so the library remembers the name a file had when it was added here (`~/.config/my_editor/media_names.json`); a file added elsewhere shows the day it was added.
- **Video and PDF thumbnails**: a frame of the video or the first page of the PDF. Visible files are processed one at a time without sound. Automatic remote thumbnail downloads are limited to 25 MiB per file; a larger movie already on this computer can still show a thumbnail. Other files have a type icon, extension and a caption naming their kind and size.
- **Quick Look previews**: video and audio play locally, with Play/Pause, a timeline, elapsed time, volume, mute and full-screen video. Playback starts when you ask for it; `Space` plays or pauses and `Escape` closes the preview (or leaves full screen first). PDFs have page navigation; text, Markdown, CSV, HTML, JSON, XML and YAML appear as read-only plain text. Long text previews show the first 256 KiB and say so. Office documents, presentations, archives and EPUB books offer **Save As…** to open them in an app on your computer. Loading has progress, Stop and Try Again. Changing the selection or closing Quick Look stops its load and playback.
- **Commands**, in the context menu and the action menu alike: **Insert** (when picking), **Quick Look**, **Copy Link**, **Open in Browser**, **Save As…** (several files: **Save to Folder…**), **Stop Sharing** (see below) and **Delete**, which asks first. Keys as in the Finder: arrows move, `Space` is Quick Look (it follows the selection, so the arrows page through it), `Return` or a double-click inserts when picking and otherwise opens Quick Look, `Delete` or `Cmd+Backspace` deletes, `Cmd+C` copies the links, `Cmd+V` adds a pasted image.
- **Adding**: Add…, files dragged onto the grid, or a pasted image. A file being added is a tile of its own, dimmed under a ring that fills as it uploads; the × in the ring cancels while the file is on its way. A file that did not upload says why under it (no connection, your signer app did not approve, the file is larger than your servers take, no space left), with **Retry** on it. Closing the window doesn't stop an upload; one that fails after it closed is counted in the status bar of the main window ("3 files weren’t added to your Media Library.", each reason in its tooltip) with **Open Media Library**, until the library is opened.
- **Dragging out**: a tile dragged onto a document is inserted there, as Insert Image from Media Library would insert it.
- **Saving**: Save As… downloads the file (a private one is saved as the file, not encrypted), checked against its hash before it takes its name, with progress and Stop at the foot of the window. Several files (Save to Folder…) are saved one after another; a file of the same name in the folder stays, and the new one is numbered as the Finder numbers a copy ("May 23, 2026 2.png"). One that didn't save every file says so at its end ("Saved 2 of 3 files. …"), with each reason in the tooltip. Closing the window doesn't stop a save, as in the Finder: the status bar of the main window says it is saving, and then that it is saved or why it wasn't. Quitting MyEditor while a file is still being saved asks first, in the same question as uploads still running; Quit Anyway stops it, and nothing half-saved is left in the folder.
- **Storage**: when a server gives you an allowance (the members' server), one line at the foot of the window says how much is used ("1.2 GB of 5 GB used", "Almost full" from 90 percent). When it is not known, nothing is shown.

### Who can see a file

Everything MyEditor uploads can be opened by anyone who has its link: a media server serves a file to whoever asks for its address. That is the normal case, so no tile is marked for it; the library says it once, in the quiet line at its foot ("Anyone with the link can see the files you add here."), and the questions before a pasted or dropped image is uploaded say it too. The exception is marked: files kept privately (encrypted, in the kind 34578 library other apps such as Lotus write) carry a small lock in the corner of their thumbnail ("Only you can see this"); MyEditor shows their pictures by opening them in memory, never on disk.

Private video, audio, PDF and text previews also open in memory: the shared cache keeps only their encrypted envelopes. Files larger than 128 MiB are not decrypted for a preview; **Save As…** remains available. Previewing never makes a public copy. A file whose privacy has not been checked is not handed to these viewers.

Inserting a private image, or choosing it as a cover, asks first ("Share “beach.jpg”? Anyone with the link will be able to see it. Camera and location details are removed first."). Sharing makes a separate public copy without camera and location details: a JPEG, PNG, WebP or BMP is re-encoded from its pixels, and a GIF keeps its frames, their timing and its loop as they are, and loses every other block (comments, application blocks such as XMP, text, anything unknown). Other kinds (HEIC, AVIF, video, audio, PDF) cannot be cleaned here yet, so a private file of those kinds is not offered for sharing, and the picker says why; the private original stays as it is, and the library shows the two as one tile, without the lock while the copy is out there. **Stop Sharing** deletes the public copy, after asking, from every server it was put on (also one since removed from Media Servers), and asks every server in use too, in case it is there unrecorded; while a server it is on cannot be reached, the image stays shared and the library says so (a server in use that never had it doesn't count); deleting the original deletes its copy too. While the private files are still being checked, or could not be checked (your signer app did not answer), the picker does not insert a file it cannot vouch for and says so under the grid, with **Try Again**.

### Media servers

**Media Servers…** (in the library's action menu) lists your servers in the order uploads use them: new files go to the first, the others keep copies. **Make Primary**, **Remove** (after asking; files already there stay there) and **Add Server** (by its address) change the list, which is saved in `~/.config/my_editor/blossom_servers.json`. The servers your Nostr account lists (its kind 10063 list) and MyEditor does not use yet are offered there; on a first run with nothing set up, that list is used as it is. **Share with Other Apps…** publishes your list as your account's kind 10063, after asking, so other Nostr apps find your files.

The defaults, chosen for two independent operators with a published per-file limit:

| URL | Per file | |
|---|---|---|
| `https://nostr.download` | 100 MiB | first |
| `https://blossom.primal.net` | about 100 MiB (unpublished) | copies |

**EINUNDZWANZIG members** also get the members' server, `https://blossom.einundzwanzig.space` (1 GiB per file, 5 GiB per member), while the membership is active. It is listed in Media Servers under the association's name, its files appear in the library even when another app uploaded them, and its storage line is the one at the foot of the library.

What happens across servers stays in the background: an upload goes to the first server that can take the file (each server's published limit decides, before anything is sent) and is then copied to the others one at a time, through BUD-04 mirroring or, where a server cannot mirror, a direct upload; a server whose allowance is full is left out. When a server does not keep a copy, the library says so once, when the file is added. A delete goes to every server the file is on; when one cannot be reached, the file stays in the library, saying it is still on one of your servers, until a later Delete reaches it.

### Security model

- **No separate credential.** Every privileged Blossom request carries an `Authorization: Nostr <base64(signed event)>` header where the event is a kind 24242 you signed via NIP-46. Your signer prompts you to approve the first upload / list call in a session.
- **No CORS proxy.** Requests go straight from the desktop app to the Blossom servers.
- **Content-addressed cache.** Thumbnails and previewed bytes are cached at `~/.config/my_editor/blossom_cache/<sha256>` and verified against the hash before use; a file loaded by an address that names its hash (the last 64-hex run, BUD-03) is refused when the bytes are another file.
- **Your own copy on each server.** A server that already has the bytes (someone else uploaded them) still gets the upload, so the file is in your own listing, yours to delete, and does not vanish when the other owner deletes theirs. Only a server your own fresh listing shows holding the file is skipped.

---

## Imports: follow websites, import files and links

Mirror your own blog, or a website you follow, into private Nostr drafts. Choose **Nostr > Imports…**, or the Imports row at the top of the Drafts panel, which also says how many new posts wait.

- **Add > Follow a Website…**: paste an address. MyEditor shows what it found (its name, whether it is a feed, a Nostr author or a collection, how many posts and how new the newest is) before anything is kept. While MyEditor is open it checks a followed feed for new posts, which appear in the Inbox; the posts that were already there when you followed it wait in Older Posts.
- **Add > Import a File…**, or drop a file on the window: a WordPress, Ghost, Medium or Substack export, a feed file, a Markdown file, or a list of sources (OPML) to follow.
- **Add > Import a Link…**: one post, thread, Nostr article or Markdown file, imported once and not kept as a source.
- Check the posts you want, or just open one, and choose **Create Drafts** (Command-Return or Ctrl+Return). **Options…** chooses whether images are copied to your media server and whether the whole article is fetched when a feed has only a summary. Imported drafts you don't change are removed after 90 days; once you change and save one, it stays like any draft you wrote.
- **Skip** (or Delete) sets posts aside on this computer; Undo, in the message or in Edit > Undo, brings them back, and so does Restore in the Skipped list.
- The toolbar shows how an import is going. Its card pauses, resumes, stops, and tries posts that failed again; the Drafts panel's Imports row shows the same line.
- **Add > Export Sources…** writes every source to an OPML file, which other feed readers (and EINUNDZWANZIG STANDUP) import.

Each draft keeps the post's title, summary, cover image, hashtags and original date.

### Each source's settings

Choose **Source Settings…** in a source's menu (the button beside it in the sidebar, or a right-click):

- Its name as MyEditor shows it, and its address. Under the name it says when it was last checked ("Checked 4 min ago").
- **Check for new posts automatically.** When it is off, the source is read only when you open it or choose **Check Now**, and the sidebar shows a pause mark beside it.
- Its defaults for new drafts: copy images to your media server, and fetch the whole article when the feed has only a summary. They travel with your list of sources, so EINUNDZWANZIG STANDUP uses them too.
- **Check Now** asks the source at once (once a minute at most).
- **Unsubscribe…** removes the source from your list on your relays, and its posts from this computer. Drafts made of them stay.

While MyEditor is open, each source that is checked automatically has its own schedule. Above 50 sources the time between checks stretches, so that no more than about 200 checks run in an hour, two at a time. Each check asks only for what changed since the last one: a feed that did not change costs no download. A source that fails is asked again later, a little later each time, and its sidebar row shows a red dot with the reason.

Sources show their website's icon once MyEditor has found it (the icon the home page names, or its `/favicon.ico`), and their first letter until then. Icons are kept in `~/.cache/my_editor/favicons` and looked for again after 30 days.

### New posts on the app icon

**Nostr > Show New Posts on the App Icon** puts the number of posts waiting in the Inbox on MyEditor's icon: in the Dock on macOS, on the taskbar on Windows, in the launcher where a Linux desktop has one. It is off until you turn it on; the Imports window offers it once, the first time new posts arrive there. There are no other notifications.

### What is kept on this computer, and for how long

The inbox (each source's checks, the posts, what you skipped, imports under way) is kept for your account in `~/.config/my_editor/imports/`, and never shared. A post keeps its text while it waits in the Inbox, 90 days at most; a post you skipped or imported keeps it 30 days. After that it keeps its title, link and state, which is enough to recognise it again: it is never offered as new again, and opening it says that its text is no longer kept here. **Open Original** reads it, and a draft made of it gets the post from its page.

A damaged inbox file is set aside (renamed, never deleted) and a new one started; the window says so once. When the inbox cannot be opened at all, **Nostr > Imports…** says why in plain words and what to do (a folder that cannot be written to, a full disk, a program that holds the file), and tries again the next time you choose it.

With MyEditor open twice, the second one shows Imports read only and says so above the list, while the first checks and imports. When the first quits, the second takes over within a few seconds.

### Addresses that work

You don't need the feed URL. The editor accepts:

- A feed URL (`https://yourblog.example.com/feed/`)
- The site's homepage or any article URL on the site
- A bare domain like `yourblog.example.com` (the `https://` is added automatically)

Auto-discovery reads `<link rel="alternate" type="application/rss+xml">` from the page head, falls back to the well-known `/feed/` path for WordPress / Ghost / Substack, and explains itself in plain language when no feed is found.

Supports RSS 2.0, Atom, and JSON Feed (WordPress, Ghost, Hugo, Jekyll, Substack, Bear, Mataroa, and friends).

Nothing an import reads may point into your own network: feeds, pages, podcast chapters, site icons and images on `localhost`, local-only names (`router`, `nas.local`, `.lan`), private or link-local addresses, or names that resolve to one are refused, and every redirect is checked the same way before it is followed (five at most). Each connection then goes to the very address that was checked, so a name whose answer changes after the check cannot lead into your network either (DNS rebinding). Behind a proxy the address is still checked, and the proxy makes the connection.

### Nostr-native publishers

Some publishers (Habla, Yakihonne, Pareto, self-hosted Nostr-aware blogs) emit feeds where the body is a teaser and the real article lives on Nostr as a kind:30023 long-form event. When the feed's link is a `nostr:naddr...` URI, or contains a bech32 naddr embedded in an HTTP URL (njump.me, habla.news, yakihonne.com, etc.), the importer fetches the event from your NIP-65 read relays plus the relay hints encoded in the address and uses its prose as the draft body. If the fetch times out or the event is empty, the feed-provided teaser is published instead so the draft always ships.

### Sources you follow, shared with EINUNDZWANZIG STANDUP

The sources you subscribe to are one private list on your relays, the same list EINUNDZWANZIG STANDUP keeps (a kind 30078 event, `d` = `einundzwanzig:feed-sources`, encrypted to yourself with NIP-44). A source followed in one app shows up in the other, together with its defaults for new drafts (copy images, fetch the full article).

- Both apps' edits are kept: before saving, MyEditor reads the list again and merges (sources added in either app stay, sources removed in either app go, a title or a default changed here wins over the other app's unchanged one).
- Whatever MyEditor does not understand (sources only STANDUP reads, keys it does not know) is written back unchanged.
- A list saved with the older encryption, NIP-04, is read too: with a key kept on this computer, or through your signer app when it offers that (MyEditor asks for it when it pairs). MyEditor writes the list back with NIP-44 only.
- A list MyEditor cannot read (while your signer app does not answer, or one it cannot decrypt) is never written over; your changes stay on this computer until it can be read, and the Imports window says so above the list.
- The list MyEditor kept before (`d` = `myeditor:feed-sources`) is merged in once and not written again.
- Quitting waits a few seconds for changes that are still on their way. Changes that could not be sent yet are kept with the list on this computer and sent at the next start.

### Importing again never overwrites a draft

Each item's draft identifier is derived from its feed id the same way EINUNDZWANZIG STANDUP derives it, so the same post gets the same identifier in both apps. Before anything is fetched or signed, MyEditor asks your relays which of the chosen posts already have a draft (also one you deleted), an article or a deletion request, and leaves those alone: a draft you edited after importing it stays exactly as it is, and a deleted one does not come back. When no relay answers, the import does not start. The question is asked once more right before each draft is signed, in case the other app made it in the meantime.

## Joining EINUNDZWANZIG in the app

Nostr > EINUNDZWANZIG Membership lets a person apply, pay the annual fee with Lightning and see what membership unlocks.
- The window names every service the association lists for its members, in its order: the members' relay, a Nostr address, a Lightning watchtower, media storage, the community group and Buzz (experimental). The association publishes that list only as text on its member portal, so `MEMBER_SERVICES` in `nostr/einundzwanzig.py` is a copy, with the source file and commit it was taken from; a link opens the association's own page with every detail and setup guide. MyEditor sets up the relay, the address and the media server itself; the member page links to the association's guide for the others.
- Which host does what: `wss://nostr.einundzwanzig.space` is the members' relay for ordinary notes and articles (it goes in the relay list); `https://blossom.einundzwanzig.space` is the media server and has no relay that goes with it; the community group and Buzz relays need a login and never go in a relay list.
- The fee shows in sats first, then CHF, then EUR. The association's own amount is exact; the others are converted with today's Bitcoin price from mempool.space (asked once while the window is open; mempool.space sees this computer's address, but no account and no reason for asking) and say "about". The invoice's sats are always exact.
- After a member chooses a Nostr address, the window asks once whether their profile should show it. Not Now leaves a Show on My Profile button on the member page, so they can change their mind later. Writing it reads the profile fresh and keeps every other field; a profile that cannot be found or read is never replaced (`nostr/profile_address.py`).

The association's API needs a client key that must not ship inside the app, so membership requests go through a small service that holds it: the membership sidecar in [`sidecar/`](../sidecar/README.md).
- A build names its sidecar in `constants.MEMBERSHIP_SERVICE_URL`. Official builds name the maintainers' sidecar, `https://e21.rinbal.de`.
- A self-hosted build names its own sidecar, or none. `MYEDITOR_MEMBERSHIP_SERVICE` in the environment names one without rebuilding (an `https://` address, or `http://` on this computer).
- Without a sidecar, with one that has no key, or with one whose key the association refuses, the window says joining in the app isn't available right now and offers the association's website instead.
- Members get their relay and media server either way, recognized from the association's public member list.
- Running a sidecar (Docker with Caddy, or systemd behind Caddy or nginx), its limits and what it logs are in [`sidecar/README.md`](../sidecar/README.md). Its server's clock must be synchronized (NTP), because signatures are valid for a minute only.
