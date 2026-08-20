"""CCIR (ccir.io) compute reference rates as a BIPP price source.

CCIR publishes posted-list-ask GPU rental rates in USD per GPU per hour, with
per-cell panel metadata: source count, dispersion, promotion status. That
metadata is the reason this module exists. It lets BIPP tell a compute-price
move apart from a panel-composition change, which the Ornn index_value feed
cannot do because it ships a bare number.

Attribution requirement: cite as "CCIR (ccir.io)" with the series identifier
and publication date wherever a value is displayed.
"""

from __future__ import annotations

import io
from urllib.request import Request, urlopen

import pandas as pd

from .metrics import compute_per_btc, normalize_index, weighted_basket


DAILY_URL = "https://ccir.io/data/rates_daily.csv"
HISTORY_URL = "https://ccir.io/data/rates_history.csv"
ATTRIBUTION = "CCIR (ccir.io)"

# Dimensions that select a price surface. Series IDs embed these too, but chip
# names contain hyphens (RTX-PRO-6000, A100-40GB) and some history-only series
# do not follow the positional schema, so always take dimensions from the daily
# catalog join rather than parsing the identifier.
SURFACE_DIMENSIONS = [
    "operator_tier",
    "form_factor",
    "interruptibility",
    "commitment_term",
    "region",
]

TIER_LABELS = {"T1": "Hyperscaler", "T2": "Neocloud", "T3": "Marketplace"}

# CCIR publishes every populated cell whatever its depth, and says cells below
# five sources "should be read as indicative". Anything showing a price from a
# cell this thin should say so rather than let it look as firm as a deep one.
INDICATIVE_BELOW = 5


def _fetch_csv(url: str) -> pd.DataFrame:
    request = Request(url, headers={"User-Agent": "bipp-streamlit/2.0 (research)"})
    with urlopen(request, timeout=60) as response:
        return pd.read_csv(io.BytesIO(response.read()))


def fetch_catalog() -> pd.DataFrame:
    """Latest daily snapshot: one row per series, with full dimension metadata."""
    df = _fetch_csv(DAILY_URL)
    df["as_of_date"] = pd.to_datetime(df["as_of_date"], utc=True)
    return df


def fetch_history() -> pd.DataFrame:
    """Daily price history. Carries only series_id, date, price, promotion_status."""
    df = _fetch_csv(HISTORY_URL)
    df["as_of_date"] = pd.to_datetime(df["as_of_date"], utc=True)
    df["price_headline"] = pd.to_numeric(df["price_headline"], errors="coerce")
    return df.dropna(subset=["price_headline"])


def load_panel(catalog: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Join history to catalog dimensions.

    Restricted to series present in the daily catalog, because only those carry
    trustworthy dimension metadata. History-only series are dropped rather than
    parsed out of the identifier.
    """
    meta_columns = [
        "series_id", "gpu_model", "segment", "operator_tier", "form_factor",
        "interruptibility", "region", "commitment_term", "product_class",
        "n_sources", "n_observations", "hhi", "confidence_level",
        "price_p25", "price_p75", "methodology_version",
    ]
    meta = catalog[[c for c in meta_columns if c in catalog.columns]]
    panel = history.merge(meta, on="series_id", how="inner")
    return panel.sort_values(["series_id", "as_of_date"]).reset_index(drop=True)


def surface_options(panel: pd.DataFrame) -> dict[str, list[str]]:
    return {dim: sorted(panel[dim].dropna().unique().tolist()) for dim in SURFACE_DIMENSIONS}


def select_surface(panel: pd.DataFrame, **dims: str | None) -> pd.DataFrame:
    """Filter to one price surface. Pass any subset of SURFACE_DIMENSIONS."""
    out = panel
    for key, value in dims.items():
        if key not in SURFACE_DIMENSIONS:
            raise ValueError(f"Unknown surface dimension: {key}")
        if value is not None:
            out = out[out[key] == value]
    return out


def max_daily_jump(prices: pd.Series) -> float:
    """Largest single-day proportional move.

    Panel composition changes show up here as step discontinuities far larger
    than any real daily rate move. On 2026-07-25 the hyperscaler B200 on-demand
    series dropped 62.6 percent in one day and then resumed a smooth trend;
    that is what this catches.
    """
    values = pd.to_numeric(prices, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) < 2:
        return 0.0
    prior, current = values[:-1], values[1:]
    usable = prior > 0
    if not usable.any():
        return 0.0
    return float(abs(current[usable] / prior[usable] - 1.0).max())


def series_quality(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per series: coverage, worst single-day jump, panel depth."""
    rows = []
    for series_id, group in panel.groupby("series_id"):
        group = group.sort_values("as_of_date")
        latest = group.iloc[-1]
        rows.append({
            "series_id": series_id,
            "gpu_model": latest["gpu_model"],
            "segment": latest["segment"],
            "operator_tier": latest["operator_tier"],
            "form_factor": latest["form_factor"],
            "interruptibility": latest["interruptibility"],
            "commitment_term": latest["commitment_term"],
            "region": latest["region"],
            "product_class": latest["product_class"],
            "confidence_level": latest["confidence_level"],
            "n_sources": latest["n_sources"],
            "days": len(group),
            "ever_shadow": bool((group["promotion_status"] == "Shadow").any()),
            "max_daily_jump": max_daily_jump(group["price_headline"]),
            "price_latest": float(latest["price_headline"]),
        })
    return pd.DataFrame(rows)


def chip_series(panel: pd.DataFrame, chip: str) -> pd.DataFrame:
    """Date-indexed price series for one chip on the already-filtered surface.

    Raises if the surface does not resolve to exactly one series for the chip.
    A silent groupby mean here would reintroduce the blending problem that the
    surface selector exists to avoid.
    """
    subset = panel[panel["gpu_model"] == chip]
    ids = subset["series_id"].unique()
    if len(ids) == 0:
        raise ValueError(f"No {chip} series on this surface")
    if len(ids) > 1:
        raise ValueError(f"Surface is ambiguous for {chip}: {len(ids)} series match")
    return subset[["as_of_date", "price_headline", "price_p25", "price_p75",
                   "promotion_status", "n_sources", "series_id"]].reset_index(drop=True)


def price_band(panel: pd.DataFrame, series_id: str) -> tuple[float, float] | None:
    """Latest interquartile range for one series, as (p25, p75).

    CCIR publishes p25 and p75 beside every headline precisely because the
    headline alone hides how far providers disagree. A page quoting the headline
    to five figures while the quartiles span nearly two to one is claiming
    precision the panel does not carry.
    """
    rows = panel[panel["series_id"] == series_id].sort_values("as_of_date")
    if rows.empty:
        return None
    latest = rows.iloc[-1]
    p25, p75 = latest.get("price_p25"), latest.get("price_p75")
    if pd.isna(p25) or pd.isna(p75):
        return None
    return float(p25), float(p75)


def basket_band(surface: pd.DataFrame, weights: dict[str, float]) -> tuple[float, float] | None:
    """The basket priced at every component's p25, and at every component's p75.

    Not a confidence interval: it assumes the components move together, which
    is the widest honest reading rather than a distributional claim. It bounds
    what the same basket would cost across the panel's middle half.
    """
    low = high = 0.0
    for chip, weight in weights.items():
        try:
            series = chip_series(surface, chip).sort_values("as_of_date").iloc[-1]
        except ValueError:
            return None
        if pd.isna(series.get("price_p25")) or pd.isna(series.get("price_p75")):
            return None
        low += float(series["price_p25"]) * weight
        high += float(series["price_p75"]) * weight
    return low, high


def build_basket(panel: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Weighted composite across chips on one surface. Inner join on date."""
    frames = []
    for chip in weights:
        series = chip_series(panel, chip).set_index("as_of_date")
        frames.append(series[["price_headline"]].rename(columns={"price_headline": chip}))
    combined = pd.concat(frames, axis=1, join="inner").dropna()
    if combined.empty:
        raise ValueError("Selected chips share no common dates on this surface")
    combined["hardware_basket"] = combined.apply(
        lambda row: weighted_basket({c: row[c] for c in weights}, weights), axis=1
    )
    return combined.reset_index()


def attach_btc(basket: pd.DataFrame, btc: pd.DataFrame, base_date: str | None = None) -> pd.DataFrame:
    """Join BTC/USD and compute the purchasing-power series.

    base_date is resolved against the whole frame before any display window is
    applied, so the index stays comparable when the window changes. BIPP v1
    rebases off the windowed frame, which silently changes the number when you
    switch 30D to 90D.
    """
    btc = btc.copy()
    btc["as_of_date"] = pd.to_datetime(btc["date"], utc=True)
    merged = basket.merge(btc[["as_of_date", "btc_usd"]], on="as_of_date", how="inner")
    if merged.empty:
        raise ValueError("No overlapping dates between compute rates and BTC/USD")
    merged = merged.sort_values("as_of_date").reset_index(drop=True)
    merged["compute_per_btc"] = merged.apply(
        lambda row: compute_per_btc(row["btc_usd"], row["hardware_basket"]), axis=1
    )
    base_index = 0
    if base_date is not None:
        matches = merged.index[merged["as_of_date"].dt.date.astype(str) == base_date].tolist()
        if not matches:
            raise ValueError(f"Base date not found: {base_date}")
        base_index = matches[0]
    merged["bipp"] = normalize_index(merged["compute_per_btc"].tolist(), base_index=base_index)
    return merged


def btc_terms_table(panel: pd.DataFrame, btc: pd.DataFrame, jump_threshold: float = 0.10) -> pd.DataFrame:
    """Every rental series restated in BTC terms: GPU-hours purchasable per BTC.

    This is the composite-free view. No basket weights are involved, so the
    unmeasured 50/30/20 weighting question does not enter the result at all.
    """
    btc = btc.copy()
    btc["as_of_date"] = pd.to_datetime(btc["date"], utc=True)
    btc_map = btc.set_index("as_of_date")["btc_usd"]

    rows = []
    for series_id, group in panel.groupby("series_id"):
        group = group.sort_values("as_of_date").set_index("as_of_date")
        aligned = group.join(btc_map, how="inner").dropna(subset=["btc_usd", "price_headline"])
        if len(aligned) < 2:
            continue
        hours = aligned["btc_usd"] / aligned["price_headline"]
        latest = aligned.iloc[-1]
        rows.append({
            "series_id": series_id,
            "gpu_model": latest["gpu_model"],
            "segment": latest["segment"],
            "commitment_term": latest["commitment_term"],
            "interruptibility": latest["interruptibility"],
            "region": latest["region"],
            "product_class": latest["product_class"],
            "confidence_level": latest["confidence_level"],
            "n_sources": latest["n_sources"],
            "days": len(aligned),
            "usd_per_gpu_hour": float(latest["price_headline"]),
            "gpu_hours_per_btc": float(hours.iloc[-1]),
            "btc_power_index": float(100 * hours.iloc[-1] / hours.iloc[0]),
            "max_daily_jump": max_daily_jump(aligned["price_headline"]),
            "ever_shadow": bool((aligned["promotion_status"] == "Shadow").any()),
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["clean"] = (~table["ever_shadow"]) & (table["max_daily_jump"] < jump_threshold)
    return table.sort_values("btc_power_index", ascending=False).reset_index(drop=True)


# Preference ladder for pricing a single chip. The page's default surface is
# neocloud SXM on demand, which is the cleanest read on datacentre compute but
# excludes consumer cards entirely: a 3090 is only listed by marketplaces (T3)
# and never in an SXM form factor. Rather than show nothing for those, fall
# back a rung at a time and report which rung answered.
RATE_LADDER = [
    ("T2", "SXM", "Neocloud SXM"),
    ("T2", "ALL", "Neocloud"),
    ("T3", "ALL", "Marketplace"),
    ("T1", "ALL", "Hyperscaler"),
]


def best_rate(panel: pd.DataFrame, chip: str, commitment_term: str = "OnDemand",
              region: str = "ALL") -> tuple[float, str, str, int] | None:
    """Latest price for one chip, taking the first rung that resolves cleanly.

    Returns (usd_per_gpu_hour, series_id, market_label, n_sources), or None when
    no rung carries the chip at all.

    n_sources rides along because CCIR publishes every populated cell whatever
    its depth and says depth is the trust signal, calling anything under five
    sources indicative. A caller showing a price should be able to say so
    without a second lookup.
    """
    for tier, form_factor, label in RATE_LADDER:
        subset = select_surface(panel, operator_tier=tier, form_factor=form_factor,
                                interruptibility="ALL", commitment_term=commitment_term,
                                region=region)
        matches = subset[subset["gpu_model"] == chip]
        if matches.empty:
            continue
        chosen = _deepest_panel(matches)
        chosen = chosen.sort_values("as_of_date")
        return (float(chosen["price_headline"].iloc[-1]), chosen["series_id"].iloc[0],
                label, int(chosen["n_sources"].iloc[-1]))
    return None


def _deepest_panel(matches: pd.DataFrame) -> pd.DataFrame:
    """Pick one series when a chip name maps to several on the same surface.

    CCIR's `gpu_model` is not unique per series: an A100 80GB and an A100 40GB
    both carry `gpu_model == "A100"` and are separated only inside the series
    identifier (`CRI-T2-A100-SXM-...` against `CRI-T2-A100-40GB-SXM-...`). So a
    surface that looks unambiguous by chip name can still hold two series, and
    A100 fell out of the priceable list entirely because of it.

    The tie-break is panel depth, which is CCIR's own trust signal: it publishes
    every populated cell whatever its depth and says n is what tells you how
    much to believe a cell. Deepest wins; series_id breaks a tie so the choice
    is stable between runs. Averaging the two instead would be the blending
    mistake the surface selector exists to prevent.

    `chip_series` stays strict and still raises on ambiguity, because the basket
    must never silently pick a variant.
    """
    ids = matches["series_id"].unique()
    if len(ids) == 1:
        return matches
    depth = (matches.groupby("series_id")["n_sources"].max()
             .sort_values(ascending=False).reset_index())
    best = depth[depth["n_sources"] == depth["n_sources"].iloc[0]]["series_id"].min()
    return matches[matches["series_id"] == best]


def priceable_chips(panel: pd.DataFrame, commitment_term: str = "OnDemand",
                    region: str = "ALL") -> list[tuple[str, float, str]]:
    """Every chip that resolves somewhere on the ladder, cheapest hour first."""
    found = []
    for chip in sorted(panel["gpu_model"].dropna().unique()):
        answer = best_rate(panel, chip, commitment_term, region)
        if answer is not None:
            found.append((chip, answer[0], answer[2]))
    return sorted(found, key=lambda row: row[1])
