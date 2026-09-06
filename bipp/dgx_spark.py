"""NVIDIA DGX Spark retail price in BTC terms.

Pinned to ASIN B0FWJ16CCH. Amazon Buy Box New is the forward market series;
NVIDIA MSRP lives as a sparse step table in code for the pre-Amazon backfill.
A successor SKU gets its own module/series file, not a silent rewrite here.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

ASIN = "B0FWJ16CCH"
PRODUCT_URL = f"https://www.amazon.com/dp/{ASIN}"
TITLE_MARK = "DGX Spark"
SERIES_START = dt.date(2025, 10, 15)
BLOCKED_GAP_DAYS = 7  # SPEC number; change only with the SPEC sentence.

# Sparse NVIDIA list-price steps. Edit in place if a sharper timestamp appears.
MSRP_STEPS: list[tuple[str, float]] = [
    ("2025-10-15", 3999.00),
    ("2026-02-23", 4699.00),
]

_USER_AGENT = "bipp-streamlit/4.0 (research)"
_BUYBOX_BLOB = re.compile(
    r'class="[^"]*twister-plus-buying-options-price-data[^"]*"\s*>\s*(\{.*?\})\s*</div>',
    re.S,
)
_SELLER = re.compile(
    r"id=['\"]sellerProfileTriggerId['\"][^>]*>\s*([^<]+?)\s*<",
    re.I,
)
_SHIPS = re.compile(
    r"offer-display-feature-name=[\"']desktop-fulfiller-info[\"'][\s\S]{0,500}?"
    r"offer-display-feature-text-message[\"']?\s*>\s*([^<]+?)\s*<",
    re.I,
)


class DgxSparkFetchError(Exception):
    """Identity or buy-box container missing; do not write a store row."""


def msrp_price_on(day: dt.date | str) -> float:
    """Latest MSRP step on or before `day` (right-labeled)."""
    when = pd.Timestamp(day).date()
    price = None
    for raw, step in MSRP_STEPS:
        if pd.Timestamp(raw).date() <= when:
            price = float(step)
    if price is None:
        raise ValueError(f"no MSRP step on or before {when}")
    return price


def current_msrp(today: dt.date | None = None) -> tuple[dt.date, float]:
    """Active MSRP step as (effective_date, price)."""
    when = today or dt.date.today()
    effective = pd.Timestamp(MSRP_STEPS[0][0]).date()
    price = float(MSRP_STEPS[0][1])
    for raw, step in MSRP_STEPS:
        step_day = pd.Timestamp(raw).date()
        if step_day <= when:
            effective, price = step_day, float(step)
    return effective, price


def fetch_html(url: str = PRODUCT_URL) -> str:
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DgxSparkFetchError(f"HTTP fetch failed: {exc}") from exc


def parse_buybox(html: str) -> dict:
    """Extract Buy Box New from Amazon HTML.

    Returns a row dict ready for the store. Raises DgxSparkFetchError when the
    page fails identity or lacks a recognizable buy-box container (soft-block /
    captcha shell). Only a rendered buy-box region with no New offer returns
    available=False.
    """
    if ASIN not in html or TITLE_MARK not in html:
        raise DgxSparkFetchError("page failed ASIN/title identity check")

    has_container = (
        "twister-plus-buying-options-price-data" in html
        or 'id="qualifiedBuybox"' in html
        or "desktop_buybox_group_1" in html
    )
    if not has_container:
        raise DgxSparkFetchError("buy-box container missing (soft-block or captcha shell)")

    seller = _first(_SELLER, html)
    ships_from = _first(_SHIPS, html)

    blob_match = _BUYBOX_BLOB.search(html)
    if blob_match is None:
        # Container markers present but no structured price blob: treat as
        # buy-box region without a New offer when qualifiedBuybox is absent,
        # otherwise fail loudly on garbage.
        if 'id="qualifiedBuybox"' not in html and "priceAmount" not in html:
            return _unavailable_row(seller, ships_from)
        raise DgxSparkFetchError("buy-box markers present but price JSON missing")

    try:
        payload = json.loads(blob_match.group(1))
    except json.JSONDecodeError as exc:
        raise DgxSparkFetchError(f"buy-box JSON parse failed: {exc}") from exc

    offers = payload.get("desktop_buybox_group_1") or []
    new_offers = [o for o in offers if str(o.get("buyingOptionType", "")).upper() == "NEW"]
    if not new_offers:
        return _unavailable_row(seller, ships_from)

    amount = new_offers[0].get("priceAmount")
    try:
        price = float(amount)
    except (TypeError, ValueError) as exc:
        raise DgxSparkFetchError(f"unreadable priceAmount: {amount!r}") from exc
    if price <= 0:
        raise DgxSparkFetchError(f"non-positive priceAmount: {price}")

    return {
        "as_of_date": pd.Timestamp(dt.date.today(), tz="UTC"),
        "asin": ASIN,
        "price_usd": price,
        "available": True,
        "seller": seller,
        "ships_from": ships_from,
    }


def snapshot_today(html: str | None = None) -> pd.DataFrame:
    """One-row frame for store.append, or raise DgxSparkFetchError."""
    page = html if html is not None else fetch_html()
    row = parse_buybox(page)
    return pd.DataFrame([row])


def last_available_date(frame: pd.DataFrame) -> dt.date:
    """Latest as_of_date with available=true, else SERIES_START."""
    if frame.empty or "available" not in frame.columns:
        return SERIES_START
    flags = frame["available"]
    if flags.dtype != bool:
        flags = flags.map(_as_bool)
    good = frame.loc[flags]
    if good.empty:
        return SERIES_START
    return pd.to_datetime(good["as_of_date"], utc=True).dt.date.max()


def is_blocked(frame: pd.DataFrame, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    last = last_available_date(frame)
    return (today - last).days >= BLOCKED_GAP_DAYS


def expand_msrp_trace(
    btc: pd.Series,
    first_amazon_available: dt.date | None,
) -> pd.DataFrame:
    """Daily Sparks-per-BTC on MSRP from SERIES_START through day before Amazon.

    If Amazon has never captured an available=true row, expand through the last
    BTC day on or before today.
    """
    if btc.empty:
        return pd.DataFrame(columns=["as_of_date", "price_usd", "sparks_per_btc", "source"])

    start = SERIES_START
    if first_amazon_available is None:
        end = pd.Timestamp(btc.index.max()).date()
    else:
        end = first_amazon_available - dt.timedelta(days=1)
    if end < start:
        return pd.DataFrame(columns=["as_of_date", "price_usd", "sparks_per_btc", "source"])

    rows = []
    for ts in btc.index:
        day = pd.Timestamp(ts).date()
        if day < start or day > end:
            continue
        price = msrp_price_on(day)
        btc_usd = float(btc.loc[ts])
        rows.append({
            "as_of_date": pd.Timestamp(day, tz="UTC"),
            "price_usd": price,
            "sparks_per_btc": btc_usd / price,
            "source": "nvidia_msrp",
        })
    return pd.DataFrame(rows)


def amazon_trace(frame: pd.DataFrame, btc: pd.Series) -> pd.DataFrame:
    """Sparks-per-BTC on available=true Amazon rows; gaps left as absent days."""
    if frame.empty:
        return pd.DataFrame(columns=["as_of_date", "price_usd", "sparks_per_btc", "source"])
    work = frame.copy()
    work["as_of_date"] = pd.to_datetime(work["as_of_date"], utc=True)
    flags = work["available"]
    if flags.dtype != bool:
        flags = flags.map(_as_bool)
    work = work.loc[flags & work["price_usd"].notna()].copy()
    rows = []
    for row in work.itertuples():
        day = pd.Timestamp(row.as_of_date).strftime("%Y-%m-%d")
        try:
            from bipp.btc import price_at
            btc_usd = price_at(btc, day)
        except Exception:  # noqa: BLE001
            continue
        price = float(row.price_usd)
        if price <= 0:
            continue
        rows.append({
            "as_of_date": row.as_of_date,
            "price_usd": price,
            "sparks_per_btc": btc_usd / price,
            "source": "amazon_buybox",
            "seller": getattr(row, "seller", None),
        })
    return pd.DataFrame(rows)


def headline_state(frame: pd.DataFrame, today: dt.date | None = None) -> dict:
    """Card state per SPEC 5.1 headline precedence."""
    today = today or dt.date.today()
    if frame.empty:
        effective, price = current_msrp(today)
        return {
            "kind": "msrp",
            "available": True,
            "price_usd": price,
            "as_of_date": effective,
            "label": "NVIDIA MSRP",
            "seller": None,
            "secondary": None,
        }

    work = frame.copy()
    work["as_of_date"] = pd.to_datetime(work["as_of_date"], utc=True)
    work = work.sort_values("as_of_date")
    latest = work.iloc[-1]
    available = _as_bool(latest["available"])
    if available and pd.notna(latest.get("price_usd")):
        return {
            "kind": "amazon",
            "available": True,
            "price_usd": float(latest["price_usd"]),
            "as_of_date": pd.Timestamp(latest["as_of_date"]).date(),
            "label": "Amazon Buy Box",
            "seller": latest.get("seller"),
            "secondary": None,
        }

    secondary = None
    flags = work["available"].map(_as_bool) if work["available"].dtype != bool else work["available"]
    prior = work.loc[flags & work["price_usd"].notna()]
    if not prior.empty:
        last_good = prior.iloc[-1]
        secondary = {
            "price_usd": float(last_good["price_usd"]),
            "as_of_date": pd.Timestamp(last_good["as_of_date"]).date(),
            "seller": last_good.get("seller"),
        }
    return {
        "kind": "amazon",
        "available": False,
        "price_usd": None,
        "as_of_date": pd.Timestamp(latest["as_of_date"]).date(),
        "label": "Amazon Buy Box",
        "seller": latest.get("seller"),
        "secondary": secondary,
    }


def _unavailable_row(seller: str | None, ships_from: str | None) -> dict:
    return {
        "as_of_date": pd.Timestamp(dt.date.today(), tz="UTC"),
        "asin": ASIN,
        "price_usd": None,
        "available": False,
        "seller": seller,
        "ships_from": ships_from,
    }


def _first(pattern: re.Pattern[str], html: str) -> str | None:
    match = pattern.search(html)
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes"}
