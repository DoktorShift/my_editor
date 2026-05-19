"""Plugin Starter - the reference plugin and the file you should read
first if you want to author your own.

This single file is BOTH:

  1. A working plugin that adds a Marketplace menu entry, a side dock
     panel with a click counter and a periodic tick, and a teardown
     hook so the timer is cleaned up when the plugin is disabled.
  2. The second half of that quickstart. The in-tab text covers the
     concepts; this file shows you what the code that backs them looks
     like, with comments wherever a decision matters.

If you only have ten minutes, read top-to-bottom in this order:

  - the docstring you're in right now,
  - ``register`` further down,
  - the ``_QUICKSTART`` string near the bottom.

That's the whole authoring loop.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from plugin_system import PluginAPI


# Background tick interval for the dock panel. Five seconds is slow
# enough that you can see it happen without being noisy in tests.
_TICK_INTERVAL_MS = 5_000


# A plugin's runtime entry point is a module-level ``register(api)``
# function. The loader calls it exactly once, very early in startup,
# with a fresh ``PluginAPI`` instance scoped to this plugin's id.
#
# Do all your wiring inside ``register``. Don't run heavy work at
# module import time: the loader imports every installed plugin before
# showing the window, so slow imports delay the user.
def register(api: PluginAPI) -> None:
    """Editor entry point. Called once at startup by the loader."""

    # ---- 1. A plain menu action. PluginAPI v1, still the workhorse.
    def open_quickstart() -> None:
        api.open_tab("Plugin Authoring Quickstart", _QUICKSTART)

    api.add_menu_action(
        "Marketplace", "Plugin Starter: Quickstart", open_quickstart,
    )

    # ---- 2. A dock widget. PluginAPI v2.
    # The factory is what the host calls to build the actual Qt widget;
    # deferring construction until the host asks keeps register() fast
    # even if a plugin's UI is heavy.
    state = _PanelState()

    def build_panel() -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("Plugin Starter Panel")
        header.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(header)

        clicks_label = QLabel("Clicks: 0")
        ticks_label = QLabel("Ticks: 0")
        layout.addWidget(clicks_label)
        layout.addWidget(ticks_label)

        button = QPushButton("Click me")
        layout.addWidget(button)

        layout.addStretch(1)

        state.bind(
            clicks_label=clicks_label,
            ticks_label=ticks_label,
            button=button,
        )
        return widget

    api.add_dock_widget("Plugin Starter", build_panel, area="right")

    # ---- 3. A teardown hook. PluginAPI v2.
    # The host runs every registered callback (LIFO) before retracting
    # the plugin's menu actions and dock widgets, so the timer stops
    # *before* the panel it updates is removed. Plugins that own
    # background work (timers, threads, sockets) should always pair it
    # with a register_unload callback.
    api.register_unload(state.shutdown)


class _PanelState:
    """Holds the dock panel's timer and the widgets it talks to.

    The state lives outside the factory so the unload hook can stop the
    timer without depending on widget references that Qt has already
    deleted by the time teardown runs.
    """

    def __init__(self) -> None:
        self._clicks = 0
        self._ticks = 0
        self._clicks_label: Optional[QLabel] = None
        self._ticks_label: Optional[QLabel] = None
        self._timer: Optional[QTimer] = None

    def bind(
        self,
        *,
        clicks_label: QLabel,
        ticks_label: QLabel,
        button: QPushButton,
    ) -> None:
        self._clicks_label = clicks_label
        self._ticks_label = ticks_label
        button.clicked.connect(self._on_click)

        # Background tick. A real RSS plugin would replace this with a
        # poll loop; the pattern is the same: own the timer so the
        # unload hook can stop it cleanly.
        self._timer = QTimer()
        self._timer.setInterval(_TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _on_click(self) -> None:
        self._clicks += 1
        if self._clicks_label is not None:
            self._clicks_label.setText(f"Clicks: {self._clicks}")

    def _on_tick(self) -> None:
        self._ticks += 1
        if self._ticks_label is not None:
            self._ticks_label.setText(f"Ticks: {self._ticks}")

    def shutdown(self) -> None:
        """Stop the timer. Safe to call more than once."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None


# Plain-text content for the quickstart tab. We deliberately use
# markdown-flavored plain text instead of HTML: it renders well in the
# editor's tab as just-text, and copy-paste into a chat works without
# surprises. Keep the content high-signal: every line is something a
# brand-new plugin author can use.
_QUICKSTART = """\
PLUGIN AUTHORING QUICKSTART
============================

A plugin is three files in one folder:

    your-plugin-id/
      manifest.json     <- declares the plugin
      __init__.py       <- exports register(api)
      icon.svg          <- optional, shown in the marketplace

Drop that folder into the editor's user-plugins directory:

    Linux   : ~/.config/my-editor/plugins/
    macOS   : ~/Library/Application Support/my-editor/plugins/
    Windows : %APPDATA%/my-editor/plugins/

Restart the editor. Your action shows up in the Marketplace menu.


THE MANIFEST
------------

Minimum manifest.json:

    {
      "id":            "your-plugin-id",      // folder name; lowercase, no colons
      "name":          "My Plugin",           // human-facing title
      "version":       "1.0.0",
      "description":   "One-line tagline.",
      "author":        "Your Name",
      "license":       "MIT",
      "api_version":   2,
      "declared_kinds":        [],
      "declared_capabilities": []
    }

Useful optional fields:

    "long_description":     "<p>HTML for the marketplace detail page.</p>"
    "homepage":             "https://..."
    "tags":                 ["productivity", "writing"]
    "category":             "writing"
    "icon":                 "icon.svg"
    "lightning_address":    "you@your-domain.com"
    "price_sats":           0                // 0 = free; > 0 = paid


THE ENTRY POINT
---------------

In __init__.py, export a function named exactly `register`:

    from plugin_system import PluginAPI

    def register(api: PluginAPI) -> None:
        def hello():
            api.open_tab("Hello", "From my plugin.")
        api.add_menu_action("Marketplace", "Hello: Say Hi", hello)


WHAT YOU CAN CALL ON api  (PluginAPI v2)
----------------------------------------

UI:
    api.add_menu_action(menu, label, callback)
        Add a clickable item to the menu bar. The callback runs on
        click; exceptions are caught and shown in the status bar.

    api.add_dock_widget(title, widget_factory, area="right")
        Attach a side panel built by your plugin. The host calls the
        factory to build the QWidget. Requires the "dock_widget"
        capability in your manifest.

Active tab:
    api.get_current_text()           -> str or None
    api.set_current_text(text)       -> bool
    api.open_tab(title, content)

Per-plugin settings (sandboxed under your plugin id):
    api.get_setting(key, default)
    api.set_setting(key, value)

Lifecycle:
    api.register_unload(callback)
        Register a function to run when the plugin is unloaded. Use
        this to stop timers, join threads, close sockets. Callbacks
        fire in LIFO order before the host removes your widgets, so
        you can interact with your own UI one last time.

Nostr publish (gated by `declared_kinds` in the manifest):
    api.publish_event(template, on_published=..., on_failed=...)

Paid-install support (rare; only if you build a wallet plugin):
    api.register_payment_provider(provider)


CAPABILITY GATING
-----------------

Two manifest fields drive what the editor lets your plugin do.

`declared_kinds` is a list of Nostr event kinds you intend to publish:

    "declared_kinds": [1, 30023]

`declared_capabilities` is a list of feature areas you intend to use:

    "declared_capabilities": ["dock_widget", "network"]

Known capabilities today:

    dock_widget   Required to call api.add_dock_widget.
    network       Advisory. Python isn't sandboxed, so the editor
                  cannot stop a plugin from opening a socket; declare
                  this so users see "uses the network" in the install
                  dialog before they grant the install.

Unknown values in either list are rejected at load time so a typo
cannot turn into a silent capability.

A few Nostr kinds are blocked outright even if declared, because they
control identity, trust, or relay routing:

    0     kind:0 metadata          (would impersonate the user)
    3     NIP-02 follow list       (would rewrite their follows)
    5     NIP-09 deletion request  (use the editor's delete UI)
    10000 NIP-51 mute list         (use the marketplace's mute UI)
    10002 NIP-65 relay list        (managed by the editor)
    24133 NIP-46 bunker RPC        (signer transport, not user content)


DISTRIBUTION
------------

To list your plugin in the marketplace, publish a kind:30700 event to
the relays you write to. The plugin author tools (coming soon to the
editor; today via the EngagementPublisher API) sign and publish it
through your Nostr signer.

Listing tags the marketplace looks at:

    ["d",        "<your-plugin-id>"]
    ["name",     "<display name>"]
    ["version",  "1.0.0"]
    ["description", "<one-liner>"]
    ["summary",  "<long-form HTML>"]
    ["homepage", "https://..."]
    ["license",  "MIT"]
    ["download", "https://.../my-plugin.zip", "<sha256-hex>"]
    ["image",    "https://.../screenshot.png"]
    ["t",        "tag"]
    ["zap",      "you@your-domain.com"]
    ["price",    "1000"]               (only if paid)

Once published, anyone running this editor can find your plugin with
no central registry approval needed. Reviews, ratings, and zaps for
your plugin all anchor on that one event via NIP-22 / NIP-32 / NIP-57.


BEST PRACTICES
--------------

  *  Never import from the editor internals (main_window, nostr,
     editor, plugin_marketplace). The `api` object is the entire
     supported surface; everything else is private and will rename.

  *  Run heavy work lazily, not at import time. The loader imports
     all installed plugins before the window appears.

  *  Use api.get_setting / api.set_setting for persisted data; never
     write to global files. Settings are sandboxed per plugin and
     survive editor restarts.

  *  Pair every background resource with a register_unload callback.
     Timers, threads, file watchers, network sessions: anything that
     runs after register() returns must stop when the plugin is
     disabled. Otherwise it leaks until the editor restarts.

  *  Errors in your callback are isolated to your plugin. They
     surface as a status-bar message, not a crash.

  *  Test offline. The editor must work when your plugin's network
     dependencies are down.


GOING DEEPER
------------

Read the source of this plugin (bundled_plugins/hello_world/) for an
annotated working example that uses all three v2 hooks together. The
full reference is in the project's PLUGIN_DEVELOPMENT.md.

Happy hacking.
"""
