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


# Real chain observations read from mempool.space on 2026-08-20. supply_at is a
# model, so it is pinned against measured heights rather than against itself.
OBSERVED_HEIGHTS = {
    "2023-01-01": 769_786,
    "2023-09-01": 805_651,
    "2024-04-20": 839_998,
    "2025-03-01": 885_783,
    "2026-01-01": 930_340,
    "2026-08-01": 960_481,
}


def test_supply_at_tracks_observed_chain_heights():
    for date, height in OBSERVED_HEIGHTS.items():
        actual = btc.circulating_supply(height)
        error = abs(btc.supply_at(date) - actual)
        assert error / actual < 0.0005, f"{date}: off by {error:,.0f} BTC"


def test_supply_at_is_monotonic():
    dates = ["2023-01-01", "2024-01-01", "2025-01-01", "2026-01-01", "2026-08-20"]
    values = [btc.supply_at(d) for d in dates]
    assert values == sorted(values)
    assert all(a != b for a, b in zip(values, values[1:]))


def test_supply_at_never_reaches_terminal_supply():
    # The fallback used to be TERMINAL_SUPPLY, which silently moved every
    # percentage on the borrow card by 4.6 percent when a fetch failed.
    assert btc.supply_at(btc.SUPPLY_FALLBACK_DATE) < btc.TERMINAL_SUPPLY
    assert btc.supply_at("2026-08-20") > 20_000_000


def test_supply_at_accepts_naive_and_aware_timestamps():
    naive = btc.supply_at(pd.Timestamp("2025-06-01"))
    aware = btc.supply_at(pd.Timestamp("2025-06-01", tz="UTC"))
    assert naive == aware


def test_cumulative_share_freezes_each_deal_at_issue():
    # The property the chart depends on: a deal's contribution is fixed the day
    # it is signed. Adding a later deal must not move any earlier point.
    history = pd.Series(
        [20_000.0, 40_000.0, 80_000.0],
        index=pd.to_datetime(["2024-01-01", "2025-01-01", "2026-01-01"]),
    )
    two = pd.DataFrame({
        "issuer": ["A", "B"], "instrument": ["loan", "loan"],
        "size_musd": [1_000.0, 1_000.0], "issued": ["2024-01-01", "2025-01-01"],
    })
    three = pd.concat([two, pd.DataFrame({
        "issuer": ["C"], "instrument": ["loan"],
        "size_musd": [5_000.0], "issued": ["2026-01-01"],
    })], ignore_index=True)

    a = btc.debt_in_btc(two, history, since="2024-01-01")
    b = btc.debt_in_btc(three, history, since="2024-01-01")
    assert a["cumulative_share"].iloc[0] == pytest.approx(b["cumulative_share"].iloc[0])
    assert a["cumulative_share"].iloc[1] == pytest.approx(b["cumulative_share"].iloc[1])
    assert b["cumulative_share"].iloc[2] > b["cumulative_share"].iloc[1]


def test_share_at_issue_is_debt_over_market_cap_on_the_day():
    history = pd.Series([50_000.0], index=pd.to_datetime(["2025-06-01"]))
    frame = pd.DataFrame({
        "issuer": ["A"], "instrument": ["loan"],
        "size_musd": [1_000.0], "issued": ["2025-06-01"],
    })
    row = btc.debt_in_btc(frame, history, since="2025-01-01").iloc[0]
    market_cap = 50_000.0 * btc.supply_at("2025-06-01")
    assert row["share_at_issue"] == pytest.approx(1_000.0 * 1e6 / market_cap)


def _two_deal_stack():
    history = pd.Series(
        [25_000.0] * 200 + [50_000.0] * 200,
        index=pd.date_range("2025-01-01", periods=400, freq="D"),
    )
    frame = pd.DataFrame({
        "issuer": ["A", "B"], "instrument": ["loan", "loan"],
        "size_musd": [1_000.0, 1_000.0],
        "issued": ["2025-01-10", "2025-08-01"],
    })
    return btc.debt_in_btc(frame, history, since="2025-01-01"), history


def test_debt_share_series_committed_line_only_steps_on_a_deal():
    stack, history = _two_deal_stack()
    series = btc.debt_share_series(stack, history, end="2025-12-31")
    quiet = series[(series["date"] > "2025-02-01") & (series["date"] < "2025-07-01")]
    assert quiet["committed"].nunique() == 1, "committed moved with no deal signed"
    assert series["committed"].iloc[-1] > quiet["committed"].iloc[0]


def test_debt_share_series_marked_line_moves_when_bitcoin_does():
    stack, history = _two_deal_stack()
    series = btc.debt_share_series(stack, history, end="2025-12-31")
    quiet = series[(series["date"] > "2025-02-01") & (series["date"] < "2025-07-01")]
    assert quiet["marked"].nunique() > 1, "marked ignored a doubling of the price"


def test_debt_share_series_lines_agree_on_the_signing_day():
    # Same price, same supply, so the two readings are the same number. They
    # separate from the next day on, and not only when the price moves: supply
    # keeps growing, so market cap grows even at a flat price.
    stack, history = _two_deal_stack()
    series = btc.debt_share_series(stack, history, end="2025-02-01")
    first = series.iloc[0]
    assert first["committed"] == pytest.approx(first["marked"], rel=1e-9)

    later = series.iloc[-1]
    assert later["marked"] < later["committed"], "flat price, growing supply should dilute"
    assert later["marked"] == pytest.approx(later["committed"], rel=0.01)


def test_debt_share_series_is_empty_for_an_empty_stack():
    assert btc.debt_share_series(pd.DataFrame(), pd.Series(dtype=float)).empty


def test_debt_share_series_runs_to_the_last_priced_day_not_the_last_deal():
    stack, history = _two_deal_stack()          # last deal 2025-08-01
    series = btc.debt_share_series(stack, history)
    assert series["date"].iloc[-1] == history.index.max()
    assert series["date"].iloc[-1] > stack["issued_on"].max()


def test_debt_share_series_honours_an_explicit_end():
    stack, history = _two_deal_stack()
    series = btc.debt_share_series(stack, history, end="2025-03-01")
    assert series["date"].iloc[-1] == pd.Timestamp("2025-03-01")


# CCIR writes the `issued` column as prose. These are real values from the
# tracker; every one of them was silently dropped before parse_issue_date.
REAL_ISSUED_CELLS = [
    ("Reported 2025-10-16", "2025-10-16", "day"),
    ("Priced 2025-10-16; JV announced 2025-10-21", "2025-10-16", "day"),
    ("Finalized ~2026-06-08 (press; no filing)", "2026-06-08", "day"),
    ("Closed; PR 2026-01-07 (funds provided ~Nov)", "2026-01-07", "day"),
    ("Underwrite reported 2025-08-20; distribution ongoing", "2025-08-20", "day"),
    ("Priced ~2026-04-16/17", "2026-04-16", "day"),
    ("2026-07-28", "2026-07-28", "day"),
    ("2025-09", "2025-09-01", "month"),
    ("2024", "2024-01-01", "year"),
    ("H2 2025", "2025-01-01", "year"),
]


@pytest.mark.parametrize("cell,expected,precision", REAL_ISSUED_CELLS)
def test_parse_issue_date_reads_ccir_prose(cell, expected, precision):
    got, got_precision = btc.parse_issue_date(cell)
    assert str(got.date()) == expected
    assert got_precision == precision


@pytest.mark.parametrize("cell", ["", "-", "—", None, "Undisclosed", "TBD"])
def test_parse_issue_date_reports_no_date_rather_than_guessing(cell):
    got, precision = btc.parse_issue_date(cell)
    assert pd.isna(got)
    assert precision == "none"


def test_parse_issue_date_takes_the_first_date_not_the_last():
    # CCIR writes these in event order, so the pricing date leads. Taking the
    # last would date a deal by whenever it was last written about.
    got, _ = btc.parse_issue_date("Priced 2025-10-16; JV announced 2025-10-21")
    assert str(got.date()) == "2025-10-16"


def test_debt_coverage_accounts_for_every_row():
    """No row may vanish. Shown plus excluded must equal the whole tracker."""
    credit = pd.DataFrame({
        "issuer": ["A", "B", "C", "D"],
        "instrument": ["loan", "loan", "guarantee for X", "loan"],
        "size_musd": [100.0, 200.0, 300.0, 400.0],
        "issued": ["2025-01-01", "Undisclosed", "2025-06-01", "2019-01-01"],
    })
    cov = btc.debt_coverage(credit, since="2023-01-01")
    excluded_rows = sum(n for n, _ in cov["excluded"].values())
    excluded_usd = sum(usd for _, usd in cov["excluded"].values())
    assert cov["rows_shown"] + excluded_rows == cov["rows_total"] == 4
    assert cov["usd_shown"] + excluded_usd == pytest.approx(cov["usd_total"]) == pytest.approx(1000.0)


def test_debt_coverage_names_a_reason_for_every_exclusion():
    credit = pd.DataFrame({
        "issuer": ["A", "B"], "instrument": ["loan", "loan"],
        "size_musd": [100.0, 200.0], "issued": ["Undisclosed", "2019-01-01"],
    })
    cov = btc.debt_coverage(credit, since="2023-01-01")
    reasons = {r for r, (n, _) in cov["excluded"].items() if n}
    assert reasons == {"no date in the cell", "issued before 2023-01-01"}
