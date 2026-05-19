"""Adapter that bridges the PluginAPI surface to a Qt main window.

Lives here, not in ``main_window.py``, so the bookkeeping is testable
without spinning up a window and so the main-window file stays focused
on editor concerns.

The host adapter is responsible for:

  - Translating ``HostHooks`` calls into concrete UI actions
    (creating QActions, inserting tabs, etc.).
  - Tracking everything a plugin contributes - its QActions, its
    payment provider, its sys.path entry, its sys.modules entry - so
    that ``unload(plugin_id)`` can retract those side effects
    cleanly. Without that bookkeeping, "Disable" and "Uninstall" in
    the marketplace would silently lie until restart.

The adapter is intentionally narrow: plugins never get a reference
to the main window, only to a PluginAPI that funnels everything
through this object.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

from .api import PaymentProvider
from .loader import PluginInfo, plugin_module_name


# --------------------------------------------------------------------------- #
# Editor-facing protocol - the slice of MainWindow the adapter touches        #
# --------------------------------------------------------------------------- #

class EditorBackend(Protocol):
    """The host-side interface the adapter calls into.

    MainWindow implements this; the adapter never holds a reference
    to a QMainWindow type directly so plugin-system tests stay
    Qt-optional.
    """

    def editor_create_menu_action(
        self,
        menu_title: str,
        label: str,
        callback: Callable[[], None],
    ) -> object:
        """Create + insert a menu action; return an opaque handle the
        adapter will pass back to ``editor_remove_menu_action`` on unload.
        """

    def editor_remove_menu_action(self, handle: object) -> None: ...

    def editor_create_dock_widget(
        self,
        title: str,
        widget: object,
        area: str,
    ) -> object:
        """Insert ``widget`` as a side panel and return an opaque handle.

        ``area`` is one of ``"left"`` / ``"right"``. The handle is what
        the adapter hands back to :meth:`editor_remove_dock_widget` at
        unload time. Implementations should reparent the widget so
        ownership tracks the editor's central layout.
        """

    def editor_remove_dock_widget(self, handle: object) -> None: ...

    def editor_get_current_text(self) -> Optional[str]: ...

    def editor_set_current_text(self, text: str) -> bool: ...

    def editor_open_tab(self, title: str, content: str) -> None: ...

    def editor_show_status(self, message: str, timeout_ms: int = 4000) -> None: ...

    def editor_publish_event(
        self,
        plugin_id: str,
        template: dict,
        *,
        on_success: Callable[[dict], None],
        on_failure: Callable[[str], None],
    ) -> None:
        """Sign + publish a Nostr event for a plugin.

        Implementations resolve the active profile, sign through the
        NIP-46 bunker session pool, and publish via the marketplace's
        outbox router. Failures (no signer, bunker rejected, no relay
        accepted) flow through ``on_failure`` so the plugin sees a
        single async error channel.

        Implementations MAY refuse the call (e.g. when no profile is
        connected) by invoking ``on_failure`` synchronously with a
        human-readable reason; the contract is that exactly one
        callback fires for each call.
        """


# --------------------------------------------------------------------------- #
# Bookkeeping records                                                         #
# --------------------------------------------------------------------------- #

@dataclass
class _PluginContrib:
    """Everything a single plugin has contributed at runtime.

    Stored per ``plugin_id`` so ``unload(plugin_id)`` can retract the
    contributions exactly.
    """

    folder: Path
    action_handles: List[object] = field(default_factory=list)
    dock_handles: List[object] = field(default_factory=list)
    unload_callbacks: List[Callable[[], None]] = field(default_factory=list)
    provider: Optional[PaymentProvider] = None
    vendor_path: Optional[str] = None
    module_name: Optional[str] = None


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #

class PluginHost:
    """The HostHooks implementation handed to every PluginAPI.

    Hold one instance per editor process. Methods are designed to be
    called from the UI thread; nothing here is thread-safe.
    """

    def __init__(self, backend: EditorBackend) -> None:
        self._backend = backend
        self._contribs: Dict[str, _PluginContrib] = {}
        # Insertion-ordered list mirrors ``_contribs`` for cases like
        # the marketplace asking "which providers are registered, in
        # the order they were loaded?". A separate list avoids
        # re-walking the dict every time.
        self._provider_order: List[str] = []

    # ----------------------------------------------------------------------
    # Lifecycle: called by the loader before/after each plugin registers
    # ----------------------------------------------------------------------

    def begin_plugin(self, info: PluginInfo) -> None:
        """Open a bookkeeping slot for a plugin about to register.

        Idempotent: re-registering an already-loaded plugin (e.g. a
        marketplace update) replaces the previous slot's payment
        provider and reuses the actions slot, which the caller is
        expected to have already cleared via :meth:`unload`.
        """
        contrib = self._contribs.setdefault(info.plugin_id, _PluginContrib(folder=info.folder))
        contrib.folder = info.folder
        contrib.module_name = plugin_module_name(info.plugin_id)

    def record_vendor_path(self, plugin_id: str, vendor_path: Optional[str]) -> None:
        """Tell the host about a sys.path entry it should undo on unload."""
        if vendor_path is None:
            return
        contrib = self._contribs.get(plugin_id)
        if contrib is not None:
            contrib.vendor_path = vendor_path

    # ----------------------------------------------------------------------
    # HostHooks implementation
    # ----------------------------------------------------------------------

    def host_add_menu_action(
        self,
        plugin_id: str,
        menu_title: str,
        label: str,
        callback: Callable[[], None],
    ) -> None:
        # The action callback is wrapped so a plugin bug surfaces as a
        # status-bar message instead of an unhandled exception.
        def _safe_callback() -> None:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - isolate per plugin
                self._backend.editor_show_status(
                    f"Plugin '{plugin_id}' action failed: {exc}", 6000,
                )

        handle = self._backend.editor_create_menu_action(
            menu_title, label, _safe_callback,
        )
        contrib = self._contribs.setdefault(plugin_id, _PluginContrib(folder=Path()))
        contrib.action_handles.append(handle)

    def host_get_current_text(self) -> Optional[str]:
        return self._backend.editor_get_current_text()

    def host_set_current_text(self, text: str) -> bool:
        return self._backend.editor_set_current_text(text)

    def host_open_tab(self, title: str, content: str) -> None:
        self._backend.editor_open_tab(title, content)

    def host_register_payment_provider(
        self,
        plugin_id: str,
        provider: PaymentProvider,
    ) -> None:
        contrib = self._contribs.setdefault(plugin_id, _PluginContrib(folder=Path()))
        contrib.provider = provider
        # Maintain insertion order without duplicates.
        if plugin_id in self._provider_order:
            self._provider_order.remove(plugin_id)
        self._provider_order.append(plugin_id)

    def host_register_unload(
        self,
        plugin_id: str,
        callback: Callable[[], None],
    ) -> None:
        contrib = self._contribs.setdefault(plugin_id, _PluginContrib(folder=Path()))
        contrib.unload_callbacks.append(callback)

    def host_add_dock_widget(
        self,
        plugin_id: str,
        title: str,
        widget_factory: Callable[[], object],
        area: str,
    ) -> None:
        # Construction is delegated to the host so all Qt parentage
        # happens on the UI thread, and so a plugin bug in the factory
        # surfaces as a status-bar message rather than crashing the
        # editor mid-register.
        try:
            widget = widget_factory()
        except Exception as exc:  # noqa: BLE001 - isolate per plugin
            self._backend.editor_show_status(
                f"Plugin '{plugin_id}' dock widget failed to build: {exc}", 6000,
            )
            return
        handle = self._backend.editor_create_dock_widget(title, widget, area)
        contrib = self._contribs.setdefault(plugin_id, _PluginContrib(folder=Path()))
        contrib.dock_handles.append(handle)

    def host_publish_event(
        self,
        plugin_id: str,
        template: dict,
        *,
        on_success: Callable[[dict], None],
        on_failure: Callable[[str], None],
    ) -> None:
        """Route a plugin's publish_event to the editor backend.

        The capability gate (``declared_kinds`` check) ran inside
        :class:`PluginAPI`; the adapter only adds bookkeeping in case
        we want to track per-plugin publish quotas later.
        """
        publish = getattr(self._backend, "editor_publish_event", None)
        if publish is None:
            on_failure(
                "this build does not expose publish_event to plugins"
            )
            return
        publish(plugin_id, template, on_success=on_success, on_failure=on_failure)

    # ----------------------------------------------------------------------
    # Inspection
    # ----------------------------------------------------------------------

    def loaded_plugin_ids(self) -> List[str]:
        """Return ids of plugins currently holding a bookkeeping slot."""
        return list(self._contribs.keys())

    def payment_providers(self) -> List[tuple[str, PaymentProvider]]:
        """Return registered providers in registration order.

        First-ready-wins is decided by the caller; the adapter just
        reports who's registered.
        """
        out: List[tuple[str, PaymentProvider]] = []
        for plugin_id in self._provider_order:
            contrib = self._contribs.get(plugin_id)
            if contrib is None or contrib.provider is None:
                continue
            out.append((plugin_id, contrib.provider))
        return out

    # ----------------------------------------------------------------------
    # Unload
    # ----------------------------------------------------------------------

    def unload(self, plugin_id: str) -> bool:
        """Retract every side effect a plugin contributed.

        Returns True if a plugin was unloaded, False if the id wasn't
        known. After this call:

          - Every callback registered via ``api.register_unload`` has
            been invoked (LIFO order). This runs first so the plugin
            can interact with its own widgets one last time.
          - Every QAction the plugin added is removed from its menu.
          - Every dock widget the plugin added is removed and deleted.
          - The plugin's payment provider is dropped.
          - The plugin's sys.path entry (if any) is removed.
          - The plugin's sys.modules entry is removed so the next
            registration re-imports the module fresh.

        Settings are NOT touched: "disable" and "update" both call
        ``unload`` and the user's saved preferences should survive.
        Uninstall is a separate marketplace flow that clears settings
        via ``PluginSettingsStore.clear_plugin``.
        """
        contrib = self._contribs.pop(plugin_id, None)
        if contrib is None:
            return False

        # Run plugin-side teardown first. LIFO mirrors typical resource
        # nesting (timers stop before the widget they update is torn
        # down). One bad callback never blocks the rest.
        for callback in reversed(contrib.unload_callbacks):
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - isolate per plugin
                self._backend.editor_show_status(
                    f"Plugin '{plugin_id}' unload callback failed: {exc}", 6000,
                )

        for handle in contrib.action_handles:
            try:
                self._backend.editor_remove_menu_action(handle)
            except Exception:  # noqa: BLE001
                # Best effort - a backend bug here mustn't leak a
                # plugin slot into the next load attempt.
                pass

        for handle in contrib.dock_handles:
            try:
                self._backend.editor_remove_dock_widget(handle)
            except Exception:  # noqa: BLE001
                pass

        if plugin_id in self._provider_order:
            self._provider_order.remove(plugin_id)

        if contrib.vendor_path is not None:
            try:
                sys.path.remove(contrib.vendor_path)
            except ValueError:
                pass

        if contrib.module_name is not None:
            sys.modules.pop(contrib.module_name, None)

        return True

    def unload_all(self) -> None:
        """Unload every plugin (used at shutdown / full reload)."""
        for plugin_id in list(self._contribs.keys()):
            self.unload(plugin_id)
