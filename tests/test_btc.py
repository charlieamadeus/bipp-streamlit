from __future__ import annotations

import pandas as pd
import pytest

from bipp import btc


def make_history() -> pd.Series:
    index = pd.to_datetime(["2023-01-01", "2023-06-01", "2024-01-01", "2026-08-01"])
    series = pd.Series([20000.0, 30000.0, 40000.0, 65000.0], index=index, name="btc_usd")
    return series.sort_index()


def test_price_at_carries_last_close_over_gaps():
    history = make_history()
    assert btc.price_at(history, "2023-06-01") == 30000.0
    assert btc.price_at(history, "2023-09-15") == 30000.0   # no candle that day
    assert btc.price_at(history, "2024-01-01") == 40000.0


def test_debt_priced_at_issue_not_today():
    """The series must move when someone borrows, never when BTC moves."""
    credit = pd.DataFrame({
        "issuer": ["A", "B"],
        "issued": ["2023-01-01", "2024-01-01"],
        "size_musd": [200.0, 400.0],
    })
    stack = btc.debt_in_btc(credit, make_history())
    # $200M at $20k = 10,000 BTC; $400M at $40k = 10,000 BTC.
    assert stack["btc_at_issue"].tolist() == pytest.approx([10000.0, 10000.0])
    assert stack["cumulative_btc"].iloc[-1] == pytest.approx(20000.0)


def test_debt_excludes_rows_before_the_cutoff_and_undated_rows():
    credit = pd.DataFrame({
        "issuer": ["old", "undated", "kept"],
        "issued": ["2019-05-01", "", "2024-01-01"],
        "size_musd": [100.0, 100.0, 400.0],
    })
    stack = btc.debt_in_btc(credit, make_history(), since="2023-01-01")
    assert stack["issuer"].tolist() == ["kept"]


def test_debt_returns_empty_frame_rather_than_wrong_one():
    assert btc.debt_in_btc(pd.DataFrame(), make_history()).empty
    undated = pd.DataFrame({"issuer": ["x"], "issued": [""], "size_musd": [1.0]})
    assert btc.debt_in_btc(undated, make_history()).empty


def test_decompose_satisfies_the_identity():
    """purchasing power = BTC / compute price, so the halves must reconcile."""
    compute_price = pd.Series([4.00, 4.40])          # +10%
    btc_usd = pd.Series([60000.0, 66000.0])          # +10%
    power = btc_usd / compute_price                  # unchanged
    split = btc.decompose(power, btc_usd, compute_price)
    assert split["purchasing_power_pct"] == pytest.approx(0.0, abs=1e-9)
    assert split["btc_pct"] == pytest.approx(10.0)
    assert split["compute_price_pct"] == pytest.approx(10.0)
    assert split["money_share"] == pytest.approx(0.5)


def test_decompose_names_the_larger_mover():
    compute_price = pd.Series([4.00, 4.04])          # +1%
    btc_usd = pd.Series([60000.0, 78000.0])          # +30%
    split = btc.decompose(btc_usd / compute_price, btc_usd, compute_price)
    assert split["driver"] == "money"
    assert split["money_share"] > 0.9

    compute_price = pd.Series([4.00, 2.80])          # -30%
    btc_usd = pd.Series([60000.0, 60600.0])          # +1%
    split = btc.decompose(btc_usd / compute_price, btc_usd, compute_price)
    assert split["driver"] == "compute"
    assert split["purchasing_power_pct"] > 0         # cheaper compute, more per BTC


def test_decompose_rejects_degenerate_input():
    with pytest.raises(ValueError):
        btc.decompose(pd.Series([1.0]), pd.Series([1.0]), pd.Series([1.0]))


def test_fetch_daily_rejects_a_backwards_span():
    with pytest.raises(ValueError, match="start must be before end"):
        btc.fetch_daily("2026-01-01", "2025-01-01")


def test_supply_follows_the_issuance_schedule():
    assert btc.circulating_supply(0) == 50.0
    assert btc.circulating_supply(209_999) == 10_500_000.0          # end of the 50 BTC epoch
    assert btc.circulating_supply(419_999) == 15_750_000.0          # + 210k * 25
    assert btc.circulating_supply(839_999) == 19_687_500.0          # the 2024 halving
    assert btc.circulating_supply(963_218) == pytest.approx(20_072_559, abs=1)
    assert btc.circulating_supply(10_000_000) < btc.TERMINAL_SUPPLY
    with pytest.raises(ValueError):
        btc.circulating_supply(-1)


def test_guarantees_are_not_borrowing():
    """A residual-value guarantee is a contingent obligation, not money drawn.

    One NVIDIA row capped at $105B is 41% of everything CCIR's tracker records
    since 2023. Summing it into "borrowed" overstates the stack by that much.
    """
    credit = pd.DataFrame({
        "issuer": ["CoreWeave", "NVIDIA"],
        "instrument": ["DDTL 5.0", "Residual value guaranties - PORTS-Pike leases"],
        "issued": ["2024-01-01", "2024-01-01"],
        "size_musd": [400.0, 105_000.0],
    })
    drawn, contingent = btc.split_contingent(credit)
    assert drawn["issuer"].tolist() == ["CoreWeave"]
    assert contingent["issuer"].tolist() == ["NVIDIA"]

    stack = btc.debt_in_btc(credit, make_history())
    assert stack["issuer"].tolist() == ["CoreWeave"]
    assert stack["cumulative_btc"].iloc[-1] == pytest.approx(10000.0)

    everything = btc.debt_in_btc(credit, make_history(), exclude_contingent=False)
    assert len(everything) == 2
    assert everything["cumulative_btc"].iloc[-1] > 2_600_000


def test_split_contingent_without_an_instrument_column_keeps_every_row():
    credit = pd.DataFrame({"issuer": ["A"], "issued": ["2024-01-01"], "size_musd": [400.0]})
    drawn, contingent = btc.split_contingent(credit)
    assert len(drawn) == 1 and contingent.empty
