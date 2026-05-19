"""Tests for ``PluginAPI.publish_event`` declared_kinds gating."""

from __future__ import annotations

from typing import Callable, List

import pytest

from plugin_system.api import (
    HostHooks,
    PaymentProvider,
    PluginAPI,
    PluginIdentity,
)


class _CapturingHost:
    """Stub host that records publish_event calls without doing I/O."""

    def __init__(self) -> None:
        self.published: List[dict] = []
        self.last_callbacks = None

    # ---- only the methods PluginAPI actually invokes are implemented ----
    def host_add_menu_action(self, *_args, **_kwargs) -> None:
        return

    def host_get_current_text(self):
        return ""

    def host_set_current_text(self, _text: str) -> bool:
        return True

    def host_open_tab(self, *_a, **_kw) -> None:
        return

    def host_register_payment_provider(self, *_a, **_kw) -> None:
        return

    def host_publish_event(
        self,
        plugin_id: str,
        template: dict,
        *,
        on_success,
        on_failure,
    ) -> None:
        self.published.append(template)
        self.last_callbacks = (on_success, on_failure)


class _StubSettings:
    """Minimal settings scope so PluginAPI construction succeeds."""

    def get(self, _key, default=None):
        return default

    def set(self, _key, _value) -> None:
        return


def _api(declared_kinds=frozenset({1111, 1985})) -> PluginAPI:
    identity = PluginIdentity(
        plugin_id="hello", name="Hello", version="1.0.0",
        declared_kinds=declared_kinds,
    )
    return PluginAPI(
        identity=identity, host=_CapturingHost(), settings_scope=_StubSettings(),
    )


# ──────────────────────────────────────────────────────────────────────
# Gating

def test_publish_event_rejects_undeclared_kind():
    api = _api(declared_kinds=frozenset({1111}))
    with pytest.raises(ValueError) as excinfo:
        api.publish_event({"kind": 1, "content": "hi", "tags": []})
    assert "declared_kinds" in str(excinfo.value)


def test_publish_event_accepts_declared_kind():
    api = _api(declared_kinds=frozenset({1, 1111}))
    api.publish_event({"kind": 1, "content": "hi", "tags": []})
    host = api._host
    assert len(host.published) == 1
    assert host.published[0]["kind"] == 1


# ──────────────────────────────────────────────────────────────────────
# Template validation

def test_publish_event_rejects_non_dict_template():
    api = _api()
    with pytest.raises(TypeError):
        api.publish_event("not a dict")


def test_publish_event_rejects_missing_kind():
    api = _api()
    with pytest.raises(ValueError):
        api.publish_event({"content": "", "tags": []})


def test_publish_event_rejects_non_int_kind():
    api = _api()
    with pytest.raises(ValueError):
        api.publish_event({"kind": "1985", "content": "", "tags": []})


def test_publish_event_rejects_non_string_content():
    api = _api()
    with pytest.raises(TypeError):
        api.publish_event({"kind": 1111, "content": 42, "tags": []})


def test_publish_event_rejects_non_list_tags():
    api = _api()
    with pytest.raises(TypeError):
        api.publish_event({"kind": 1111, "content": "", "tags": "not a list"})


def test_publish_event_rejects_malformed_tag_entries():
    api = _api()
    with pytest.raises(TypeError):
        api.publish_event({"kind": 1111, "content": "", "tags": [["e", 42]]})


# ──────────────────────────────────────────────────────────────────────
# Callback wiring

def test_publish_event_passes_callbacks_through():
    api = _api()
    calls: List[str] = []
    api.publish_event(
        {"kind": 1111, "content": "", "tags": []},
        on_published=lambda _e: calls.append("ok"),
        on_failed=lambda _r: calls.append("fail"),
    )
    on_ok, on_fail = api._host.last_callbacks  # type: ignore[attr-defined]
    on_ok({"id": "x"})
    on_fail("boom")
    assert calls == ["ok", "fail"]
