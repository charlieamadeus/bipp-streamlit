from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from .metrics import compute_per_btc, normalize_index, weighted_basket


ORNN_BASE_URL = "https://api.ornnai.com/api/gpu"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


def load_fixture_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return _normalize_raw_columns(df)


def fetch_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "bipp-prototype/0.1"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_ornn_series(gpu_name: str) -> pd.DataFrame:
    url = f"{ORNN_BASE_URL}/{quote(gpu_name)}/index-history"
    payload = fetch_json(url)
    if not payload.get("success"):
        raise RuntimeError(f"Ornn request failed for {gpu_name}")
    df = pd.DataFrame(payload["data"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date
    return df[["date", "index_value"]].rename(columns={"index_value": _gpu_column(gpu_name)})


def fetch_coinbase_btc_usd(start_date: str, end_date: str) -> pd.DataFrame:
    url = f"{COINBASE_CANDLES_URL}?granularity=86400&start={start_date}T00:00:00Z&end={end_date}T00:00:00Z"
    rows = fetch_json(url)
    if not isinstance(rows, list):
        raise RuntimeError("Unexpected Coinbase candles response")
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    df["date"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.date
    df = df.sort_values("date")
    return df[["date", "close"]].rename(columns={"close": "btc_usd"})


def fetch_live_dataset() -> pd.DataFrame:
    h100 = fetch_ornn_series("H100 SXM")
    h200 = fetch_ornn_series("H200")
    b200 = fetch_ornn_series("B200")
    first_date = max(h100["date"].min(), h200["date"].min(), b200["date"].min()).isoformat()
    last_date = min(h100["date"].max(), h200["date"].max(), b200["date"].max()).isoformat()
    btc = fetch_coinbase_btc_usd(first_date, last_date)

    df = btc.merge(h100, on="date").merge(h200, on="date").merge(b200, on="date")
    return _normalize_raw_columns(df)


def trailing_window(raw_df: pd.DataFrame, days: int) -> pd.DataFrame:
    if days <= 0:
        raise ValueError("Trailing window must be positive")
    df = _normalize_raw_columns(raw_df)
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=days - 1)
    return filter_date_range(df, start_date, end_date)


def filter_date_range(raw_df: pd.DataFrame, start_date: str | date | pd.Timestamp, end_date: str | date | pd.Timestamp) -> pd.DataFrame:
    df = _normalize_raw_columns(raw_df)
    start = _as_utc_day(start_date)
    end = _as_utc_day(end_date)
    if start > end:
        raise ValueError("Start date must be on or before end date")

    filtered = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
    if filtered.empty:
        raise ValueError("Selected date range has no overlapping data")
    return filtered


def build_metrics(raw_df: pd.DataFrame, weights: dict[str, float], base_date: str | None = None) -> pd.DataFrame:
    df = _normalize_raw_columns(raw_df).copy()
    df["hardware_basket"] = df.apply(
        lambda row: weighted_basket(
            {"h100": row["h100"], "h200": row["h200"], "b200": row["b200"]},
            weights,
        ),
        axis=1,
    )
    df["compute_per_btc"] = df.apply(lambda row: compute_per_btc(row["btc_usd"], row["hardware_basket"]), axis=1)
    base_index = 0
    if base_date is not None:
        matches = df.index[df["date"].dt.date.astype(str) == base_date].tolist()
        if not matches:
            raise ValueError(f"Base date not found: {base_date}")
        base_index = matches[0]
    df["bipp"] = normalize_index(df["compute_per_btc"].tolist(), base_index=base_index)
    return df


def _normalize_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], utc=True)
    required = {"date", "btc_usd", "h100", "h200", "b200"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    normalized = normalized.sort_values("date").reset_index(drop=True)
    for column in ["btc_usd", "h100", "h200", "b200"]:
        normalized[column] = normalized[column].astype(float)
        if (normalized[column] <= 0).any():
            raise ValueError(f"{column} must be positive")
    return normalized


def _as_utc_day(value: str | date | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.normalize()


def _gpu_column(gpu_name: str) -> str:
    lookup = {
        "H100 SXM": "h100",
        "H200": "h200",
        "B200": "b200",
    }
    if gpu_name not in lookup:
        raise ValueError(f"Unsupported GPU for BIPP basket: {gpu_name}")
    return lookup[gpu_name]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
