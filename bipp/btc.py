"""BTC/USD history, long enough to price things that happened years ago.

Coinbase caps a candle request at 300 rows, so multi-year history has to be
chunked. bipp.pipeline.fetch_coinbase_btc_usd does a single request and is fine
for the 30-to-90 day compute window; this is for the debt series, which reaches
back to 2023.
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.request import Request, urlopen

import pandas as pd

CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
CHUNK_DAYS = 290  # under Coinbase's 300-candle ceiling


def fetch_daily(start: str, end: str) -> pd.Series:
    """Daily BTC/USD closes over an arbitrary span, indexed by date."""
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first >= last:
        raise ValueError("start must be before end")

    closes: dict[str, float] = {}
    cursor = first
    while cursor < last:
        stop = min(cursor + dt.timedelta(days=CHUNK_DAYS), last)
        url = f"{CANDLES_URL}?granularity=86400&start={cursor}T00:00:00Z&end={stop}T00:00:00Z"
        request = Request(url, headers={"User-Agent": "bipp-streamlit/4.0"})
        with urlopen(request, timeout=30) as response:
            for candle in json.loads(response.read()):
                stamp = dt.datetime.fromtimestamp(candle[0], dt.timezone.utc)
                closes[stamp.strftime("%Y-%m-%d")] = float(candle[4])
        cursor = stop

    if not closes:
        raise RuntimeError("Coinbase returned no candles")
    series = pd.Series(closes, name="btc_usd")
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


def price_at(history: pd.Series, when) -> float:
    """BTC/USD on a date, carrying the last known close backwards over gaps."""
    return float(history.asof(pd.Timestamp(when)))


TERMINAL_SUPPLY = 21_000_000
HALVING_INTERVAL = 210_000
INITIAL_REWARD = 50.0

# A residual value guarantee or a vendor backstop is a contingent payment
# obligation, not money drawn. CCIR's tracker carries both, and on 2026-08-17 a
# single NVIDIA residual-value guarantee capped at $105B entered it: at that
# day's BTC it is 1.63M BTC-equivalent, which is 41% of everything the tracker
# records since 2023. Summing it into "borrowed" overstates the stack by that
# much, so it is separated rather than counted.
CONTINGENT_PATTERN = r"guarant|backstop"


def circulating_supply(block_height: int) -> float:
    """Bitcoin mined so far, from the issuance schedule alone.

    Deterministic given height, so no supply oracle is needed and the number
    cannot drift with whatever a third-party endpoint decides to report.
    """
    if block_height < 0:
        raise ValueError("block_height must be non-negative")
    supply, reward, start = 0.0, INITIAL_REWARD, 0
    while start <= block_height and reward > 0:
        end = min(start + HALVING_INTERVAL - 1, block_height)
        supply += (end - start + 1) * reward
        reward /= 2
        start += HALVING_INTERVAL
    return supply


def split_contingent(credit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate drawn borrowings from contingent guarantees.

    A frame with no instrument column cannot be classified, so everything is
    treated as drawn. That is the safe direction: it never silently discards
    rows on the basis of a column that is not there.
    """
    if credit.empty or "instrument" not in credit.columns:
        return credit, credit.iloc[0:0]
    flag = credit["instrument"].astype(str).str.contains(CONTINGENT_PATTERN, case=False, na=False)
    return credit[~flag].copy(), credit[flag].copy()


def debt_in_btc(credit: pd.DataFrame, history: pd.Series,
                since: str = "2023-01-01", exclude_contingent: bool = True) -> pd.DataFrame:
    """Each borrowing converted to BTC at the price on the day it was struck.

    Issue-date pricing rather than today's price, because the question is how
    much money was committed at the moment of commitment. Marking the whole
    stack at today's BTC would make the series move when BTC moves, which is
    the opposite of what it is for.
    """
    if credit.empty:
        return pd.DataFrame()
    working = credit.copy()
    if exclude_contingent:
        working, _ = split_contingent(working)
    if working.empty:
        return pd.DataFrame()
    working["issued_on"] = pd.to_datetime(working["issued"], errors="coerce", format="mixed")
    working = working.dropna(subset=["issued_on", "size_musd"])
    working = working[working["issued_on"] >= pd.Timestamp(since)].sort_values("issued_on")
    if working.empty:
        return pd.DataFrame()

    working["btc_at_issue"] = working.apply(
        lambda row: row["size_musd"] * 1e6 / price_at(history, row["issued_on"]), axis=1
    )
    working["cumulative_btc"] = working["btc_at_issue"].cumsum()
    return working.reset_index(drop=True)


def decompose(compute_per_btc: pd.Series, btc_usd: pd.Series,
              compute_price: pd.Series) -> dict[str, float]:
    """Split a move in purchasing power into its money and compute halves.

    compute_per_btc = btc_usd / compute_price, so over any window the change in
    purchasing power is exactly the change in BTC divided by the change in the
    compute price. Reporting both halves is the whole point: a rise in what one
    BTC buys means something different when BTC rallied than when compute got
    cheaper.
    """
    def change(series: pd.Series) -> float:
        clean = series.dropna()
        if len(clean) < 2 or clean.iloc[0] == 0:
            raise ValueError("Need at least two points and a non-zero start")
        return (clean.iloc[-1] / clean.iloc[0] - 1) * 100

    power, money, compute = change(compute_per_btc), change(btc_usd), change(compute_price)
    return {
        "purchasing_power_pct": power,
        "btc_pct": money,
        "compute_price_pct": compute,
        "driver": "money" if abs(money) >= abs(compute) else "compute",
        "money_share": abs(money) / (abs(money) + abs(compute)) if (money or compute) else 0.0,
    }
