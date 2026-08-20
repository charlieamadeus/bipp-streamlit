"""Compute unit economics: what a GPU earns against what it costs to finance.

Every function here takes its assumptions as arguments. Nothing is hardcoded,
because a Grok 4.6 review on 2026-08-19 established that the depreciation basis
alone flips the sign of the answer, and an assumption that flips the sign must
be visible in the interface rather than buried in a module.

The review's finding, reproduced in tests: on an H100 80GB SXM5 at the 3-year
committed rate, used-price depreciation gives +3pp of carry and covenant-style
amortization gives -9pp. Same rate, same opex, opposite conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

HOURS_PER_YEAR = 8760

# CCIR's credit page: no GPU-collateralized instrument in the tracker references
# a market residual, and every disclosed schedule amortizes collateral toward
# zero, typically on a three-to-four year cash clock.
COVENANT_AMORTIZATION_YEARS = 3.5


@dataclass(frozen=True)
class CarryAssumptions:
    """Everything that is judgment rather than measurement."""

    utilization: float = 0.90
    opex_share: float = 0.45
    cost_of_debt_pct: float = 6.88
    capex_multiple: float = 1.0        # 1.0 = bare card; >1 adds node, network, power
    depreciation_basis: str = "covenant"  # "covenant" | "used_curve"

    def validate(self) -> None:
        if not 0 < self.utilization <= 1:
            raise ValueError("Utilization must be in (0, 1]")
        if not 0 <= self.opex_share < 1:
            raise ValueError("Opex share must be in [0, 1)")
        if self.capex_multiple < 1:
            raise ValueError("Capex multiple must be at least 1.0")
        if self.depreciation_basis not in ("covenant", "used_curve"):
            raise ValueError("Depreciation basis must be 'covenant' or 'used_curve'")


def used_curve_depreciation(pct_of_launch: float, age_years: float) -> float:
    """Annual value decay implied by the secondary market, as a fraction.

    Honest about what it is: a geometric fit through the whole life, which
    averages the launch-scarcity years against the post-successor crash. It is
    a recovery estimate for a single card sold on a marketplace, not the
    depreciation that hits an income statement.
    """
    if not 0 < pct_of_launch <= 100:
        raise ValueError("pct_of_launch must be in (0, 100]")
    if age_years <= 0:
        raise ValueError("age_years must be positive")
    return 1 - (pct_of_launch / 100) ** (1 / age_years)


def covenant_depreciation(amortization_years: float = COVENANT_AMORTIZATION_YEARS) -> float:
    """Straight-line cash amortization of collateral to zero."""
    if amortization_years <= 0:
        raise ValueError("amortization_years must be positive")
    return 1 / amortization_years


def launch_price(executed_median_usd: float, pct_of_launch: float) -> float:
    """Reconstruct the launch basis from the residual mark.

    This is a reconstruction, not a delivered price, and CCIR grades its
    confidence per chip. Callers should surface that grade.
    """
    if executed_median_usd <= 0:
        raise ValueError("executed_median_usd must be positive")
    if not 0 < pct_of_launch <= 100:
        raise ValueError("pct_of_launch must be in (0, 100]")
    return executed_median_usd / (pct_of_launch / 100)


def carry(
    rent_usd_per_hour: float,
    capex_usd: float,
    depreciation_rate: float,
    assumptions: CarryAssumptions,
) -> dict[str, float]:
    """Gross yield, net yield, and spread over the cost of debt, all percent."""
    assumptions.validate()
    if rent_usd_per_hour <= 0 or capex_usd <= 0:
        raise ValueError("Rent and capex must be positive")

    deployed = capex_usd * assumptions.capex_multiple
    gross = rent_usd_per_hour * HOURS_PER_YEAR * assumptions.utilization / deployed * 100
    net = gross * (1 - assumptions.opex_share) - depreciation_rate * 100
    return {
        "deployed_capex_usd": deployed,
        "gross_yield_pct": gross,
        "depreciation_pct": depreciation_rate * 100,
        "net_yield_pct": net,
        "carry_pp": net - assumptions.cost_of_debt_pct,
    }


def breakeven_rent(
    capex_usd: float,
    depreciation_rate: float,
    assumptions: CarryAssumptions,
) -> float:
    """Rental rate at which carry is exactly zero."""
    assumptions.validate()
    deployed = capex_usd * assumptions.capex_multiple
    required_gross = (assumptions.cost_of_debt_pct + depreciation_rate * 100) / (1 - assumptions.opex_share)
    return required_gross / 100 * deployed / (HOURS_PER_YEAR * assumptions.utilization)


def commitment_ratio(catalog: pd.DataFrame, chip: str, tier: str = "T2",
                     form_factor: str = "ALL", region: str = "ALL",
                     term: str = "1Y") -> dict[str, float] | None:
    """Committed rate divided by on-demand rate, for one chip.

    Grok's suggested monthly read, and the cheapest honest one available: it
    needs no capex estimate, no opex assumption, and no residual sample. A
    falling ratio means operators are paying more to lock occupancy and the
    spot list is drifting away from what clears.
    """
    def rate(commitment: str) -> float | None:
        match = catalog[
            (catalog["gpu_model"] == chip)
            & (catalog["operator_tier"] == tier)
            & (catalog["form_factor"] == form_factor)
            & (catalog["interruptibility"] == "ALL")
            & (catalog["commitment_term"] == commitment)
            & (catalog["region"] == region)
        ]
        return float(match.iloc[0]["price_headline"]) if not match.empty else None

    on_demand, committed = rate("OnDemand"), rate(term)
    if not on_demand or not committed:
        return None
    return {
        "on_demand": on_demand,
        "committed": committed,
        "ratio": committed / on_demand,
        "discount_pct": (1 - committed / on_demand) * 100,
    }


def term_structure(catalog: pd.DataFrame, chip: str, tier: str = "T2",
                   form_factor: str = "ALL", region: str = "ALL") -> pd.DataFrame:
    """Full forward curve for one chip, on-demand through 3-year."""
    order = ["OnDemand", "1M", "3M", "6M", "1Y", "2Y", "3Y"]
    rows = []
    for term in order:
        match = catalog[
            (catalog["gpu_model"] == chip)
            & (catalog["operator_tier"] == tier)
            & (catalog["form_factor"] == form_factor)
            & (catalog["interruptibility"] == "ALL")
            & (catalog["commitment_term"] == term)
            & (catalog["region"] == region)
        ]
        if not match.empty:
            rows.append({
                "term": term,
                "usd_per_gpu_hour": float(match.iloc[0]["price_headline"]),
                "n_sources": match.iloc[0]["n_sources"],
            })
    return pd.DataFrame(rows)


def credit_spreads_by_vintage(credit: pd.DataFrame) -> pd.DataFrame:
    """Median SOFR spread and volume by issue year.

    Fixed-coupon and floating instruments are not pooled: only SOFR-spread
    instruments appear, because a coupon and a spread are different quantities.
    """
    import re

    if credit.empty:
        return pd.DataFrame()
    working = credit.copy()
    working["sofr_spread"] = working["rate"].map(
        lambda text: float(m.group(1)) if (m := re.search(r"SOFR\s*\+\s*(\d+(?:\.\d+)?)", text or "")) else None
    )
    working["vintage"] = working["issued"].astype(str).str[:4]
    working = working.dropna(subset=["sofr_spread"])
    working = working[working["vintage"].str.fullmatch(r"\d{4}")]
    if working.empty:
        return pd.DataFrame()
    grouped = working.groupby("vintage").agg(
        instruments=("sofr_spread", "size"),
        median_spread_pct=("sofr_spread", "median"),
        notional_musd=("size_musd", "sum"),
    ).reset_index()
    return grouped.sort_values("vintage").reset_index(drop=True)
