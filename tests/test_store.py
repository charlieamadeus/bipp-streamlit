from __future__ import annotations

import pandas as pd
import pytest

from bipp import store
from bipp.pipeline import fetch_ornn_panel


def _ornn_rows(*days_and_prices: tuple[str, float]) -> pd.DataFrame:
    rows = [{"as_of_date": day, "h100": price, "h200": price + 1, "b200": price + 2}
            for day, price in days_and_prices]
    frame = pd.DataFrame(rows)
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], utc=True)
    return frame


def test_ornn_append_keeps_the_first_row(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    first = _ornn_rows(("2026-06-03", 2.69))
    restated = _ornn_rows(("2026-06-03", 9.99))
    store.append("ornn", first)
    store.append("ornn", restated)
    got = store.read("ornn")
    assert len(got) == 1
    assert got["h100"].iloc[0] == pytest.approx(2.69)


def test_ornn_append_extends_with_a_new_day(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    store.append("ornn", _ornn_rows(("2026-06-03", 2.69)))
    counts = store.append("ornn", _ornn_rows(("2026-06-03", 2.69), ("2026-06-04", 2.70)))
    assert counts["added"] == 1
    got = store.read("ornn")
    assert list(got["as_of_date"].dt.strftime("%Y-%m-%d")) == ["2026-06-03", "2026-06-04"]
    assert got["h100"].tolist() == pytest.approx([2.69, 2.70])


def test_merge_ornn_stored_wins_and_live_fills_the_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    store.append("ornn", _ornn_rows(("2026-06-03", 2.69)))
    live = _ornn_rows(("2026-06-03", 9.99), ("2026-09-02", 2.83))
    merged = store.merge_ornn(live)
    by_day = {str(d.date()): h for d, h in zip(merged["as_of_date"], merged["h100"])}
    assert by_day["2026-06-03"] == pytest.approx(2.69)
    assert by_day["2026-09-02"] == pytest.approx(2.83)


def test_merge_ornn_returns_store_when_live_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    store.append("ornn", _ornn_rows(("2026-06-03", 2.69)))
    merged = store.merge_ornn(pd.DataFrame())
    assert len(merged) == 1
    assert merged["h100"].iloc[0] == pytest.approx(2.69)


def test_coverage_lists_ornn(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    store.append("ornn", _ornn_rows(("2026-06-03", 2.69), ("2026-06-04", 2.70)))
    row = store.coverage().set_index("series").loc["ornn"]
    assert row["rows"] == 2
    assert row["days"] == 2
    assert row["first"] == "2026-06-03"
    assert row["last"] == "2026-06-04"


def test_fetch_ornn_panel_aligns_the_three_gpus(monkeypatch):
    def fake_series(gpu_name: str) -> pd.DataFrame:
        col = {"H100 SXM": "h100", "H200": "h200", "B200": "b200"}[gpu_name]
        return pd.DataFrame({
            "date": [pd.Timestamp("2026-06-03").date(), pd.Timestamp("2026-06-04").date()],
            col: [1.0, 1.1] if col == "h100" else [2.0, 2.1] if col == "h200" else [3.0, 3.1],
        })

    monkeypatch.setattr("bipp.pipeline.fetch_ornn_series", fake_series)
    panel = fetch_ornn_panel()
    assert list(panel.columns) == ["as_of_date", "h100", "h200", "b200"]
    assert len(panel) == 2
    assert panel["h100"].tolist() == pytest.approx([1.0, 1.1])
    assert panel["b200"].iloc[-1] == pytest.approx(3.1)
