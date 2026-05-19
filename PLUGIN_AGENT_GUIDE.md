# Plugin Agent Guide

Instructions for an AI assistant tasked with building a my-editor plugin. Read this **before** writing code. The companion file is [`PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md) — that is the human-facing tutorial; this is the spec for getting it right on the first try.

---

## Source of truth (open these before guessing)

| Need | File |
|---|---|
| Real API surface | `plugin_system/api.py` |
| What the loader accepts/rejects | `plugin_system/loader.py` |
| Where things get written on disk | `plugin_system/paths.py`, `plugin_system/settings.py` |
| Working reference plugin | `bundled_plugins/hello_world/` |
| Test fixtures (`FakeHost`) | `tests/test_plugin_loader.py` |

If the user asks for a capability not present in `plugin_system/api.py`, **say so**. Do not invent methods.

---

## Hard rules — never violate

1. **Don't import editor internals** — no `main_window`, `nostr`, `editor`, `widgets`, no Qt. The `api` arg is the entire stable surface.
2. **Don't call `api.publish_event(...)`** — it doesn't exist in v1. If the user asks, push back: "Not implemented yet; `declared_kinds` reserves it."
3. **Don't store secrets in settings.** Plaintext JSON on disk. Ask the user to provide credentials at runtime, or document an env var.
4. **Don't run `pip install` from inside the plugin.** Vendor deps into `vendor/` at build time. The editor never touches the user's site-packages.
5. **Don't hard-code `/` paths.** Use `pathlib.Path`. Plugins run on macOS, Windows, Linux.
6. **Don't write outside scoped settings or the user data dir.** No `open("/tmp/...")` lying around.
7. **Don't bypass `register(api)`.** Top-level side effects (network calls, file writes) at import time will run before the editor is healthy and will get blamed on the loader.

---

## Required scaffold

Every plugin is exactly this:

```
<plugin_id>/
  manifest.json     required
  __init__.py       required, must export register(api)
  vendor/           only if you vendor deps
  assets/           only if you ship images/icons
```

Minimum `manifest.json`:

```json
{
  "id": "your_id",
  "name": "Your Name",
  "version": "0.1.0",
  "description": "One sentence.",
  "author": "your-npub-or-name",
  "api_version": 1,
  "declared_kinds": []
}
```

Minimum `__init__.py`:

```python
def register(api):
    api.add_menu_action("Plugins", "Do The Thing", on_click)

def on_click():
    ...
```

---

## DO / DON'T

| Do | Don't |
|---|---|
| Put menu entries under `"Plugins"` | Create a top-level menu per plugin |
| Guard `get_current_text()` with `if text is None:` | Assume a tab is open |
| Coerce settings reads (`int(api.get_setting("k", 0))`) | Trust the stored type — JSON can round-trip oddly |
| Use `set_setting` for kilobytes of state | Stuff megabytes in there — write your own file |
| Catch exceptions inside menu callbacks and surface them | Let one click crash the menu loop silently |
| Use Pathlib + `__file__` to locate bundled assets | Compute paths relative to the working dir |
| Mark unsupported OSes with `"platforms": [...]` | Crash at import time on an OS you didn't test |
| Bump `version` whenever behaviour changes | Re-publish the same version with new code |
| Keep `vendor/` minimal and pinned | Drop your whole site-packages in there |
| Set `"enabled": false` for opt-in betas | Ship a half-finished plugin enabled by default |

---

## register(api) — the rules

```python
def register(api):
    # 1. Pure setup. Wire callbacks, register providers.
    # 2. Return. Don't block; don't spawn long-lived threads here.
    # 3. Anything that runs later happens inside the callbacks you wired.
```

- `register` runs **once** at editor startup.
- Exceptions inside `register` are caught — your plugin shows in the failed-load list. Don't rely on this for control flow; it's a diagnostic.
- No hot reload. Code changes need an editor restart.
- The `api` object is scoped to your `id`. Settings keys, providers, and declared kinds live in your namespace. Stop worrying about collisions with other plugins.

---

## API recipes

### Menu action that edits the current tab

```python
def register(api):
    api.add_menu_action("Plugins", "Uppercase Selection", upper)

def upper():
    text = api.get_current_text()
    if text is None:
        return                                  # no tab open
    api.set_current_text(text.upper())
```

### Open a fresh tab

```python
api.open_tab("Report", generate_report())
```

The user can Save-As; you don't choose the file path.

### Scoped settings

```python
count = int(api.get_setting("invocations", 0)) + 1
api.set_setting("invocations", count)           # JSON-serialisable only
```

Allowed values: `str`, `int`, `float`, `bool`, `list`, `dict`, `None`. Nothing else.

### Payment provider

`PaymentProvider` is **duck-typed**. Implement exactly this protocol:

```python
class MyProvider:
    name = "NWC"                                # str, non-empty

    def is_ready(self) -> bool:
        ...

    def pay_invoice(self, bolt11, sats, on_success, on_failure):
        # async / signal-style:
        #   on_success(preimage_hex)
        #   on_failure(human_readable_reason)
        ...

def register(api):
    api.register_payment_provider(MyProvider())
```

`pay_invoice` must call exactly one of `on_success` / `on_failure`. Never raise; turn errors into `on_failure("reason")`.

---

## Vendoring third-party deps

Only vendor when the editor doesn't already ship the package. Check `requirements.txt` first.

```bash
cd <plugin_folder>
pip install --target=vendor <package>
```

The loader prepends `vendor/` to `sys.path` before importing your `__init__.py`. Each vendored package must be a real package (`__init__.py` present).

**Refuse** to bundle: anything > 5MB, anything that links native libraries the user might not have, anything that opens network connections at import time.

---

## Manifest checklist

**Required (loader enforces):**
- [ ] `id` — filesystem-safe, unique
- [ ] `name`, `version` (SemVer), `description`, `author`
- [ ] `api_version: 1`
- [ ] `declared_kinds` — `[]` is fine

**Recommended (marketplace surfaces them):**
- [ ] `min_editor_version`
- [ ] `tags` — for search
- [ ] `repository` — for trust
- [ ] `icon_url`

**Only if applicable:**
- [ ] `platforms` — restrict OSes
- [ ] `enabled: false` — opt-in beta
- [ ] `price_sats` + `lightning_address` — paid plugin
- [ ] `download_url` + `sha256` — HTTPS install source

**Skip in v1:**
- `listing_event` — Nostr registry not yet wired

---

## Shipping an update

Updates are detected purely by version comparison. The marketplace has an **Updates** section that auto-populates when a registry's listing version is higher than the installed version (SemVer tuple compare, recomputed every time the marketplace opens).

**The update flow, in order:**

1. Bump `version` in `manifest.json` (SemVer). Fix → patch, new feature → minor, breaking → major.
2. Rebuild the zip. Recompute `sha256` — the installer rejects a mismatch.
3. Update the registry index (or HTTPS source) entry for your `id` to point at the new `version`, `download_url`, `sha256`. **Keep `id` identical** — that's the match key.
4. Tell the user to open the marketplace; the plugin appears in Updates; the Install button reads `Update to {version}`.

**Do**

| Do | Why |
|---|---|
| Bump SemVer on every code change that ships | Same-version re-publish silently breaks auto-update — users won't see it |
| Roll forward (`1.0.1` → `1.0.2`) to fix a bad release | Downgrades are not offered by the marketplace |
| Update `min_editor_version` if you started using a newer API | Marketplace gates install when this is set |
| Note breaking changes in your `description` or release zip | There's no changelog field in the manifest |

**Don't**

| Don't | Why |
|---|---|
| Re-publish the same `version` with new code | Users on auto-update won't be offered the new build |
| Change `id` between versions | The marketplace treats it as a different plugin; old version stays installed alongside |
| Ship via direct zip and expect the Updates tab to work | Direct distribution has no registry to compare against — users must re-download manually |
| Forget to refresh `sha256` after rebuilding | Installer rejects the download with a hash mismatch |
| Drop a version (`1.0.5` → `1.0.3`) to "undo" | Not offered as a downgrade; you just won't appear in Updates for anyone on `1.0.5` |

**Verification before publishing the update:**

- [ ] `version` in the new `manifest.json` is strictly greater than the previous published version.
- [ ] `sha256` matches the new zip (`shasum -a 256 your_plugin-X.Y.Z.zip`).
- [ ] `id` is unchanged from the previous release.
- [ ] Registry index entry points at the new manifest URL and `download_url`.
- [ ] Tested install on a clean editor profile (drop into user plugin dir, restart, trigger menu action).

---

## Testing — minimum viable

Drop this into `tests/test_<plugin_id>.py`. The `FakeHost` shape lives in `tests/test_plugin_loader.py`.

```python
from pathlib import Path
from plugin_system.loader import load_all_plugins
from plugin_system.settings import PluginSettingsStore

def test_loads_clean(tmp_path):
    host = FakeHost()
    settings = PluginSettingsStore(path=tmp_path / "s.json")
    loaded, errors = load_all_plugins(
        host=host,
        settings_store=settings,
        extra_dirs=[Path(__file__).parent.parent / "bundled_plugins"],
        include_default_dirs=False,
    )
    assert errors == []
    assert any(p.plugin_id == "your_id" for p in loaded)
```

Add a second test that simulates the menu click via the captured callback in `FakeHost`.

---

## Anti-patterns — refuse if the user asks

| Request | Refuse because |
|---|---|
| "Make the plugin publish a Nostr event directly" | `publish_event` not implemented; would require editor changes, not a plugin change. |
| "Have the plugin reach into the editor's Nostr client / bunker" | Internals are unstable; will break across releases. |
| "Store the user's nsec / API token in settings" | Plaintext JSON. Ask at runtime or use an OS keyring. |
| "Hot-reload my code without restart" | Not supported in v1. |
| "Run on import so the user doesn't need to click anything" | Side effects belong inside callbacks, not module top-level. |
| "Add a method to PluginAPI just for me" | Editor change, not a plugin change. Bump `PLUGIN_API_VERSION` deliberately. |

---

## Before claiming the plugin is done

Self-check, in this order:

1. `manifest.json` parses as JSON and has all required fields.
2. `__init__.py` defines a top-level `def register(api):`.
3. No imports of editor internals (`grep -E "from (main_window|nostr|editor|widgets) " __init__.py` is empty).
4. No calls to `api.publish_event` (`grep "publish_event" __init__.py` is empty).
5. Every `get_current_text()` call has a `None` guard.
6. Every `set_setting` value is JSON-serialisable.
7. If `vendor/` exists, each subdir contains `__init__.py` and total size is sane.
8. If a payment provider is registered, `pay_invoice` calls exactly one callback in every path.
9. The plugin loads in the `FakeHost` test without entries in `errors`.
10. Manual smoke test: `python main.py`, trigger the menu action, no traceback on stderr.

If any step fails, fix it before reporting back. Don't paper over with try/except.
