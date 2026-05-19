"""Plugin system for my-editor.

A plugin is a folder containing a ``manifest.json`` and an
``__init__.py`` that exports ``register(api: PluginAPI) -> None``.
The editor loads plugins at startup from two locations:

  - ``bundled_plugins/``  next to the editor's source, shipped with
                          the install
  - ``~/.config/my_editor/plugins/`` (or the per-platform equivalent)
                          for plugins the user installs from the
                          marketplace

All public types of the plugin system live in this package. Plugins
should only ever import from ``plugin_system``; touching internal
editor modules directly is unsupported and will break across editor
releases.
"""

from .api import (
    ADVISORY_CAPABILITIES,
    CAPABILITY_LABELS,
    ENFORCED_CAPABILITIES,
    KNOWN_CAPABILITIES,
    PLUGIN_API_VERSION,
    HostHooks,
    PaymentProvider,
    PluginAPI,
    PluginIdentity,
    PluginPermissionError,
    parse_declared_capabilities,
    parse_declared_kinds,
)
from .loader import (
    LoadReport,
    PluginInfo,
    PluginLoadError,
    PluginSkipped,
    SKIP_DISABLED,
    SKIP_NO_MANIFEST,
    SKIP_SHADOWED,
    SKIP_WRONG_PLATFORM,
    load_all_plugins,
    load_one_plugin_folder,
    plugin_module_name,
    read_plugin_manifest,
)
from .paths import (
    APP_FOLDER,
    bundled_plugins_dir,
    plugin_log_path,
    plugin_settings_path,
    user_config_root,
    user_plugins_dir,
)
from .host_adapter import EditorBackend, PluginHost
from .settings import PluginSettingsScope, PluginSettingsStore

__all__ = [
    # api
    "ADVISORY_CAPABILITIES",
    "CAPABILITY_LABELS",
    "ENFORCED_CAPABILITIES",
    "KNOWN_CAPABILITIES",
    "PLUGIN_API_VERSION",
    "HostHooks",
    "PaymentProvider",
    "PluginAPI",
    "PluginIdentity",
    "PluginPermissionError",
    "parse_declared_capabilities",
    "parse_declared_kinds",
    # loader
    "LoadReport",
    "PluginInfo",
    "PluginLoadError",
    "PluginSkipped",
    "SKIP_DISABLED",
    "SKIP_NO_MANIFEST",
    "SKIP_SHADOWED",
    "SKIP_WRONG_PLATFORM",
    "load_all_plugins",
    "load_one_plugin_folder",
    "plugin_module_name",
    "read_plugin_manifest",
    # paths
    "APP_FOLDER",
    "bundled_plugins_dir",
    "plugin_log_path",
    "plugin_settings_path",
    "user_config_root",
    "user_plugins_dir",
    # host adapter
    "EditorBackend",
    "PluginHost",
    # settings
    "PluginSettingsScope",
    "PluginSettingsStore",
]
