from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

import pandas as pd
import pytest

from bipp import ccir, display_rates, store


@pytest.mark.parametrize("include_history", [False, True])
def test_html_instead_of_csv_preserves_the_exact_dated_archive(include_history):
    response = BytesIO(b"<!doctype html><title>Data terms</title>")
    response.headers = Message()
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    archived = pd.DataFrame({"as_of_date": pd.to_datetime(["2026-11-06"], utc=True),
                             "price_headline": [3.75], "series_id": ["fixture"]})
    with patch.object(ccir, "urlopen", return_value=response), patch.object(store, "read", return_value=archived):
        result, archive_only = display_rates.load_panel(include_history=include_history)
    assert archive_only
    pd.testing.assert_frame_equal(result, archived)


def test_snapshot_still_fails_if_rates_return_html():
    response = BytesIO(b"<html></html>")
    response.headers = Message()
    response.headers["Content-Type"] = "text/html"
    with patch.object(ccir, "urlopen", return_value=response), pytest.raises(ValueError, match="instead of.*CSV"):
        ccir.fetch_catalog()


def test_no_archive_preserves_network_failure():
    with patch.object(ccir, "fetch_catalog", side_effect=URLError("rates unavailable")), \
         patch.object(store, "read", return_value=pd.DataFrame()), \
         pytest.raises(URLError, match="rates unavailable"):
        display_rates.load_panel(include_history=True)


@pytest.mark.parametrize("include_history", [False, True])
def test_healthy_paths_preserve_each_page_history_policy(include_history):
    live = pd.DataFrame({"price_headline": [2.0]})
    merged = pd.DataFrame({"price_headline": [1.9, 2.0]})
    with patch.object(ccir, "fetch_catalog"), patch.object(ccir, "fetch_history"), \
         patch.object(ccir, "load_panel", return_value=live), patch.object(store, "merge_rates", return_value=merged) as merge:
        result, archive_only = display_rates.load_panel(include_history=include_history)
    assert not archive_only
    assert result is (merged if include_history else live)
    assert merge.call_count == int(include_history)


def test_v2_keeps_explicit_nvl_variants_separate_from_base_basket(monkeypatch):
    import app_v2

    panel = pd.DataFrame({
        "gpu_model": ["H100", "H100", "H200", "H200", "B200"],
        "series_id": ["CRI-T2-H100-ALL-ALL-OD-ALL", "CRI-T2-H100-NVL-ALL-ALL-OD-ALL",
                      "CRI-T2-H200-ALL-ALL-OD-ALL", "CRI-T2-H200-NVL-ALL-ALL-OD-ALL",
                      "CRI-T2-B200-ALL-ALL-OD-ALL"],
    })
    monkeypatch.setattr(display_rates, "load_panel", lambda **kw: (panel, True))
    result, archived = app_v2.load_ccir_panel.__wrapped__()
    assert archived
    assert result["gpu_model"].tolist() == ["H100", "H100-NVL", "H200", "H200-NVL", "B200"]
    pd.testing.assert_series_equal(result["series_id"], panel["series_id"])
    assert panel["gpu_model"].tolist() == ["H100", "H100", "H200", "H200", "B200"]
