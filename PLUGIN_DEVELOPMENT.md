# Building Plugins for my-editor

The editor core is small. Everything optional lives as a plugin: a folder with `manifest.json` + `__init__.py` that the loader discovers at startup.

> PluginAPI v1 · editor `1.0.0`+ · working example: [`bundled_plugins/hello_world/`](bundled_plugins/hello_world/)

---

## What plugins can do today

| Capability | Status | How |
|---|---|---|
| Add menu actions | shipping | `api.add_menu_action(menu, label, callback)` |
| Read / replace active tab text | shipping | `api.get_current_text()` · `api.set_current_text(text)` |
| Open a fresh tab with content | shipping | `api.open_tab(title, content)` |
| Persist scoped settings | shipping | `api.get_setting(key, default)` · `api.set_setting(key, value)` |
| Register a payment provider (NWC, LNbits, Stripe, Paypal …) | shipping | `api.register_payment_provider(provider)` |
| Ship vendored Python deps | shipping | drop them in `vendor/`; loader adds it to `sys.path` |
| Per-platform plugins (mac/linux/win) | shipping | `"platforms": ["darwin"]` in manifest |
| Be installed/uninstalled via the marketplace | shipping (HTTPS sources) | `download_url` + `sha256` in manifest |

---

## 60-second start

```python
# <user_plugins>/hello/__init__.py
def register(api):
    api.add_menu_action(
        "Plugins",
        "Say Hello",
        lambda: api.open_tab("Greeting", "Hello from a plugin"),
    )
```

```json
// <user_plugins>/hello/manifest.json
{
  "id": "hello",
  "name": "Hello",
  "version": "0.1.0",
  "description": "Quick hello.",
  "author": "your-npub-or-name",
  "api_version": 1,
  "declared_kinds": []
}
```

Restart the editor. The action appears under the `Plugins` menu. For a fuller working example see `bundled_plugins/hello_world/`, which exercises every shipping API call.

---

## Where plugins live

The loader scans, in order:

1. `bundled_plugins/` next to the editor source (read-only, ships with the install).
2. The user plugin directory:
   - **macOS** — `~/Library/Application Support/my_editor/plugins/`
   - **Linux** — `$XDG_CONFIG_HOME/my_editor/plugins/`, falling back to `~/.config/my_editor/plugins/`
   - **Windows** — `%APPDATA%\my_editor\plugins\`

Same `id` in both locations? The user copy wins — that's how updates ship. Set `"enabled": false` in the manifest to make the loader skip a plugin.

---

## Anatomy of a plugin folder

```
your_plugin/
  manifest.json     metadata + capabilities
  __init__.py       must export register(api)
  vendor/           optional — third-party Python deps you ship
  assets/           optional — icons, screenshots
```

At startup the loader reads the manifest, prepends `vendor/` to `sys.path`, imports `__init__.py`, and calls `register(api)` once. Exceptions in `register()` are caught and logged; the editor keeps running and your plugin shows in the failed-load list.

Don't import editor internals (`main_window`, `nostr`, `editor`). The `api` argument is the entire stable surface.

---

## manifest.json fields

### Read by the loader (affect whether your plugin loads)

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique. Filesystem-safe. |
| `name` | string | yes | Shown in the Plugins menu. |
| `version` | string | yes | SemVer. |
| `api_version` | int | yes | Currently `1`. Loader refuses plugins targeting a newer version. |
| `declared_kinds` | int list | yes | Reserved for a future `publish_event` capability. Use `[]` today. |
| `enabled` | bool | optional | Default `true`. Set `false` to ship a manifest the loader should skip. |
| `platforms` | string list | optional | E.g. `["darwin","linux"]`. Loader skips on other OSes. |

### Read by the marketplace (affect listing, install, payment)

| Field | Purpose |
|---|---|
| `description`, `author`, `tags`, `icon_url`, `repository` | Card / detail page metadata. |
| `min_editor_version` | Marketplace gates install. |
| `download_url`, `sha256` | HTTPS install source — installer verifies the hash before unpacking. |
| `lightning_address`, `price_sats` | Paid plugins. Payment is routed to the address; `0` or omitted means free. |
| `listing_event` | `naddr1...` of a future Nostr listing event. Parsed today, not yet wired. |

---

## PluginAPI v1 — full surface

Everything below exists in `plugin_system/api.py` and is exercised by `tests/test_plugin_loader.py`.

```python
api.plugin_id           # str (from manifest)
api.name                # str
api.version             # str

api.add_menu_action(menu, label, callback)
api.get_current_text()                       # -> str | None
api.set_current_text(text)                   # -> bool
api.open_tab(title, content)

api.get_setting(key, default=None)
api.set_setting(key, value)                  # JSON-serialisable values only

api.register_payment_provider(provider)      # see below
```

The `api` object is **scoped to your plugin**: setting keys, registered providers, and declared kinds live under your `id`. Two plugins can use the same setting key without colliding.

### Settings storage

Stored at `<user_config>/plugin_settings.json`, chmod 600 on POSIX, best-effort on Windows. Atomic writes. Use it for kilobytes; for megabytes write your own file in the user data dir.

---

## Bundling dependencies

If you need a third-party package the editor doesn't already ship, vendor it:

```bash
# from your plugin folder
pip install --target=vendor feedparser pytz
```

The loader prepends `your_plugin/vendor/` to `sys.path` before importing your `__init__.py`, so `import feedparser` Just Works. The editor never runs `pip install` against the user's environment.

Keep `vendor/` tight — every megabyte is startup time and review burden.

---

## Payment providers

A plugin can offer the marketplace a way to pay an invoice — e.g. the bundled NWC plugin pays bolt11s in one click. The shape is duck-typed:

```python
class MyProvider:
    name = "NWC"
    def is_ready(self) -> bool: ...
    def pay_invoice(self, bolt11: str): ...

def register(api):
    api.register_payment_provider(MyProvider())
```

The marketplace calls `is_ready()` when the user clicks Install on a paid plugin; first ready provider wins; otherwise it falls back to a QR-code invoice.

---

## Distributing your plugin

### Direct (no registry)

Zip your folder, host the zip anywhere, tell users to unzip into their plugin directory and restart. Fine for private tools and beta testing.

### Custom HTTPS source

Host a JSON index that lists `manifest.json` URLs (each manifest carries `download_url` + `sha256`). Add the source URL in the marketplace UI; the installer fetches and hash-verifies the zip.

### Nostr-native source *(planned)*

The roadmap is `kind:30700` addressable events signed by your npub, aggregated by a curator index (`kind:30750`). The `listing_event` manifest field exists for this. **Not wired yet** — don't rely on it.

---

## Shipping an update

Updates are version-driven. There is no separate "publish update" action — bump the version and the marketplace handles the rest.

### What you do as the developer

1. Bump `version` in `manifest.json` (SemVer: `1.0.0` → `1.0.1` for fixes, `→ 1.1.0` for new features, `→ 2.0.0` for breaking changes).
2. Build the new zip and recompute `sha256`.
3. Update your registry index (or HTTPS source) so its listing for your `id` points at the new `version` + `download_url` + `sha256`.
4. Done. No coordination with the editor team, no separate update button to wire.

Keep `id` the same — that's how the installer recognises "this is the same plugin, replace it" rather than "install a second copy".

### What the user sees

- The marketplace has three sections: **Discover**, **Installed**, **Updates**.
- On every marketplace open, the controller refetches every enabled registry and compares each installed plugin's on-disk `version` against the listing's `version` (SemVer tuple compare). If the listing is newer, the plugin appears in the Updates section.
- The Install button on a plugin that has an update relabels itself to `Update to {version}` — same flow, atomic replace (the installer moves the old folder to a backup, unpacks the new one, swaps).
- The user restarts the editor to pick up the new code (no hot reload).

### Gotchas

- **Downgrades aren't offered.** If you publish a regression and bump back to a *lower* version, the marketplace won't downgrade users. Ship a higher SemVer (`1.0.1-fix.1` or `1.0.2`) to roll forward.
- **Direct-distribution plugins don't get update notifications.** If you skip the registry and just hand users a zip, the marketplace has nothing to compare against. Users must manually re-download.
- **`sha256` must match the new zip.** If you bump `version` but forget to refresh the hash, the installer rejects the download.
- **Don't re-publish the same `version` with new code.** It silently confuses auto-update: users who already have it won't be offered the new build.

---

## Earning bitcoin

Set `price_sats` and `lightning_address` in your manifest:

```json
{
  "price_sats": 2100,
  "lightning_address": "you@getalby.com"
}
```

That's all you need. The marketplace generates the invoice off your lightning address; you don't run a server, hold custody, or manage invoices. You're notified via your wallet (Alby/Coinos/LNbits webhooks, NIP-47 events, whatever).

---

## Testing locally

```bash
# 1. Drop your folder in the user plugin dir
# 2. Run from a terminal so stderr is visible
python main.py
```

Failed loads write a traceback to stderr and surface a "1 plugin failed to load" message in the status bar.

For automated tests, see `tests/test_plugin_loader.py` — it exposes a `FakeHost` fixture and shows the full `load_all_plugins(...)` shape.

---

## Common pitfalls

| Symptom | Likely cause |
|---|---|
| Plugin doesn't appear after restart | Missing `manifest.json`, syntax error in `__init__.py`, `"enabled": false`, or current OS not in `platforms`. Check stderr. |
| `register` not exported | Your `__init__.py` must contain a top-level `def register(api):`. |
| Vendored library not importing | `vendor/<lib>/` must be a real package (`__init__.py` present); use `pip install --target=vendor`. |
| "Two plugins fighting over the same setting" | They aren't. Settings are scoped per plugin `id`. |
| Cross-platform path bugs | Use `pathlib.Path`; never hard-code `/`. |
| Looking for `publish_event` | Not implemented in v1. `declared_kinds` reserves the capability for later. |

---

## Best practices

- **Stick to `api.*`.** Importing private editor modules will break across releases; the PluginAPI is versioned.
- **Settings are not a secret store.** Plaintext JSON, chmod 600. Don't store long-lived secrets — ask the user.
- **Fail loudly in development, gracefully in production.** Surface errors through a friendly menu action rather than crashing.
- **Version conservatively.** Bump SemVer when behaviour changes; re-publishing the same version confuses auto-update.
- **Keep `vendor/` tiny.** Startup time and trust both scale with it.

---

## Reference: a paid plugin manifest

```json
{
  "id": "rss_importer",
  "name": "RSS Importer",
  "version": "1.2.0",
  "description": "Import RSS, Atom and JSON feeds as private Nostr drafts.",
  "author": "npub1abc...xyz",
  "icon_url": "https://example.com/rss.png",

  "api_version": 1,
  "min_editor_version": "1.0.0",
  "declared_kinds": [],

  "price_sats": 2100,
  "lightning_address": "alice@lnbits.com",

  "download_url": "https://example.com/releases/rss_importer-1.2.0.zip",
  "sha256": "f7a3...",

  "repository": "https://github.com/alice/my-editor-rss",
  "tags": ["nostr", "rss", "import", "drafts"]
}
```

---

## License & liability

Plugins are arbitrary Python. The loader catches errors but cannot sandbox malicious code. If your plugin signs Nostr events, asks for credentials, or moves money: open-source it at the URL in `repository`, document its network calls, and treat user data with care. The Nostr-anchored comment thread on your plugin's detail page is the social check that keeps the marketplace honest.

Good luck. Ship something.
