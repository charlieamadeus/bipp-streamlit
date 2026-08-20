"""BIPP v2: Bitcoin against the compute capital stack.

v1 asked one question: how many composite GPU-hours does one BTC buy, using
Ornn's index. v2 keeps that question, sources it from CCIR instead so panel
changes are visible, and adds the three other prices of compute that CCIR
publishes. Rent, own, borrow, and output are four different scarcity claims,
and they do not have to move together. The gaps are the point.

Financial methodology and visualization tooling. Not investment advice.
Compute rates, residuals, token prices and debt data: CCIR (ccir.io).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from bipp import ccir, ccir_pages
from bipp.metrics import normalize_index
from bipp.pipeline import fetch_coinbase_btc_usd, fetch_live_dataset

BASKET_CHIPS = ["H100", "H200", "B200"]
DEFAULT_WEIGHTS = {"H100": 0.5, "H200": 0.3, "B200": 0.2}
JUMP_THRESHOLD = 0.10

SENSITIVITY_SURFACES = [
    ("Hyperscaler, on-demand, global", dict(operator_tier="T1", form_factor="ALL", interruptibility="ALL", commitment_term="OnDemand", region="ALL")),
    ("Hyperscaler, on-demand, US", dict(operator_tier="T1", form_factor="ALL", interruptibility="ALL", commitment_term="OnDemand", region="US")),
    ("Hyperscaler, 1-year committed", dict(operator_tier="T1", form_factor="ALL", interruptibility="ALL", commitment_term="1Y", region="ALL")),
    ("Hyperscaler, 3-year committed", dict(operator_tier="T1", form_factor="ALL", interruptibility="ALL", commitment_term="3Y", region="ALL")),
    ("Neocloud, on-demand, global", dict(operator_tier="T2", form_factor="ALL", interruptibility="ALL", commitment_term="OnDemand", region="ALL")),
    ("Neocloud, on-demand, US", dict(operator_tier="T2", form_factor="ALL", interruptibility="ALL", commitment_term="OnDemand", region="US")),
    ("Neocloud, guaranteed, global", dict(operator_tier="T2", form_factor="ALL", interruptibility="GTD", commitment_term="OnDemand", region="ALL")),
    ("Neocloud, 1-year committed", dict(operator_tier="T2", form_factor="ALL", interruptibility="ALL", commitment_term="1Y", region="ALL")),
    ("Marketplace, on-demand, global", dict(operator_tier="T3", form_factor="ALL", interruptibility="ALL", commitment_term="OnDemand", region="ALL")),
    ("Neocloud SXM, on-demand, global", dict(operator_tier="T2", form_factor="SXM", interruptibility="ALL", commitment_term="OnDemand", region="ALL")),
]


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def load_ccir_panel() -> pd.DataFrame:
    return ccir.load_panel(ccir.fetch_catalog(), ccir.fetch_history())


@st.cache_data(ttl=3600, show_spinner=False)
def load_btc(start: str, end: str) -> pd.DataFrame:
    return fetch_coinbase_btc_usd(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ornn() -> pd.DataFrame:
    return fetch_live_dataset()


@st.cache_data(ttl=3600, show_spinner=False)
def load_tokens() -> pd.DataFrame:
    return ccir_pages.fetch_tokens()


@st.cache_data(ttl=3600, show_spinner=False)
def load_hardware() -> pd.DataFrame:
    return ccir_pages.fetch_hardware()


@st.cache_data(ttl=3600, show_spinner=False)
def load_credit() -> pd.DataFrame:
    return ccir_pages.fetch_credit()


def daily_returns(values: pd.Series) -> pd.Series:
    return values.astype(float).pct_change().dropna()


# ---------------------------------------------------------------------------
# Tab 1: v1 against v2
# ---------------------------------------------------------------------------

def tab_comparison(panel: pd.DataFrame, btc: pd.DataFrame, surface: dict[str, str], weights: dict[str, float]) -> None:
    st.subheader("v1 against v2")
    st.caption(
        "Same formula, same BTC leg, two compute price sources. v1 uses Ornn's "
        "index_value, whose unit and construction are undocumented. v2 uses CCIR "
        "posted list ask in USD per GPU per hour."
    )

    try:
        v2 = ccir.attach_btc(ccir.build_basket(ccir.select_surface(panel, **surface), weights), btc)
    except ValueError as exc:
        st.error(f"v2 basket unavailable on this surface: {exc}")
        return

    try:
        ornn = load_ornn()
        ornn = ornn.rename(columns={"h100": "H100", "h200": "H200", "b200": "B200"})
        ornn["hardware_basket"] = sum(ornn[c] * weights[c] for c in weights)
        ornn["compute_per_btc"] = ornn["btc_usd"] / ornn["hardware_basket"]
        ornn["as_of_date"] = pd.to_datetime(ornn["date"], utc=True)
    except Exception as exc:  # noqa: BLE001 - live endpoint, any failure is informational
        st.warning(f"Ornn v1 leg unavailable: {exc}")
        ornn = None

    columns = st.columns(4)
    columns[0].metric("v2 basket, latest", f"${v2['hardware_basket'].iloc[-1]:.2f}/GPU-hr")
    columns[1].metric("v2 BIPP", f"{v2['bipp'].iloc[-1]:.1f}", delta=f"{v2['bipp'].iloc[-1] - 100:+.1f} vs base")
    columns[2].metric("BTC/USD", f"${v2['btc_usd'].iloc[-1]:,.0f}")
    columns[3].metric("CCIR history", f"{len(v2)} days")

    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
        subplot_titles=("Compute per BTC, rebased to 100 at first common date", "Hardware basket, USD per GPU-hour"),
    )
    figure.add_trace(
        go.Scatter(x=v2["as_of_date"], y=normalize_index(v2["compute_per_btc"].tolist()),
                   name="v2 (CCIR)", mode="lines", line=dict(width=2)),
        row=1, col=1,
    )
    figure.add_trace(
        go.Scatter(x=v2["as_of_date"], y=v2["hardware_basket"], name="v2 basket (USD/GPU-hr)", mode="lines"),
        row=2, col=1,
    )

    if ornn is not None:
        overlap = ornn[ornn["as_of_date"] >= v2["as_of_date"].min()]
        if not overlap.empty:
            figure.add_trace(
                go.Scatter(x=overlap["as_of_date"], y=normalize_index(overlap["compute_per_btc"].tolist()),
                           name="v1 (Ornn)", mode="lines", line=dict(width=2, dash="dash")),
                row=1, col=1,
            )
            figure.add_trace(
                go.Scatter(x=overlap["as_of_date"], y=overlap["hardware_basket"],
                           name="v1 basket (index_value)", mode="lines", line=dict(dash="dash")),
                row=2, col=1,
            )

    figure.update_layout(height=620, margin=dict(l=40, r=24, t=56, b=32), legend=dict(orientation="h"))
    st.plotly_chart(figure, use_container_width=True)

    if ornn is not None:
        merged = v2[["as_of_date", "compute_per_btc"]].merge(
            ornn[["as_of_date", "compute_per_btc"]], on="as_of_date", suffixes=("_v2", "_v1")
        )
        if len(merged) > 3:
            correlation = daily_returns(merged["compute_per_btc_v1"]).corr(daily_returns(merged["compute_per_btc_v2"]))
            left, right = st.columns(2)
            left.metric("Daily-change correlation, v1 vs v2", f"{correlation:+.2f}")
            right.metric("Overlapping days", f"{len(merged)}")
            if abs(correlation) < 0.3:
                st.warning(
                    f"Correlation of {correlation:+.2f} means these two sources do not move together. "
                    "Do not average them into one composite. Averaging uncorrelated series lowers "
                    "variance by cancellation, which would make BIPP look steadier than BTC for a "
                    "purely arithmetic reason and corrupt the exact hypothesis this tool tests. "
                    "Treat the gap as a data-quality signal instead."
                )


# ---------------------------------------------------------------------------
# Tab 2: rentals
# ---------------------------------------------------------------------------

def tab_rentals(panel: pd.DataFrame, btc: pd.DataFrame, surface: dict[str, str], weights: dict[str, float]) -> None:
    st.subheader("Rent: GPU-hours per BTC")

    selected = ccir.select_surface(panel, **surface)
    try:
        basket = ccir.build_basket(selected, weights)
        series = ccir.attach_btc(basket, btc)
    except ValueError as exc:
        st.error(f"{exc}. Widen the surface in the sidebar.")
        return

    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                           subplot_titles=("BIPP v2", "BTC/USD", "Component rates, USD per GPU-hour"))
    figure.add_trace(go.Scatter(x=series["as_of_date"], y=series["bipp"], name="BIPP v2", mode="lines"), row=1, col=1)
    figure.add_trace(go.Scatter(x=series["as_of_date"], y=series["btc_usd"], name="BTC/USD", mode="lines"), row=2, col=1)

    for chip in weights:
        try:
            component = ccir.chip_series(selected, chip)
        except ValueError:
            continue
        figure.add_trace(
            go.Scatter(x=component["as_of_date"], y=component["price_headline"], name=chip, mode="lines"),
            row=3, col=1,
        )
        jump = ccir.max_daily_jump(component["price_headline"])
        if jump >= JUMP_THRESHOLD:
            st.warning(
                f"{chip} ({component['series_id'].iloc[0]}) moves {jump * 100:.1f} percent in a single day "
                "inside this window. That size of step is a panel composition change, not a rate move. "
                "Treat any BIPP reading built on it as an artifact."
            )

    figure.update_yaxes(title_text="Index", row=1, col=1)
    figure.update_yaxes(title_text="USD", row=2, col=1)
    figure.update_yaxes(title_text="USD/GPU-hr", row=3, col=1)
    figure.update_layout(height=760, margin=dict(l=40, r=24, t=56, b=32), legend=dict(orientation="h"))
    st.plotly_chart(figure, use_container_width=True)

    bipp_volatility = daily_returns(series["compute_per_btc"]).std() * 100
    btc_volatility = daily_returns(series["btc_usd"]).std() * 100
    left, middle, right = st.columns(3)
    left.metric("BIPP daily volatility", f"{bipp_volatility:.2f}%")
    middle.metric("BTC daily volatility", f"{btc_volatility:.2f}%")
    right.metric("Ratio", f"{bipp_volatility / btc_volatility:.2f}x" if btc_volatility else "n/a")
    st.caption(
        "The July 16 hypothesis on Ledger row intelligence-credit-thesis is that compute purchasing "
        "power holds steadier than BTC/USD. A ratio above 1.0 is evidence against it: the hardware "
        "leg is adding noise rather than damping BTC."
    )

    st.markdown("#### Does the answer survive the basket choice?")
    rows = []
    for label, candidate in SENSITIVITY_SURFACES:
        try:
            candidate_series = ccir.attach_btc(
                ccir.build_basket(ccir.select_surface(panel, **candidate), weights), btc
            )
        except ValueError:
            continue
        components_clean = all(
            ccir.max_daily_jump(ccir.chip_series(ccir.select_surface(panel, **candidate), chip)["price_headline"]) < JUMP_THRESHOLD
            for chip in weights
        )
        rows.append({
            "Price surface": label,
            "Basket now (USD/GPU-hr)": round(candidate_series["hardware_basket"].iloc[-1], 3),
            "Basket change": f"{(candidate_series['hardware_basket'].iloc[-1] / candidate_series['hardware_basket'].iloc[0] - 1) * 100:+.1f}%",
            "BIPP": round(candidate_series["bipp"].iloc[-1], 1),
            "Clean": "yes" if components_clean else "PANEL JUMP",
        })

    if rows:
        table = pd.DataFrame(rows)
        st.dataframe(table, use_container_width=True, hide_index=True)
        clean_values = table.loc[table["Clean"] == "yes", "BIPP"]
        if len(clean_values) > 1:
            low, high = clean_values.min(), clean_values.max()
            st.metric("Spread across clean surfaces", f"{low:.1f} to {high:.1f}", delta=f"{high - low:.1f} index points")
            if low < 100 < high:
                st.error(
                    "The sign flips across defensible surfaces. BIPP does not have one value right now; "
                    "it has a range that straddles no-change. Any single headline number is a choice, not a reading."
                )

    st.markdown("#### Every citable series in BTC terms")
    st.caption("No basket weights involved, so the unmeasured 50/30/20 question does not enter these numbers.")
    citable = panel[panel["product_class"] == "citable"]
    table = ccir.btc_terms_table(citable, btc, JUMP_THRESHOLD)
    if table.empty:
        st.info("No citable series with enough overlapping history.")
        return
    if st.checkbox("Hide series with a panel jump or shadow status", value=True):
        table = table[table["clean"]]
    st.dataframe(
        table[["series_id", "gpu_model", "segment", "commitment_term", "interruptibility", "region",
               "usd_per_gpu_hour", "gpu_hours_per_btc", "btc_power_index", "n_sources", "confidence_level"]],
        use_container_width=True, hide_index=True,
    )
    st.download_button(
        "Download BTC-terms table (CSV)",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name="bipp_v2_btc_terms.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Tab 3: the four axes
# ---------------------------------------------------------------------------

def tab_axes(panel: pd.DataFrame, btc: pd.DataFrame, surface: dict[str, str]) -> None:
    st.subheader("Rent, own, borrow, produce")
    st.caption(
        "Four prices of the same underlying scarcity, all denominated in BTC. "
        "Rentals come from CCIR's CSV feed. Residuals, tokens and debt are scraped "
        "from CCIR pages, which have no published data contract and can break on a redesign."
    )

    btc_usd = float(btc.sort_values("date")["btc_usd"].iloc[-1])
    st.metric("BTC/USD used for every figure below", f"${btc_usd:,.0f}")

    selected = ccir.select_surface(panel, **surface)
    latest_rentals = {}
    for chip in sorted(selected["gpu_model"].unique()):
        try:
            latest_rentals[chip] = float(ccir.chip_series(selected, chip)["price_headline"].iloc[-1])
        except ValueError:
            continue

    own, rent, produce, borrow = st.tabs(["Own (residuals)", "Rent vs own", "Produce (tokens)", "Borrow (credit)"])

    with own:
        hardware = load_hardware()
        if hardware.empty:
            st.error("Hardware table did not parse. The page layout may have changed.")
        else:
            priced = ccir_pages.gpus_per_btc(hardware, btc_usd)
            st.dataframe(
                priced[["model", "age_years", "executed_median_usd", "posted_ask_usd",
                        "ask_over_executed", "pct_of_launch", "gpus_per_btc_executed", "gpus_per_btc_ask"]]
                .round(2),
                use_container_width=True, hide_index=True,
            )
            figure = go.Figure()
            figure.add_trace(go.Bar(x=priced["model"], y=priced["gpus_per_btc_executed"], name="At executed median"))
            figure.add_trace(go.Bar(x=priced["model"], y=priced["gpus_per_btc_ask"], name="At posted ask"))
            figure.update_layout(barmode="group", height=420, yaxis_type="log",
                                 yaxis_title="Cards per BTC (log)", legend=dict(orientation="h"),
                                 margin=dict(l=40, r=24, t=24, b=32))
            st.plotly_chart(figure, use_container_width=True)
            st.caption(
                "The gap between the two bars is the bid-ask on physical compute. It widens with age, "
                "which is what an illiquid secondary market looks like."
            )

    with rent:
        hardware = load_hardware()
        if hardware.empty or not latest_rentals:
            st.info("Needs both a hardware table and at least one rental series on the selected surface.")
        else:
            payback = ccir_pages.payback_hours(hardware, latest_rentals)
            if payback.empty:
                st.info("No chip names matched between the residual table and this rental surface.")
            else:
                st.dataframe(
                    payback[["model", "age_years", "executed_median_usd", "rental_usd_per_hour",
                             "payback_hours", "payback_months_full_util"]].round(2),
                    use_container_width=True, hide_index=True,
                )
                st.caption(
                    "Hours of rental revenue needed to recover the card's current secondary-market price, "
                    "at the rate on the selected surface, gross of power, hosting, financing and downtime. "
                    "This is the bridge between the flow price of compute and its stock price: a card whose "
                    "payback is lengthening is losing its claim on the rental stream faster than its resale "
                    "value is falling."
                )

    with produce:
        tokens = load_tokens()
        if tokens.empty:
            st.error("Token table did not parse. The page layout may have changed.")
        else:
            priced = ccir_pages.tokens_per_btc(tokens, btc_usd)
            basis = st.radio("Pricing basis", ["first_party_posted", "cross_provider_median"], horizontal=True)
            subset = priced[priced["pricing_basis"] == basis]
            st.caption(
                "CCIR keeps these unpooled on purpose: a posted first-party price and a cross-provider "
                "median for an open-weight model answer different questions."
            )
            st.dataframe(
                subset[["model", "input_usd_per_mtok", "output_usd_per_mtok",
                        "output_btok_per_btc", "input_btok_per_btc"]].round(3),
                use_container_width=True, hide_index=True,
            )
            if not subset.empty:
                left, right = st.columns(2)
                cheapest = subset.iloc[-1]
                priciest = subset.iloc[0]
                left.metric(f"Most output per BTC ({cheapest['model']})", f"{cheapest['output_btok_per_btc']:,.0f}B tokens")
                right.metric(f"Least output per BTC ({priciest['model']})", f"{priciest['output_btok_per_btc']:,.2f}B tokens")
                st.caption(
                    "This is the axis BIPP v1 could never see. A GPU-hour is an input; a token is the "
                    "product. If token prices fall faster than GPU-hour prices, the efficiency gain is "
                    "accruing to the model layer rather than the hardware layer, and a purchasing-power "
                    "index built only on hardware will miss it entirely."
                )

    with borrow:
        credit = load_credit()
        if credit.empty:
            st.error("Credit table did not parse. The page layout may have changed.")
        else:
            secured_only = st.checkbox("Secured instruments only", value=True)
            subset = credit[credit["seniority"] == "secured"] if secured_only else credit
            summary = ccir_pages.credit_summary(subset, btc_usd)
            columns = st.columns(4)
            columns[0].metric("Instruments", f"{summary['instruments']}")
            columns[1].metric("Notional", f"${summary['notional_usd'] / 1e9:,.1f}B")
            columns[2].metric("In BTC", f"{summary['btc_equivalent']:,.0f} BTC")
            columns[3].metric("Of 21M terminal supply", f"{summary['pct_of_terminal_supply']:.1f}%")
            st.caption(
                "Notional is a face amount, not a market value, and this tracker mixes GPU-secured debt "
                "with campus-secured and landlord SPV structures. Toggle secured-only to narrow it. "
                "The BTC figure is a scale comparison, not a claim that these are substitutes."
            )
            in_btc = ccir_pages.credit_in_btc(subset, btc_usd)
            st.dataframe(
                in_btc[["issuer", "instrument", "type", "size_musd", "size_btc", "rate", "maturity", "status"]]
                .round({"size_musd": 0, "size_btc": 0}),
                use_container_width=True, hide_index=True,
            )
            by_type = in_btc.groupby("type")["size_btc"].sum().sort_values(ascending=False).reset_index()
            figure = go.Figure(go.Bar(x=by_type["type"], y=by_type["size_btc"]))
            figure.update_layout(height=360, yaxis_title="BTC equivalent",
                                 margin=dict(l=40, r=24, t=24, b=32))
            st.plotly_chart(figure, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab 4: data audit
# ---------------------------------------------------------------------------

def tab_audit(panel: pd.DataFrame) -> None:
    st.subheader("Data audit")
    quality = ccir.series_quality(panel)
    columns = st.columns(4)
    columns[0].metric("Series joined", f"{len(quality)}")
    columns[1].metric("Citable", f"{int((quality['product_class'] == 'citable').sum())}")
    columns[2].metric("Full history", f"{int((quality['days'] == quality['days'].max()).sum())}")
    columns[3].metric("With a >10% daily jump", f"{int((quality['max_daily_jump'] >= JUMP_THRESHOLD).sum())}")

    st.dataframe(
        quality.sort_values("max_daily_jump", ascending=False)[
            ["series_id", "gpu_model", "segment", "commitment_term", "region", "product_class",
             "confidence_level", "n_sources", "days", "max_daily_jump", "ever_shadow", "price_latest"]
        ].round(4),
        use_container_width=True, hide_index=True,
    )
    st.markdown(
        f"""
**Sources and limits**

- Rental rates: `{ccir.DAILY_URL}` and `{ccir.HISTORY_URL}`. USD per GPU per hour,
  posted list ask, operator-equal aggregation, published by 07:30 ET.
- The history file is not linked anywhere on ccir.io. It resolves and robots.txt
  permits it, but it is an undocumented endpoint and can disappear without notice.
- History is short: CCIR carries roughly 30 days. Ornn carries about 92. Neither
  spans a full cycle, so base-date choice dominates any reading.
- Residuals, tokens and credit are scraped from page HTML, not a data feed.
- Attribution: {ccir.ATTRIBUTION}, with series identifier and publication date.
- Terms permit non-commercial quotation and citation with attribution. Systematic
  or bulk retrieval, redistribution of series history, and derived commercial
  products need written permission at research@ccir.io. Running this locally for
  personal research sits inside the permitted use; publishing the derived series
  is where that changes.
"""
    )


# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="BIPP v2", layout="wide")
    st.title("BIPP v2: Bitcoin against the compute capital stack")
    st.caption(
        "Financial methodology and visualization tooling, not investment advice. "
        "Measures purchasing power over compute infrastructure, its resale value, its "
        "financing, and its output. Compute data: CCIR (ccir.io)."
    )

    with st.spinner("Loading CCIR rate panel..."):
        try:
            panel = load_ccir_panel()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load CCIR rates: {exc}")
            st.stop()

    first = panel["as_of_date"].min().date()
    last = panel["as_of_date"].max().date()
    btc = load_btc((first - timedelta(days=2)).isoformat(), (last + timedelta(days=1)).isoformat())

    st.sidebar.header("Price surface")
    st.sidebar.caption("Which compute market the basket is priced against.")
    options = ccir.surface_options(panel)

    def pick(label: str, dimension: str, preferred: str) -> str:
        values = options[dimension]
        index = values.index(preferred) if preferred in values else 0
        return st.sidebar.selectbox(label, values, index=index)

    surface = {
        "operator_tier": pick("Operator tier", "operator_tier", "T2"),
        "form_factor": pick("Form factor", "form_factor", "ALL"),
        "interruptibility": pick("Interruptibility", "interruptibility", "ALL"),
        "commitment_term": pick("Commitment term", "commitment_term", "OnDemand"),
        "region": pick("Region", "region", "ALL"),
    }
    st.sidebar.caption(f"Tier: {ccir.TIER_LABELS.get(surface['operator_tier'], surface['operator_tier'])}")

    st.sidebar.header("Basket")
    weights = {}
    for chip, default in DEFAULT_WEIGHTS.items():
        weights[chip] = st.sidebar.number_input(chip, min_value=0.0, max_value=1.0, value=default, step=0.05)
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        st.sidebar.error(f"Weights sum to {total:.2f}, not 1.00.")
        st.stop()

    comparison, rentals, axes, audit = st.tabs(
        ["v1 vs v2", "Rent (GPU-hours per BTC)", "Four axes in BTC", "Data audit"]
    )
    with comparison:
        tab_comparison(panel, btc, surface, weights)
    with rentals:
        tab_rentals(panel, btc, surface, weights)
    with axes:
        tab_axes(panel, btc, surface)
    with audit:
        tab_audit(panel)


if __name__ == "__main__":
    main()
