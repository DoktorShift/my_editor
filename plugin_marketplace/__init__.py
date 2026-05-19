"""Marketplace for discovering and installing plugins.

This package separates marketplace concerns from the lower-level
plugin runtime in ``plugin_system``:

  - ``registry``   - fetches the plugin index from a remote URL
                     (or a local file) and parses it into typed
                     records.
  - ``installer``  - downloads, verifies, and extracts plugin
                     packages into the user-plugins directory.
  - ``log_viewer`` - dialog that tails ``plugin.log`` for users who
                     run the editor as a bundled GUI app with no
                     terminal access.
  - ``ui``         - the master-detail marketplace window.

The package never reaches into editor internals - it talks to the
plugin runtime through ``plugin_system`` and to the main window
through a small ``MarketplaceHost`` protocol.
"""

from .models import PluginListing, RegistrySource

__all__ = [
    "PluginListing",
    "RegistrySource",
]
