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

import re

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
# Last-resort denominator when the chain tip is unreachable. Circulating, not
# terminal: a network failure must not silently reprice every percentage on the
# page by the 4.6 percent gap between the two.
SUPPLY_FALLBACK_DATE = "2026-08-20"
HALVING_INTERVAL = 210_000
INITIAL_REWARD = 50.0

# A residual value guarantee or a vendor backstop is a contingent payment
# obligation, not money drawn. CCIR's tracker carries both, and on 2026-08-17 a
# single NVIDIA residual-value guarantee capped at $105B entered it: at that
# day's BTC it is 1.63M BTC-equivalent, which is 41% of everything the tracker
# records since 2023. Summing it into "borrowed" overstates the stack by that
# much, so it is separated rather than counted.
# A row is contingent only when the instrument ITSELF is a guarantee, never
# because a debt instrument happens to mention one. The old pattern matched any
# occurrence of "guarant|backstop" in the prose and so threw out a $300M Crusoe
# credit facility for saying "Goldman Sachs loan, AMD backstop" -- a drawn loan
# that merely carries support, classified as an unfunded promise.
#
# CCIR types every row, and its debt types are unambiguous: a Bond, Convertible,
# Credit facility or Lease is money raised whatever its support package. Only
# rows it leaves as Other can be a bare guarantee, and among those the language
# test decides. On the current ledger that is one row of seven: NVIDIA's
# residual value guaranties. The other six are a Magnetar loan, SAFEs and
# promissory notes, an early debt raise, a private-credit facility and a planned
# leveraged loan, all correctly kept as drawn.
CONTINGENT_PATTERN = r"guarant|backstop"
DEBT_TYPES = frozenset({"bond", "convertible", "credit facility", "lease"})


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


# Height is near-linear in time, so a date maps to a supply without a network
# call or a per-date lookup. Anchored on two real chain observations at the ends
# of the window this app covers, both read from mempool.space on 2026-08-20:
# 2023-01-01 was height 769,786 and 2026-08-01 was height 960,481.
# Checked against four interior probes (2023-09-01, 2024-04-20, 2025-03-01,
# 2026-01-01); worst supply error 6,012 BTC, 0.03 percent of supply.
HEIGHT_ANCHOR_DATE = "2023-01-01"
HEIGHT_ANCHOR = 769_786
BLOCKS_PER_DAY = 145.79


def supply_at(when) -> float:
    """Circulating supply on a given date.

    Dividing a historical series by today's supply measures every past point
    against coins that did not exist yet. Supply grew 4.1 percent across this
    app's window, so the error is small but it is in one direction.
    """
    when = pd.Timestamp(when)
    if when.tz is None:
        when = when.tz_localize("UTC")
    days = (when - pd.Timestamp(HEIGHT_ANCHOR_DATE, tz="UTC")).days
    return circulating_supply(max(0, int(HEIGHT_ANCHOR + BLOCKS_PER_DAY * days)))


def split_contingent(credit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate drawn borrowings from contingent guarantees.

    A frame with no instrument column cannot be classified, so everything is
    treated as drawn. That is the safe direction: it never silently discards
    rows on the basis of a column that is not there.
    """
    if credit.empty or "instrument" not in credit.columns:
        return credit, credit.iloc[0:0]
    names_a_guarantee = credit["instrument"].astype(str).str.contains(
        CONTINGENT_PATTERN, case=False, na=False)
    if "type" in credit.columns:
        is_debt = credit["type"].astype(str).str.strip().str.lower().isin(DEBT_TYPES)
    else:
        # No type column: fall back to language alone rather than guess, and
        # accept that a supported loan may be misfiled. Reported, not silent.
        is_debt = pd.Series(False, index=credit.index)
    flag = names_a_guarantee & ~is_debt
    return credit[~flag].copy(), credit[flag].copy()


# CCIR's `issued` column is prose, not a date field. Real values include
# "Reported 2025-10-16", "Priced 2025-10-16; JV announced 2025-10-21",
# "Finalized ~2026-06-08 (press; no filing)" and "-". Handing that to
# pd.to_datetime(errors="coerce") and dropping the failures silently removed 20
# non-contingent rows worth $156.9B, which was more than half the tracker, and
# removed them non-randomly: press-reported private credit fails, filed deals
# parse. The selection rule was effectively "did pandas understand the prose".
_ISO_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ISO_MONTH = re.compile(r"(\d{4})-(\d{2})(?!\d)")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def parse_issue_date(text) -> tuple[pd.Timestamp, str]:
    """Pull an issue date out of CCIR's free-text `issued` cell.

    Returns (timestamp, precision) where precision is 'day', 'month', 'year' or
    'none'. The FIRST date in the cell wins: CCIR writes these in event order,
    so "Priced 2025-10-16; JV announced 2025-10-21" leads with the pricing date,
    which is the one an issuance series wants.

    Precision is returned rather than hidden because a row dated only to a year
    is placed on 1 January, and a chart should be able to say so.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return pd.NaT, "none"
    raw = str(text).strip()
    if not raw or raw in {"-", "--", "—", "–"}:
        return pd.NaT, "none"

    m = _ISO_DAY.search(raw)
    if m:
        try:
            return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3))), "day"
        except ValueError:
            pass
    m = _ISO_MONTH.search(raw)
    if m:
        try:
            return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1), "month"
        except ValueError:
            pass
    # A bare year has to be caught before pd.to_datetime, which parses "2024"
    # happily and would report it as a day-precision date on 1 January.
    if _YEAR.fullmatch(raw):
        return pd.Timestamp(int(raw), 1, 1), "year"
    direct = pd.to_datetime(raw, errors="coerce", format="mixed")
    if not pd.isna(direct):
        return pd.Timestamp(direct), "day"
    m = _YEAR.search(raw)
    if m:
        return pd.Timestamp(int(m.group(1)), 1, 1), "year"
    return pd.NaT, "none"


def debt_coverage(credit: pd.DataFrame, since: str = "2023-01-01",
                  exclude_contingent: bool = True) -> dict:
    """What the debt chart shows against what the tracker holds.

    Exists so the page can state its own coverage instead of implying it plots
    everything. Every excluded row is counted and reasoned, never dropped.
    """
    if credit.empty:
        return {"rows_total": 0, "rows_shown": 0, "usd_total": 0.0, "usd_shown": 0.0,
                "excluded": {}, "precision": {}}
    working = credit.copy()
    contingent_rows = 0, 0.0
    if exclude_contingent:
        drawn, contingent = split_contingent(working)
        contingent_rows = (len(contingent), float(contingent["size_musd"].sum()))
        working = drawn

    dated = working["issued"].map(parse_issue_date)
    working = working.assign(issued_on=[d for d, _ in dated],
                             date_precision=[p for _, p in dated])
    undated = working[working["issued_on"].isna()]
    old = working[(~working["issued_on"].isna()) & (working["issued_on"] < pd.Timestamp(since))]
    shown = working[(~working["issued_on"].isna()) & (working["issued_on"] >= pd.Timestamp(since))]

    return {
        "rows_total": len(credit),
        "rows_shown": len(shown),
        "usd_total": float(credit["size_musd"].sum()),
        "usd_shown": float(shown["size_musd"].sum()),
        "excluded": {
            "no date in the cell": (len(undated), float(undated["size_musd"].sum())),
            f"issued before {since}": (len(old), float(old["size_musd"].sum())),
            "contingent, not drawn": contingent_rows,
        },
        "precision": shown["date_precision"].value_counts().to_dict(),
    }


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
    dated = working["issued"].map(parse_issue_date)
    working["issued_on"] = [d for d, _ in dated]
    working["date_precision"] = [p for _, p in dated]
    working = working.dropna(subset=["issued_on", "size_musd"])
    working = working[working["issued_on"] >= pd.Timestamp(since)].sort_values("issued_on")
    if working.empty:
        return pd.DataFrame()

    working["btc_at_issue"] = working.apply(
        lambda row: row["size_musd"] * 1e6 / price_at(history, row["issued_on"]), axis=1
    )
    working["cumulative_btc"] = working["btc_at_issue"].cumsum()

    # Each deal's share of Bitcoin's whole market capitalisation on the day it
    # was signed, then accumulated. Freezing the share at issue is the point:
    # dividing the running total by one supply figure instead would re-rate every
    # past deal every time a block is mined, so the chart's history would keep
    # changing shape after the fact.
    working["supply_at_issue"] = working["issued_on"].map(supply_at)
    working["share_at_issue"] = working["btc_at_issue"] / working["supply_at_issue"]
    working["cumulative_share"] = working["share_at_issue"].cumsum()
    return working.reset_index(drop=True)


def debt_share_series(stack: pd.DataFrame, history: pd.Series,
                      end: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Two daily readings of the same pile of debt.

    `committed` is each deal's share of Bitcoin's market capitalisation on the
    day it was signed, accumulated. It moves only when someone borrows.

    `marked` is every dollar borrowed so far against Bitcoin's market
    capitalisation on the day being read. It moves when Bitcoin moves.

    The gap between them is the whole point: it is whether Bitcoin has outgrown
    the borrowing since the borrowing happened, or the other way round. Neither
    line alone can say that.

    Caveat carried deliberately: `marked` treats debt as outstanding once
    issued. Two of the tracker's instruments have matured and a third of them
    carry no parseable maturity, so netting redemptions is not yet possible and
    would barely move the line today. It will matter as maturities land.
    """
    if stack.empty:
        return pd.DataFrame()
    # Default to the last day priced, not the last day borrowed. Ending on the
    # final deal would make "marked to Bitcoin today" mean "marked to Bitcoin on
    # whatever day someone last signed something", which drifts further from
    # true the longer nobody borrows.
    last_deal = pd.Timestamp(stack["issued_on"].max())
    if end is not None:
        end = pd.Timestamp(end)          # an explicit end is honoured as given
    elif len(history):
        # Never stop short of the last deal, in case prices lag the tracker.
        end = max(pd.Timestamp(history.index.max()), last_deal)
    else:
        end = last_deal
    days = pd.date_range(stack["issued_on"].min(), end, freq="D")
    if len(days) == 0:
        return pd.DataFrame()

    by_day = stack.groupby("issued_on").agg(
        share=("share_at_issue", "sum"), usd=("size_musd", "sum"))
    committed = by_day["share"].cumsum().reindex(days, method="ffill").fillna(0.0)
    borrowed = (by_day["usd"] * 1e6).cumsum().reindex(days, method="ffill").fillna(0.0)
    market_cap = pd.Series([price_at(history, d) * supply_at(d) for d in days], index=days)

    return pd.DataFrame({
        "date": days,
        "committed": committed.to_numpy(),
        "marked": (borrowed / market_cap).to_numpy(),
    }).reset_index(drop=True)


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
