from __future__ import annotations

import pandas as pd
import pytest

from bipp import economics
from bipp.economics import CarryAssumptions

# H100 80GB SXM5 as CCIR reports it on 2026-08-19.
H100_SXM5_USED = 14000.0
H100_SXM5_PCT_OF_LAUNCH = 41.7
H100_SXM5_AGE = 3.8
H100_SXM5_LAUNCH = H100_SXM5_USED / (H100_SXM5_PCT_OF_LAUNCH / 100)
RATE_3Y = 2.37


def test_launch_price_reconstruction():
    assert economics.launch_price(H100_SXM5_USED, H100_SXM5_PCT_OF_LAUNCH) == pytest.approx(33573, abs=1)


def test_used_curve_depreciation_matches_ccir_marks():
    rate = economics.used_curve_depreciation(H100_SXM5_PCT_OF_LAUNCH, H100_SXM5_AGE)
    assert rate == pytest.approx(0.206, abs=0.002)
    # The successor generation depreciates far slower on this curve, which is
    # exactly why the curve is a poor forward estimate.
    assert economics.used_curve_depreciation(86.2, 2.3) == pytest.approx(0.063, abs=0.002)


def test_covenant_depreciation_is_straight_line_to_zero():
    assert economics.covenant_depreciation(3.5) == pytest.approx(1 / 3.5)
    assert economics.covenant_depreciation(3.0) == pytest.approx(0.333, abs=0.001)


def test_depreciation_basis_flips_the_sign():
    """The Grok 4.6 finding of 2026-08-19, pinned.

    Same chip, same committed rate, same opex, same cost of debt. Only the
    depreciation basis changes, and the conclusion inverts.
    """
    assumptions = CarryAssumptions(utilization=0.90, opex_share=0.45, cost_of_debt_pct=6.88)

    used = economics.carry(
        RATE_3Y, H100_SXM5_LAUNCH,
        economics.used_curve_depreciation(H100_SXM5_PCT_OF_LAUNCH, H100_SXM5_AGE),
        assumptions,
    )
    covenant = economics.carry(
        RATE_3Y, H100_SXM5_LAUNCH, economics.covenant_depreciation(3.0), assumptions,
    )

    assert used["carry_pp"] > 0
    assert covenant["carry_pp"] < 0
    assert used["carry_pp"] == pytest.approx(3.17, abs=0.05)
    assert covenant["carry_pp"] == pytest.approx(-9.60, abs=0.05)


def test_rate_and_utilization_must_move_together():
    """Rate and billed hours are one choice, not two.

    On-demand list does not come with 90% billed utilization, and a committed
    book does. Comparing rates at a fixed utilization manufactures a sign flip
    that belongs to the utilization, not the rate. Priced in matching pairs,
    the rate costs about 4pp of carry; the depreciation basis costs 13pp.
    """
    depreciation = economics.used_curve_depreciation(H100_SXM5_PCT_OF_LAUNCH, H100_SXM5_AGE)

    on_demand = economics.carry(
        3.475, H100_SXM5_LAUNCH, depreciation,
        CarryAssumptions(utilization=0.70, opex_share=0.45),
    )
    committed = economics.carry(
        RATE_3Y, H100_SXM5_LAUNCH, depreciation,
        CarryAssumptions(utilization=0.90, opex_share=0.45),
    )
    assert on_demand["carry_pp"] > 0 and committed["carry_pp"] > 0
    assert on_demand["carry_pp"] - committed["carry_pp"] == pytest.approx(4.3, abs=0.5)

    # Mispairing the committed rate with on-demand occupancy flips the sign on
    # its own. That is the trap this test exists to document.
    mispaired = economics.carry(
        RATE_3Y, H100_SXM5_LAUNCH, depreciation,
        CarryAssumptions(utilization=0.70, opex_share=0.45),
    )
    assert mispaired["carry_pp"] < 0


def test_capex_multiple_scales_the_denominator():
    assumptions = CarryAssumptions(capex_multiple=1.5)
    bare = economics.carry(RATE_3Y, 10000.0, 0.30, CarryAssumptions())
    deployed = economics.carry(RATE_3Y, 10000.0, 0.30, assumptions)
    assert deployed["deployed_capex_usd"] == pytest.approx(15000.0)
    assert deployed["gross_yield_pct"] == pytest.approx(bare["gross_yield_pct"] / 1.5)


def test_breakeven_rent_is_the_zero_carry_point():
    assumptions = CarryAssumptions(utilization=0.90, opex_share=0.45, cost_of_debt_pct=6.88)
    depreciation = economics.covenant_depreciation(3.0)
    breakeven = economics.breakeven_rent(H100_SXM5_LAUNCH, depreciation, assumptions)
    result = economics.carry(breakeven, H100_SXM5_LAUNCH, depreciation, assumptions)
    assert result["carry_pp"] == pytest.approx(0.0, abs=1e-9)
    # And the committed rate sits below it, which is the finding.
    assert RATE_3Y < breakeven


def test_assumptions_reject_impossible_values():
    for bad in [CarryAssumptions(utilization=0.0), CarryAssumptions(utilization=1.5),
                CarryAssumptions(opex_share=1.0), CarryAssumptions(capex_multiple=0.5),
                CarryAssumptions(depreciation_basis="vibes")]:
        with pytest.raises(ValueError):
            bad.validate()


def make_catalog() -> pd.DataFrame:
    rows = []
    for term, price in [("OnDemand", 3.48), ("1Y", 2.69), ("3Y", 2.37)]:
        rows.append({"gpu_model": "H100", "operator_tier": "T2", "form_factor": "ALL",
                     "interruptibility": "ALL", "commitment_term": term, "region": "ALL",
                     "price_headline": price, "n_sources": 22})
    return pd.DataFrame(rows)


def test_commitment_ratio_matches_the_published_prints():
    ratio = economics.commitment_ratio(make_catalog(), "H100")
    assert ratio["ratio"] == pytest.approx(2.69 / 3.48, abs=0.001)
    assert ratio["discount_pct"] == pytest.approx(22.7, abs=0.5)


def test_commitment_ratio_returns_none_when_unpaired():
    assert economics.commitment_ratio(make_catalog(), "GB200") is None


def test_term_structure_is_ordered_not_alphabetical():
    curve = economics.term_structure(make_catalog(), "H100")
    assert curve["term"].tolist() == ["OnDemand", "1Y", "3Y"]
    assert curve["usd_per_gpu_hour"].is_monotonic_decreasing


def test_credit_spreads_exclude_fixed_coupons():
    credit = pd.DataFrame({
        "rate": ["SOFR +9.62%", "SOFR +2.88%", "6.50% fixed", "SOFR +4.25%"],
        "issued": ["2023-07-30", "2026-03-31", "2025-01-01", "2026-06-01"],
        "size_musd": [1438.0, 8500.0, 500.0, 3000.0],
    })
    result = economics.credit_spreads_by_vintage(credit)
    assert result["vintage"].tolist() == ["2023", "2026"]
    assert result.loc[result["vintage"] == "2026", "instruments"].iloc[0] == 2
    assert result.loc[result["vintage"] == "2026", "median_spread_pct"].iloc[0] == pytest.approx(3.565)
