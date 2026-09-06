"""DGX Spark BTC chart acceptance tests (SPEC section 6)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from bipp import btc as btc_mod
from bipp import dgx_spark, store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_msrp_steps_pin():
    assert dgx_spark.MSRP_STEPS == [
        ("2025-10-15", 3999.00),
        ("2026-02-23", 4699.00),
    ]


def test_sparks_per_btc_matches_price_at():
    history = pd.Series(
        [100_000.0],
        index=pd.to_datetime(["2026-03-01"]),
        name="btc_usd",
    )
    price = dgx_spark.msrp_price_on("2026-03-01")
    assert price == pytest.approx(4699.00)
    btc_usd = btc_mod.price_at(history, "2026-03-01")
    assert btc_usd / price == pytest.approx(100_000.0 / 4699.00)


def test_buybox_fixture_parses_price_amount():
    html = (FIXTURES / "amazon_buybox_new.html").read_text(encoding="utf-8")
    row = dgx_spark.parse_buybox(html)
    assert row["available"] is True
    assert row["price_usd"] == pytest.approx(4799.99)
    assert row["asin"] == dgx_spark.ASIN
    assert row["seller"] == "Micro Center"


def test_softblock_raises_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    html = (FIXTURES / "amazon_softblock.html").read_text(encoding="utf-8")
    with pytest.raises(dgx_spark.DgxSparkFetchError):
        dgx_spark.parse_buybox(html)
    assert not (tmp_path / "dgx_spark.csv").exists()


def test_no_new_offer_writes_unavailable():
    html = (FIXTURES / "amazon_no_new_offer.html").read_text(encoding="utf-8")
    row = dgx_spark.parse_buybox(html)
    assert row["available"] is False
    assert row["price_usd"] is None


def test_store_append_idempotent_on_as_of_date(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    frame = pd.DataFrame([{
        "as_of_date": pd.Timestamp("2026-09-06", tz="UTC"),
        "asin": dgx_spark.ASIN,
        "price_usd": 4799.99,
        "available": True,
        "seller": "Micro Center",
        "ships_from": "Amazon",
    }])
    store.append("dgx_spark", frame)
    restated = frame.copy()
    restated["price_usd"] = 1.0
    counts = store.append("dgx_spark", restated)
    assert counts["added"] == 0
    got = store.read("dgx_spark")
    assert len(got) == 1
    assert got["price_usd"].iloc[0] == pytest.approx(4799.99)


def test_spark_card_does_not_call_load_hardware(monkeypatch):
    import app_v4

    def boom(*_a, **_k):
        raise AssertionError("load_hardware must not be called for Spark")

    monkeypatch.setattr(app_v4, "load_hardware", boom)
    state = dgx_spark.headline_state(pd.DataFrame())
    assert state["kind"] == "msrp"
    assert state["price_usd"] == pytest.approx(dgx_spark.current_msrp()[1])
    # Helpers used by the card path still work with an empty store.
    empty = pd.DataFrame()
    btc = pd.Series([90_000.0], index=pd.to_datetime(["2026-09-06"], utc=True), name="btc_usd")
    msrp = dgx_spark.expand_msrp_trace(btc, None)
    assert not msrp.empty
    assert app_v4.chart_spark(empty, btc).data  # builds without hardware


def _import_snapshot_main():
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "scripts" / "snapshot.py"
    spec = importlib.util.spec_from_file_location("bipp_snapshot_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hard_failure_exits_1_even_if_soft_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, "STORE", tmp_path)
    snap = _import_snapshot_main()

    def fail_rates(*_a, **_k):
        raise RuntimeError("rates down")

    monkeypatch.setattr(snap.ccir, "fetch_catalog", fail_rates)
    monkeypatch.setattr(snap, "fetch_ornn_panel", lambda: pd.DataFrame({
        "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "h100": [1.0], "h200": [1.0], "b200": [1.0],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_hardware", lambda: pd.DataFrame({
        "model": ["H100"], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "executed_median_usd": [1000.0], "age_years": [1.0],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_tokens", lambda: pd.DataFrame({
        "model": ["x"], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "output_usd_per_mtok": [1.0], "pricing_basis": ["posted"],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_credit", lambda: pd.DataFrame({
        "issuer": ["x"], "instrument": ["y"], "type": ["loan"],
        "size_musd": [1.0], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
    }))
    monkeypatch.setattr(snap.dgx_spark, "snapshot_today", lambda: pd.DataFrame([{
        "as_of_date": pd.Timestamp("2026-09-06", tz="UTC"),
        "asin": dgx_spark.ASIN, "price_usd": 4799.99, "available": True,
        "seller": None, "ships_from": None,
    }]))

    code = snap.main()
    out = capsys.readouterr().out
    assert code == 1
    assert "FAILED  rates:" in out
    assert "SOFT" not in out or "SOFT  dgx_spark" not in out


def test_soft_only_amazon_failure_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, "STORE", tmp_path)
    snap = _import_snapshot_main()

    monkeypatch.setattr(snap.ccir, "load_panel", lambda *_a, **_k: pd.DataFrame({
        "series_id": ["s"], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "price_headline": [1.0],
    }))
    monkeypatch.setattr(snap.ccir, "fetch_catalog", lambda: object())
    monkeypatch.setattr(snap.ccir, "fetch_history", lambda: object())
    monkeypatch.setattr(snap, "fetch_ornn_panel", lambda: pd.DataFrame({
        "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "h100": [1.0], "h200": [1.0], "b200": [1.0],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_hardware", lambda: pd.DataFrame({
        "model": ["H100"], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "executed_median_usd": [1000.0], "age_years": [1.0],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_tokens", lambda: pd.DataFrame({
        "model": ["x"], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
        "output_usd_per_mtok": [1.0], "pricing_basis": ["posted"],
    }))
    monkeypatch.setattr(snap.ccir_pages, "fetch_credit", lambda: pd.DataFrame({
        "issuer": ["x"], "instrument": ["y"], "type": ["loan"],
        "size_musd": [1.0], "as_of_date": [pd.Timestamp("2026-09-06", tz="UTC")],
    }))

    def soft_fail():
        raise dgx_spark.DgxSparkFetchError("buy-box container missing")

    monkeypatch.setattr(snap.dgx_spark, "snapshot_today", soft_fail)

    code = snap.main()
    out = capsys.readouterr().out
    assert code == 0
    assert "SOFT  dgx_spark:" in out
    assert "FAILED" not in out
    assert not (tmp_path / "dgx_spark.csv").exists()
