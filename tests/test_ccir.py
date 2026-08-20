from __future__ import annotations

import pandas as pd
import pytest

from bipp import ccir, ccir_pages


def make_panel() -> pd.DataFrame:
    dates = pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"], utc=True)
    rows = []
    for chip, prices in [("H100", [4.0, 4.1, 4.2]), ("H200", [6.0, 6.0, 6.0]), ("B200", [8.0, 8.0, 8.0])]:
        for when, price in zip(dates, prices):
            rows.append({
                "series_id": f"CRI-T2-{chip}-ALL-ALL-OD-ALL", "as_of_date": when,
                "price_headline": price, "price_p25": price * 0.9, "price_p75": price * 1.1,
                "promotion_status": "Published", "gpu_model": chip, "segment": "Neocloud",
                "operator_tier": "T2", "form_factor": "ALL", "interruptibility": "ALL",
                "region": "ALL", "commitment_term": "OnDemand", "product_class": "citable",
                "n_sources": 5, "confidence_level": "High",
            })
    return pd.DataFrame(rows)


def make_btc() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]).date,
        "btc_usd": [60000.0, 61000.0, 62000.0],
    })


def test_max_daily_jump_finds_step_change():
    assert ccir.max_daily_jump(pd.Series([14.0, 5.23, 5.24])) == pytest.approx(0.6264, abs=1e-3)
    assert ccir.max_daily_jump(pd.Series([4.0, 4.02, 4.01])) < 0.01
    assert ccir.max_daily_jump(pd.Series([4.0])) == 0.0


def test_max_daily_jump_ignores_nonpositive_prior():
    assert ccir.max_daily_jump(pd.Series([0.0, 5.0, 5.1])) == pytest.approx(0.02, abs=1e-6)


def test_chip_series_refuses_ambiguous_surface():
    panel = make_panel()
    duplicate = panel[panel["gpu_model"] == "H100"].copy()
    duplicate["series_id"] = "CRI-T2-H100-SXM-ALL-OD-ALL"
    with pytest.raises(ValueError, match="ambiguous"):
        ccir.chip_series(pd.concat([panel, duplicate]), "H100")


def test_chip_series_requires_the_chip():
    with pytest.raises(ValueError, match="No GB200"):
        ccir.chip_series(make_panel(), "GB200")


def test_build_basket_applies_weights():
    basket = ccir.build_basket(make_panel(), {"H100": 0.5, "H200": 0.3, "B200": 0.2})
    assert basket["hardware_basket"].iloc[0] == pytest.approx(0.5 * 4.0 + 0.3 * 6.0 + 0.2 * 8.0)
    assert len(basket) == 3


def test_base_date_is_resolved_before_windowing():
    """v1's defect: rebasing off the windowed frame silently changes the index."""
    basket = ccir.build_basket(make_panel(), {"H100": 0.5, "H200": 0.3, "B200": 0.2})
    btc = make_btc()
    full = ccir.attach_btc(basket, btc, base_date="2026-08-02")
    windowed = ccir.attach_btc(basket.iloc[1:].reset_index(drop=True), btc, base_date="2026-08-02")
    assert full.loc[full["as_of_date"].dt.date.astype(str) == "2026-08-02", "bipp"].iloc[0] == 100.0
    assert full["bipp"].iloc[-1] == pytest.approx(windowed["bipp"].iloc[-1])


def test_attach_btc_rejects_disjoint_dates():
    basket = ccir.build_basket(make_panel(), {"H100": 1.0})
    btc = pd.DataFrame({"date": pd.to_datetime(["2025-01-01"]).date, "btc_usd": [50000.0]})
    with pytest.raises(ValueError, match="No overlapping dates"):
        ccir.attach_btc(basket, btc)


def test_btc_terms_table_needs_no_weights():
    table = ccir.btc_terms_table(make_panel(), make_btc())
    assert set(table["gpu_model"]) == {"H100", "H200", "B200"}
    h100 = table[table["gpu_model"] == "H100"].iloc[0]
    assert h100["gpu_hours_per_btc"] == pytest.approx(62000.0 / 4.2)
    assert h100["btc_power_index"] == pytest.approx(100 * (62000.0 / 4.2) / (60000.0 / 4.0))
    assert bool(h100["clean"]) is True


def test_select_surface_rejects_unknown_dimension():
    with pytest.raises(ValueError, match="Unknown surface dimension"):
        ccir.select_surface(make_panel(), gpu_model="H100")


def test_money_does_not_read_a_grade_marker_as_billions():
    assert ccir_pages._money("76.6% [B]") == pytest.approx(76.6)
    assert ccir_pages._money("$27,155") == pytest.approx(27155.0)
    assert ccir_pages._money("1.41x") == pytest.approx(1.41)
    assert ccir_pages._money("") is None


def test_normalize_chip_matches_rental_names():
    assert ccir_pages.normalize_chip("H100 80GB SXM5") == "H100"
    assert ccir_pages.normalize_chip("RTX A6000 48GB") == "RTX"
    assert ccir_pages.normalize_chip("") == ""


def test_btc_denomination_round_trips():
    hardware = pd.DataFrame({
        "model": ["H100 80GB SXM5"], "age_years": [3.8],
        "executed_median_usd": [14000.0], "posted_ask_usd": [25150.0],
    })
    priced = ccir_pages.gpus_per_btc(hardware, 70000.0)
    assert priced["gpus_per_btc_executed"].iloc[0] == pytest.approx(5.0)

    payback = ccir_pages.payback_hours(hardware, {"H100": 3.5})
    assert payback["payback_hours"].iloc[0] == pytest.approx(4000.0)


def test_credit_summary_scales_millions_to_btc():
    credit = pd.DataFrame({"issuer": ["A", "B"], "size_musd": [1000.0, 1000.0]})
    summary = ccir_pages.credit_summary(credit, 50000.0)
    assert summary["notional_usd"] == pytest.approx(2e9)
    assert summary["btc_equivalent"] == pytest.approx(40000.0)
    assert summary["pct_of_terminal_supply"] == pytest.approx(100 * 40000 / 21_000_000)


def make_token_page() -> str:
    def table(rows):
        head = "<tr><th>Model</th><th>Input</th><th>Cached</th><th>Output</th><th>30d</th><th>Last reprice</th></tr>"
        body = "".join(
            f"<tr><td>{m}</td><td>${i}</td><td>-</td><td>${o}</td><td></td><td>none</td></tr>"
            for m, i, o in rows
        )
        return f"<table>{head}{body}</table>"
    return (
        "<h2>Frontier posted</h2>"
        "<h3>OA OpenAI standard tier</h3>"
        + table([("gpt-5.5-pro", 20, 180), ("gpt-5.6-sol", 5, 30),
                 ("gpt-4-turbo-2024-04-09", 10, 30), ("text-embedding-3", 0.1, 0.1)])
        + "<h3>AN Anthropic model table</h3>"
        + table([("Claude Opus 5", 5, 25), ("Claude Fable 5", 10, 50)])
        + "<h3>ZA Z.AI model table</h3>"
        + table([("GLM-OCR", 0.01, 0.03), ("GLM-4-32B", 0.05, 0.10)])
    )


def test_tokens_capture_the_provider_heading():
    frame = ccir_pages.fetch_tokens(make_token_page())
    assert set(frame["provider"].unique()) == {
        "OA OpenAI standard tier", "AN Anthropic model table", "ZA Z.AI model table"
    }


def test_flagships_come_back_in_curated_order():
    """Order is a judgement about which lab sets the standard, so it is pinned."""
    page = make_token_page() + (
        "<h3>AN Anthropic model table</h3>"
        "<table><tr><th>Model</th><th>Input</th><th>Cached</th><th>Output</th>"
        "<th>30d</th><th>Last reprice</th></tr>"
        "<tr><td>Claude Sonnet 5</td><td>$2</td><td>-</td><td>$10</td><td></td><td>none</td></tr>"
        "</table>"
    )
    frame = ccir_pages.frontier_models(ccir_pages.fetch_tokens(page))
    models = frame["model"].tolist()

    # Fable leads, then the rest of Anthropic, then OpenAI: not price order.
    assert models[0] == "Claude Fable 5"
    assert models == ["Claude Fable 5", "Claude Opus 5", "Claude Sonnet 5", "gpt-5.6-sol"]
    assert frame["lab"].tolist()[:3] == ["Anthropic"] * 3
    # Cheaper Sonnet outranks pricier gpt-5.6-sol because the lab order says so.
    assert frame.loc[2, "output_usd_per_mtok"] < frame.loc[3, "output_usd_per_mtok"]


def test_flagships_drop_names_ccir_stopped_publishing():
    frame = ccir_pages.frontier_models(ccir_pages.fetch_tokens(make_token_page()))
    # The fixture has no Sonnet, Gemini, Grok, DeepSeek or Kimi row.
    assert frame["model"].tolist() == ["Claude Fable 5", "Claude Opus 5", "gpt-5.6-sol"]


def test_flagships_ignore_everything_not_on_the_list():
    frame = ccir_pages.frontier_models(ccir_pages.fetch_tokens(make_token_page()))
    listed = frame["model"].tolist()
    for noise in ["gpt-5.5-pro", "gpt-4-turbo-2024-04-09", "text-embedding-3",
                  "GLM-OCR", "GLM-4-32B"]:
        assert noise not in listed


def test_frontier_excludes_cross_provider_medians():
    """A posted price and a cross-provider median answer different questions,
    and CCIR never pools them. Neither does the flagship list."""
    page = make_token_page() + (
        "<h3>OA OpenAI standard tier</h3>"
        "<table><tr><th>Model</th><th>Providers</th><th>Input median</th>"
        "<th>Output median</th><th>Output range</th></tr>"
        "<tr><td>llama-4-405b</td><td>7</td><td>$1</td><td>$900</td><td>-</td></tr></table>"
    )
    frame = ccir_pages.frontier_models(ccir_pages.fetch_tokens(page))
    assert "llama-4-405b" not in frame["model"].tolist()


def make_ladder_panel() -> pd.DataFrame:
    """A datacentre chip on SXM, and a consumer card only a marketplace lists."""
    when = pd.to_datetime(["2026-08-19"], utc=True)
    rows = []
    for chip, tier, form, price in [("H100", "T2", "SXM", 3.57), ("H100", "T2", "ALL", 3.48),
                                    ("3090", "T3", "ALL", 0.20)]:
        rows.append({
            "series_id": f"CRI-{tier}-{chip}-{form}-ALL-OD-ALL", "as_of_date": when[0],
            "price_headline": price, "price_p25": price, "price_p75": price,
            "promotion_status": "Published", "gpu_model": chip, "segment": "x",
            "operator_tier": tier, "form_factor": form, "interruptibility": "ALL",
            "region": "ALL", "commitment_term": "OnDemand", "product_class": "citable",
            "n_sources": 4, "confidence_level": "High",
        })
    return pd.DataFrame(rows)


def test_ladder_prefers_the_cleanest_rung():
    panel = make_ladder_panel()
    rate, series_id, market = ccir.best_rate(panel, "H100")
    assert rate == 3.57 and market == "Neocloud SXM"
    assert series_id == "CRI-T2-H100-SXM-ALL-OD-ALL"


def test_ladder_falls_through_for_a_consumer_card():
    """A 3090 has no SXM form factor and no neocloud tier. Without the ladder
    it would show nothing at all."""
    rate, _, market = ccir.best_rate(make_ladder_panel(), "3090")
    assert rate == 0.20 and market == "Marketplace"


def _panel_with_a_twin_series(price, n_sources):
    """Two series sharing one gpu_model on one rung, as A100 really does."""
    panel = make_ladder_panel()
    twin = panel[panel["gpu_model"] == "3090"].copy()
    twin["series_id"] = "CRI-T3-3090-40GB-ALL-ALL-OD-ALL"
    twin["form_factor"] = "ALL"          # same rung, two series
    twin["price_headline"] = price
    twin["n_sources"] = n_sources
    return panel, twin


def test_ladder_picks_the_deepest_panel_rather_than_averaging():
    # Changed deliberately from "return None". CCIR reuses one gpu_model across
    # capacity variants (A100 80GB and A100 40GB are both "A100"), so refusing
    # on collision dropped a major chip off the page entirely. Panel depth is
    # CCIR's own trust signal, so the deeper series wins.
    panel, twin = _panel_with_a_twin_series(price=99.0, n_sources=1)
    original = ccir.best_rate(panel, "3090")
    combined = ccir.best_rate(pd.concat([panel, twin]), "3090")
    assert combined is not None
    assert combined[0] == original[0], "picked the shallow twin"
    assert combined[1] == original[1]


def test_ladder_never_averages_two_series():
    # The invariant the old test was really protecting: whatever is returned is
    # one series' own price, never a blend of two.
    panel, twin = _panel_with_a_twin_series(price=99.0, n_sources=999)
    price, series_id, _ = ccir.best_rate(pd.concat([panel, twin]), "3090")
    assert price == 99.0, "should be the deep twin's own price"
    assert series_id == "CRI-T3-3090-40GB-ALL-ALL-OD-ALL"


def test_chip_series_still_refuses_an_ambiguous_surface():
    # best_rate got a tie-break; the basket path must not. A silent pick there
    # would put an unannounced variant inside a weighted composite.
    panel, twin = _panel_with_a_twin_series(price=99.0, n_sources=999)
    both = pd.concat([panel, twin])
    surface = ccir.select_surface(both, operator_tier="T3", form_factor="ALL",
                                  interruptibility="ALL", commitment_term="OnDemand",
                                  region="ALL")
    with pytest.raises(ValueError, match="ambiguous"):
        ccir.chip_series(surface, "3090")


def test_ladder_returns_none_for_an_unknown_chip():
    assert ccir.best_rate(make_ladder_panel(), "GB200") is None


def test_priceable_chips_is_cheapest_first():
    found = ccir.priceable_chips(make_ladder_panel())
    assert [chip for chip, _, _ in found] == ["3090", "H100"]
    assert found[0][1] < found[1][1]
