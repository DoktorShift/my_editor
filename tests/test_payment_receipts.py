"""Tests for the paid-install receipt store."""

from __future__ import annotations

from plugin_marketplace.payment_receipts import (
    PaymentReceipt,
    PaymentReceiptStore,
)


def _receipt(plugin_id: str = "hello", version: str = "1.0.0") -> PaymentReceipt:
    return PaymentReceipt(
        plugin_id=plugin_id, version=version, sats=1000,
        lightning_address="alice@example.com",
        bolt11="lnbc1u1pj...", preimage="ab" * 32,
        paid_at=1_700_000_000, source_name="my-editor official",
    )


def test_initial_store_is_empty(tmp_path):
    store = PaymentReceiptStore(path=tmp_path / "receipts.json")
    assert store.all() == []


def test_add_persists_across_instances(tmp_path):
    path = tmp_path / "receipts.json"
    store1 = PaymentReceiptStore(path=path)
    store1.add(_receipt())

    store2 = PaymentReceiptStore(path=path)
    assert len(store2.all()) == 1
    assert store2.all()[0].plugin_id == "hello"


def test_has_paid_for_matches_on_plugin_id_alone(tmp_path):
    store = PaymentReceiptStore(path=tmp_path / "receipts.json")
    store.add(_receipt(plugin_id="hello", version="1.0.0"))
    assert store.has_paid_for("hello") is True
    # Patch version that the receipt didn't pin requires version match.
    assert store.has_paid_for("hello", version="2.0.0") is False
    assert store.has_paid_for("hello", version="1.0.0") is True


def test_has_paid_for_returns_false_for_unknown_plugin(tmp_path):
    store = PaymentReceiptStore(path=tmp_path / "receipts.json")
    store.add(_receipt())
    assert store.has_paid_for("missing") is False


def test_corrupted_file_returns_empty(tmp_path):
    path = tmp_path / "receipts.json"
    path.write_text("this is not json", encoding="utf-8")
    store = PaymentReceiptStore(path=path)
    assert store.all() == []


def test_add_after_corrupted_file_resets_to_good_state(tmp_path):
    path = tmp_path / "receipts.json"
    path.write_text("garbage", encoding="utf-8")
    store = PaymentReceiptStore(path=path)
    store.add(_receipt())
    # Roundtrip: a fresh instance reads the new clean file.
    store2 = PaymentReceiptStore(path=path)
    assert len(store2.all()) == 1
