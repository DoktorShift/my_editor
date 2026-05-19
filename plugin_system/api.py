"""The PluginAPI surface - the only edge a plugin is allowed to touch.

Design rules:

  - **Typed, not leaky**. Methods return strings / bools / dicts and
    accept callbacks. They never hand a plugin a raw editor widget,
    QMainWindow, or BunkerClient. That keeps internal refactors from
    breaking every plugin in the marketplace.
  - **Capability-scoped**. Anything a plugin does on the user's
    behalf (publish a Nostr event, register a payment provider) is
    gated by what the plugin declared in its ``manifest.json``. The
    loader refuses to load a plugin whose declarations don't match
    what it actually tries to do at runtime.
  - **Versioned**. ``PluginAPI.VERSION`` is the integer the editor
    increments whenever the surface changes in a breaking way. The
    loader skips plugins whose ``api_version`` exceeds it.

This module deliberately depends on PySide6 only for type hints. The
host (MainWindow) builds a concrete ``HostHooks`` adapter and the API
calls into that, so plugins never see Qt internals leaking out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Set,
)

if TYPE_CHECKING:
    from .settings import PluginSettingsScope
    from PySide6.QtWidgets import QWidget


# --------------------------------------------------------------------------- #
# Public constants                                                            #
# --------------------------------------------------------------------------- #

# Increment whenever PluginAPI gains a method or changes the meaning
# of an existing one. The loader refuses plugins whose
# ``manifest["api_version"]`` is greater than this number.
PLUGIN_API_VERSION: int = 2


# The closed catalog of strings a plugin may put in
# ``manifest["declared_capabilities"]``. Two-tier:
#
#   - *Enforced* capabilities gate a runtime API. Calling the gated API
#     without declaring the capability raises ``PluginPermissionError``.
#     Today: ``dock_widget`` gates :meth:`PluginAPI.add_dock_widget`.
#   - *Advisory* capabilities are informational. The loader records them
#     so the install dialog can show the user "this plugin uses the
#     network" before they click Install. Python isn't sandboxed, so we
#     can't actually stop a plugin from opening a socket; the disclosure
#     is the value. Today: ``network``.
#
# Unknown strings are rejected at load time so a typo can't turn into a
# silent capability that nothing renders or enforces.
ENFORCED_CAPABILITIES: frozenset[str] = frozenset({"dock_widget"})
ADVISORY_CAPABILITIES: frozenset[str] = frozenset({"network"})
KNOWN_CAPABILITIES: frozenset[str] = ENFORCED_CAPABILITIES | ADVISORY_CAPABILITIES


# Human-readable labels for the install dialog. Keep these short and
# user-facing; they appear next to the plugin's name on the install
# screen so the user can decide whether to grant.
CAPABILITY_LABELS: dict[str, str] = {
    "dock_widget": "Adds a side panel",
    "network": "Uses the network",
}


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #

class PluginPermissionError(RuntimeError):
    """Raised when a plugin tries to do something it didn't declare in
    its manifest (e.g. publish a Nostr kind it didn't list under
    ``declared_kinds``)."""


# --------------------------------------------------------------------------- #
# PaymentProvider - capability a plugin registers to handle payments          #
# --------------------------------------------------------------------------- #

class PaymentProvider(Protocol):
    """Implemented by a plugin that can pay bolt11 invoices.

    The marketplace asks every registered provider in priority order.
    The first that returns ``is_ready() == True`` handles the payment.
    When none are ready, the marketplace falls back to a QR-code
    invoice for manual payment.
    """

    name: str  # "NWC", "Cashu", ...

    def is_ready(self) -> bool:
        """Return True when the provider can pay right now (wallet
        connected, network reachable, etc.)."""

    def pay_invoice(
        self,
        bolt11: str,
        sats: int,
        on_success: Callable[[str], None],
        on_failure: Callable[[str], None],
    ) -> None:
        """Pay ``bolt11`` (size ``sats``). Async, signal-style.

        On success, calls ``on_success(preimage_hex)``. On failure,
        calls ``on_failure(reason)`` with a human-readable message.
        """


# --------------------------------------------------------------------------- #
# HostHooks - the editor's side of the API, supplied by MainWindow            #
# --------------------------------------------------------------------------- #

class HostHooks(Protocol):
    """Adapter between PluginAPI and the editor's internals.

    MainWindow builds one of these at startup and hands the same
    instance to every PluginAPI it constructs. Keeping the adapter
    explicit means tests can stub it without spinning up Qt.

    Hooks that attach resources to a plugin (menu actions, payment
    providers) take ``plugin_id`` so the host can track those
    resources for unload. Hooks that perform stateless edits on the
    active document do not - they have no per-plugin lifetime.
    """

    def host_add_menu_action(
        self,
        plugin_id: str,
        menu_title: str,
        label: str,
        callback: Callable[[], None],
    ) -> None: ...

    def host_get_current_text(self) -> Optional[str]: ...

    def host_set_current_text(self, text: str) -> bool: ...

    def host_open_tab(self, title: str, content: str) -> None: ...

    def host_register_payment_provider(
        self,
        plugin_id: str,
        provider: PaymentProvider,
    ) -> None: ...

    def host_register_unload(
        self,
        plugin_id: str,
        callback: Callable[[], None],
    ) -> None:
        """Record a plugin-supplied teardown callback.

        The host runs every recorded callback (LIFO) before retracting
        the plugin's UI contributions. Exceptions in one callback are
        logged and do not block the rest.
        """

    def host_add_dock_widget(
        self,
        plugin_id: str,
        title: str,
        widget_factory: Callable[[], "QWidget"],
        area: str,
    ) -> None:
        """Build and attach a plugin dock widget.

        ``widget_factory`` is called by the host (the host owns Qt
        construction). ``area`` is one of ``"left"`` / ``"right"``. The
        host tracks the resulting widget for unload.
        """

    def host_publish_event(
        self,
        plugin_id: str,
        template: dict,
        *,
        on_success: Callable[[dict], None],
        on_failure: Callable[[str], None],
    ) -> None:
        """Sign + publish a Nostr event on behalf of a plugin.

        ``template`` is an unsigned event dict (no ``id`` / ``sig`` /
        ``pubkey`` yet). The host fills in ``pubkey`` from the active
        signer profile, signs through the NIP-46 bunker, and publishes
        through the marketplace's outbox router.

        The host is also responsible for enforcing ``declared_kinds``:
        a plugin may only publish kinds it declared in its manifest.
        That check happens before we even ask the bunker, so the
        signer never sees an attempt to expand the plugin's privileges.
        """
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# PluginInfo passed at construction time                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PluginIdentity:
    """The slice of manifest data the PluginAPI cares about at runtime.

    Loader hands one of these to ``PluginAPI`` so the API can enforce
    capability scoping without holding a reference to the entire
    manifest dict.
    """

    plugin_id: str
    name: str
    version: str
    declared_kinds: frozenset[int]
    declared_capabilities: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- #
# PluginAPI                                                                   #
# --------------------------------------------------------------------------- #

class PluginAPI:
    """The handle each plugin receives in ``register(api)``.

    Methods are designed to be safe to call at any point after
    ``register()`` returns. Calls made *inside* ``register()`` (e.g.
    ``add_menu_action(...)``) are common and supported.
    """

    VERSION: int = PLUGIN_API_VERSION

    def __init__(
        self,
        *,
        identity: PluginIdentity,
        host: HostHooks,
        settings_scope: "PluginSettingsScope",
    ) -> None:
        self._identity = identity
        self._host = host
        self._settings = settings_scope

    # -- identity (read-only) ----------------------------------------------

    @property
    def plugin_id(self) -> str:
        return self._identity.plugin_id

    @property
    def name(self) -> str:
        return self._identity.name

    @property
    def version(self) -> str:
        return self._identity.version

    # -- menus / UI hooks --------------------------------------------------

    def add_menu_action(
        self,
        menu: str,
        label: str,
        callback: Callable[[], None],
    ) -> None:
        """Add a clickable item to the menu bar.

        ``menu`` is the top-level menu title. If the menu doesn't
        exist yet the host creates it. The Plugins menu is the
        suggested home for plugin entries.

        ``label`` is the human-readable action text.

        ``callback`` is invoked on click. Exceptions inside it are
        caught and logged so a bug in one plugin can't crash the
        editor.
        """
        if not isinstance(menu, str) or not menu.strip():
            raise ValueError("menu must be a non-empty string")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("label must be a non-empty string")
        if not callable(callback):
            raise TypeError("callback must be callable")
        # Pass plugin_id so the host can track this action for unload.
        # Without that bookkeeping, uninstall/disable can't retract
        # the menu entry without a full editor restart.
        self._host.host_add_menu_action(
            self.plugin_id, menu.strip(), label.strip(), callback,
        )

    def add_dock_widget(
        self,
        title: str,
        widget_factory: Callable[[], "QWidget"],
        *,
        area: str = "right",
    ) -> None:
        """Attach a side panel built by this plugin.

        ``title`` is the human-readable name of the panel (used in
        diagnostics and as the splitter handle tooltip).

        ``widget_factory`` is a zero-arg callable that returns a fresh
        ``QWidget``. The host calls it; the plugin keeps no Qt
        construction in its register-path until the host asks for it.

        ``area`` is ``"left"`` or ``"right"``. The widget sits inside
        the editor's central splitter alongside the editor area and the
        drafts panel.

        Capability: requires ``"dock_widget"`` in the manifest's
        ``declared_capabilities``. Without that the call raises
        :class:`PluginPermissionError`, surfaced at register time.

        The host removes and deletes the widget when the plugin is
        unloaded, so the plugin does not need to clean it up manually.
        Long-lived background work the plugin runs alongside the widget
        (timers, threads) should be torn down via
        :meth:`register_unload`.
        """
        if "dock_widget" not in self._identity.declared_capabilities:
            raise PluginPermissionError(
                f"plugin {self.plugin_id!r} called add_dock_widget without "
                f"declaring the 'dock_widget' capability in its manifest. "
                f"Add \"declared_capabilities\": [\"dock_widget\"] to "
                f"manifest.json and reload the plugin."
            )
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not callable(widget_factory):
            raise TypeError("widget_factory must be callable")
        if area not in ("left", "right"):
            raise ValueError(f"area must be 'left' or 'right', got {area!r}")
        self._host.host_add_dock_widget(
            self.plugin_id, title.strip(), widget_factory, area,
        )

    def register_unload(self, callback: Callable[[], None]) -> None:
        """Register a function to run when this plugin is unloaded.

        Use this for anything the plugin owns that the host can't see:
        background threads, ``QTimer`` instances, open sockets, file
        watchers. The host calls every registered callback (LIFO, most
        recently registered first) before tearing down the plugin's
        menu actions and dock widgets, so the callback can safely
        interact with those.

        Exceptions inside a callback are logged but do not block the
        rest. Callbacks that need to wait (e.g. ``thread.join``) block
        the unload; keep them fast.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._host.host_register_unload(self.plugin_id, callback)

    # -- editor text -------------------------------------------------------

    def get_current_text(self) -> Optional[str]:
        """Return the plain-text contents of the active tab, or ``None``
        if no editor tab is open."""
        return self._host.host_get_current_text()

    def set_current_text(self, text: str) -> bool:
        """Replace the active tab's text with ``text``. Returns True on
        success, False when there is no editor to write to."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return self._host.host_set_current_text(text)

    def open_tab(self, title: str, content: str) -> None:
        """Open a new tab populated with ``content``.

        ``title`` becomes the tab label. The plugin doesn't choose the
        backing file (the tab is created without a file path); the
        user can Save-As to keep it.
        """
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        self._host.host_open_tab(title, content)

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Read a value from this plugin's scoped settings."""
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        """Persist a value in this plugin's scoped settings."""
        self._settings.set(key, value)

    # -- capability registration ------------------------------------------

    # -- Nostr publish ----------------------------------------------------

    def publish_event(
        self,
        template: dict,
        *,
        on_published: Optional[Callable[[dict], None]] = None,
        on_failed: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Publish a Nostr event from the plugin.

        ``template`` is a partial NIP-01 event dict - at minimum it
        must carry ``kind`` (int), ``content`` (str), and ``tags``
        (list-of-lists). The host fills in ``pubkey`` / ``created_at``
        / ``id`` / ``sig`` from the active signer profile and routes
        the publish through the marketplace's outbox router.

        Capability gating layers, in order:

          1. ``kind`` must be a NIP-01 valid integer (0..65535).
          2. ``kind`` must appear in the manifest's ``declared_kinds``.
          3. ``kind`` must NOT be on the host's destructive blocklist
             (identity / trust / deletion kinds that would let a
             plugin silently impersonate or sabotage the user's
             profile even with explicit declaration).
          4. Every ``p`` / ``e`` tag value must be valid 64-char hex
             so a malformed tag can't be smuggled into a relay's
             index and confuse downstream consumers.

        Returns nothing. Errors of intent (programming mistakes or
        manifest gaps) raise synchronously. Errors of effect (signer
        offline, relay NACK) flow through ``on_failed``.
        """
        if not isinstance(template, dict):
            raise TypeError("template must be a dict")
        kind = template.get("kind")
        if not isinstance(kind, int) or isinstance(kind, bool):
            raise ValueError("template['kind'] must be an int")
        if kind < 0 or kind > 65535:
            raise ValueError(
                f"template['kind'] must be in the NIP-01 range 0..65535, got {kind}"
            )
        if kind in _BLOCKED_PUBLISH_KINDS:
            raise ValueError(
                f"plugins cannot publish kind {kind}: this kind controls the "
                f"user's identity, follow graph, mute list, or relay routing "
                f"and is reserved for the editor. Use a dedicated PluginAPI "
                f"surface if you need this behaviour."
            )
        if kind not in self._identity.declared_kinds:
            raise ValueError(
                f"plugin {self.plugin_id!r} did not declare kind {kind} in "
                f"its manifest's declared_kinds. Add the kind to manifest.json "
                f"and reload the plugin."
            )
        content = template.get("content", "")
        if not isinstance(content, str):
            raise TypeError("template['content'] must be a string")
        tags = template.get("tags", [])
        if not isinstance(tags, list):
            raise TypeError("template['tags'] must be a list of tag arrays")
        for tag in tags:
            if not isinstance(tag, list) or not all(isinstance(v, str) for v in tag):
                raise TypeError("each tag must be a list of strings")
            # Tag-value sanity. ``p`` / ``e`` values are 32-byte hex
            # per NIP-01; let through-quality fall to relays for other
            # tag namespaces (they may carry arbitrary strings).
            if tag and tag[0] in ("p", "e") and len(tag) >= 2:
                if not _is_hex32(tag[1]):
                    raise ValueError(
                        f"tag {tag[0]!r} requires a 64-char lowercase hex value, "
                        f"got {tag[1]!r}"
                    )

        def _noop_ok(_event: dict) -> None:
            return

        def _noop_fail(_reason: str) -> None:
            return

        ok = on_published or _noop_ok
        fail = on_failed or _noop_fail
        self._host.host_publish_event(
            self.plugin_id, dict(template), on_success=ok, on_failure=fail,
        )

    def register_payment_provider(self, provider: PaymentProvider) -> None:
        """Tell the marketplace that this plugin can pay invoices.

        The marketplace will call ``provider.is_ready()`` whenever the
        user clicks Install on a paid plugin. The first ready provider
        wins. When no provider is ready the QR-code fallback handles
        the payment.
        """
        # Duck-type the whole protocol, including ``name`` - without it
        # the marketplace UI can't render "Pay with …" and would crash
        # at install time instead of at register time.
        missing = [
            attr for attr in ("name", "is_ready", "pay_invoice")
            if not hasattr(provider, attr)
        ]
        if missing:
            raise TypeError(
                "provider must implement PaymentProvider "
                f"(missing: {', '.join(missing)})"
            )
        if not isinstance(provider.name, str) or not provider.name.strip():
            raise TypeError("provider.name must be a non-empty string")
        if not callable(provider.is_ready) or not callable(provider.pay_invoice):
            raise TypeError("provider.is_ready and pay_invoice must be callable")
        self._host.host_register_payment_provider(self.plugin_id, provider)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

# Nostr NIP-01 event kinds are unsigned 16-bit integers. Any value
# outside this range is meaningless on the protocol and almost
# certainly a manifest typo. Catching it at parse time gives the
# plugin author a clear error message instead of a silent no-op when
# they later try to publish.
_NOSTR_KIND_MIN: int = 0
_NOSTR_KIND_MAX: int = 65535


# Kinds plugins MUST NOT publish through ``publish_event``, even when
# explicitly declared. These control the user's identity, social
# graph, mute list, and relay routing. Letting a plugin overwrite
# them silently would be impersonation; expressly opting in via the
# manifest would still be a footgun. The editor's own UI is the only
# legitimate surface for these.
#
# NIP-01: kind 0 metadata; NIP-02: kind 3 follow list; NIP-09: kind 5
# deletion (use ``EngagementPublisher.delete`` instead); NIP-46:
# kind 24133 bunker RPC; NIP-65: kind 10002 relay list; NIP-51:
# kind 10000 mute list.
_BLOCKED_PUBLISH_KINDS: frozenset[int] = frozenset({
    0,
    3,
    5,
    10000,
    10002,
    24133,
})


def _is_hex32(value: str) -> bool:
    """``True`` when ``value`` is exactly 64 lowercase hex chars.

    NIP-01 pubkeys and event ids are 32-byte (256-bit) values encoded
    as lowercase hex. The publish surface forbids uppercase / partial
    hex so a malformed tag never reaches a relay's index.
    """
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def parse_declared_capabilities(raw: Any) -> frozenset[str]:
    """Coerce a manifest's ``declared_capabilities`` list into a typed set.

    Tolerates a missing key (returns empty). Raises ``ValueError`` for:

      - non-list values (so a typo like ``"dock_widget"`` instead of a
        one-element list fails loud at load time),
      - entries that are not strings,
      - entries not in :data:`KNOWN_CAPABILITIES` (a closed catalog
        protects the user from a plugin asking for a permission the
        editor doesn't understand and silently ignoring it).
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ValueError("declared_capabilities must be a list of strings")
    out: Set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(
                f"declared_capabilities entries must be strings, got {item!r}"
            )
        token = item.strip()
        if token not in KNOWN_CAPABILITIES:
            known = ", ".join(sorted(KNOWN_CAPABILITIES))
            raise ValueError(
                f"declared_capabilities entry {item!r} is not recognised; "
                f"known capabilities are: {known}"
            )
        out.add(token)
    return frozenset(out)


def parse_declared_kinds(raw: Any) -> frozenset[int]:
    """Coerce a manifest's ``declared_kinds`` list into a typed set.

    Tolerates ints, str-encoded ints, and a missing key. Anything else
    raises so the loader can report a clear manifest error before the
    plugin is imported.

    Each kind must fall inside the NIP-01 0..65535 range; values outside
    are rejected so a plugin can't accidentally declare an unreachable
    capability.
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ValueError("declared_kinds must be a list of integers")
    out: Set[int] = set()
    for item in raw:
        if isinstance(item, bool):
            # ``bool`` is a subclass of int; guard explicitly so
            # ``True/False`` doesn't sneak in as kind 1 / 0.
            raise ValueError(f"declared_kinds entries must be ints, got {item!r}")
        if isinstance(item, int):
            value = item
        elif isinstance(item, str) and item.isdigit():
            value = int(item)
        else:
            raise ValueError(f"declared_kinds entries must be ints, got {item!r}")
        if value < _NOSTR_KIND_MIN or value > _NOSTR_KIND_MAX:
            raise ValueError(
                f"declared_kinds entry {value} is outside the Nostr range "
                f"({_NOSTR_KIND_MIN}..{_NOSTR_KIND_MAX})"
            )
        out.add(value)
    return frozenset(out)
