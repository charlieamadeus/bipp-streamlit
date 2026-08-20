"""BTC and the cost of compute.

Four panels. The first is the chart this project has always been about: how many
GPU-hours one Bitcoin buys. The other three are the context that says whether
that number means anything.

Financial methodology and visualization tooling, not investment advice.
Compute rates, residuals and debt data: CCIR (ccir.io).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from bipp import ccir, ccir_pages, economics
from bipp.metrics import normalize_index
from bipp.pipeline import fetch_coinbase_btc_usd, fetch_live_dataset

JUMP_THRESHOLD = 0.10
DEFAULT_WEIGHTS = {"H100": 0.5, "H200": 0.3, "B200": 0.2}


@st.cache_data(ttl=3600, show_spinner=False)
def load_catalog() -> pd.DataFrame:
    return ccir.fetch_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def load_panel() -> pd.DataFrame:
    return ccir.load_panel(load_catalog(), ccir.fetch_history())


@st.cache_data(ttl=3600, show_spinner=False)
def load_btc(start: str, end: str) -> pd.DataFrame:
    return fetch_coinbase_btc_usd(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ornn() -> pd.DataFrame:
    return fetch_live_dataset()


@st.cache_data(ttl=3600, show_spinner=False)
def load_hardware() -> pd.DataFrame:
    return ccir_pages.fetch_hardware()


@st.cache_data(ttl=3600, show_spinner=False)
def load_credit() -> pd.DataFrame:
    return ccir_pages.fetch_credit()


# ---------------------------------------------------------------------------
# Panel 1: GPU-hours per BTC
# ---------------------------------------------------------------------------

def panel_purchasing_power(panel: pd.DataFrame, btc: pd.DataFrame,
                           surface: dict[str, str], weights: dict[str, float]) -> None:
    st.subheader("How much compute does one Bitcoin buy")

    selected = ccir.select_surface(panel, **surface)
    try:
        series = ccir.attach_btc(ccir.build_basket(selected, weights), btc)
    except ValueError as exc:
        st.error(f"{exc}. Widen the surface in the sidebar.")
        return

    quarantined = []
    for chip in weights:
        try:
            component = ccir.chip_series(selected, chip)
        except ValueError:
            continue
        jump = ccir.max_daily_jump(component["price_headline"])
        if jump >= JUMP_THRESHOLD:
            quarantined.append((chip, component["series_id"].iloc[0], jump))

    columns = st.columns(4)
    columns[0].metric("GPU-hours per BTC", f"{series['compute_per_btc'].iloc[-1]:,.0f}")
    columns[1].metric("Index", f"{series['bipp'].iloc[-1]:.1f}",
                      delta=f"{series['bipp'].iloc[-1] - 100:+.1f} vs window start")
    columns[2].metric("Basket", f"${series['hardware_basket'].iloc[-1]:.2f}/GPU-hr")
    columns[3].metric("BTC/USD", f"${series['btc_usd'].iloc[-1]:,.0f}")

    if quarantined:
        for chip, series_id, jump in quarantined:
            st.warning(
                f"{chip} ({series_id}) moves {jump * 100:.0f}% in one day inside this window. "
                "That is a panel composition change, not a rate move. This reading is an artifact."
            )

    figure = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        subplot_titles=("GPU-hours per BTC", "BTC/USD", "Component rates with dispersion, USD/GPU-hour"),
    )
    figure.add_trace(go.Scatter(x=series["as_of_date"], y=series["compute_per_btc"],
                                name="GPU-hours per BTC", mode="lines", line=dict(width=2)), row=1, col=1)
    figure.add_trace(go.Scatter(x=series["as_of_date"], y=series["btc_usd"],
                                name="BTC/USD", mode="lines"), row=2, col=1)

    for chip in weights:
        try:
            component = ccir.chip_series(selected, chip)
        except ValueError:
            continue
        figure.add_trace(go.Scatter(x=component["as_of_date"], y=component["price_p75"],
                                    line=dict(width=0), showlegend=False, hoverinfo="skip"), row=3, col=1)
        figure.add_trace(go.Scatter(x=component["as_of_date"], y=component["price_p25"],
                                    fill="tonexty", line=dict(width=0), name=f"{chip} IQR",
                                    opacity=0.2, hoverinfo="skip"), row=3, col=1)
        figure.add_trace(go.Scatter(x=component["as_of_date"], y=component["price_headline"],
                                    name=chip, mode="lines"), row=3, col=1)

    figure.update_yaxes(title_text="GPU-hours", row=1, col=1)
    figure.update_yaxes(title_text="USD", row=2, col=1)
    figure.update_yaxes(title_text="USD/GPU-hr", row=3, col=1)
    figure.update_layout(height=780, margin=dict(l=40, r=24, t=56, b=32), legend=dict(orientation="h"))
    st.plotly_chart(figure, use_container_width=True)

    st.markdown("#### The longer history, and the June 30 divergence")
    st.caption(
        "CCIR history starts 2026-07-21, three weeks after BTC's low, so it cannot see the "
        "divergence this project was built to watch. Ornn reaches back to 2026-05-19."
    )
    try:
        ornn = load_ornn().rename(columns={"h100": "H100", "h200": "H200", "b200": "B200"})
    except Exception as exc:  # noqa: BLE001
        st.info(f"Ornn leg unavailable: {exc}")
        return

    ornn["hardware_basket"] = sum(ornn[chip] * weights[chip] for chip in weights if chip in ornn)
    ornn["compute_per_btc"] = ornn["btc_usd"] / ornn["hardware_basket"]
    ornn["as_of_date"] = pd.to_datetime(ornn["date"], utc=True)

    btc_trough = ornn.loc[ornn["btc_usd"].idxmin()]
    power_trough = ornn.loc[ornn["compute_per_btc"].idxmin()]

    left, middle, right = st.columns(3)
    left.metric("BTC trough", f"${btc_trough['btc_usd']:,.0f}",
                delta=btc_trough["as_of_date"].strftime("%Y-%m-%d"), delta_color="off")
    middle.metric("Compute-per-BTC trough", f"{power_trough['compute_per_btc']:,.0f}",
                  delta=power_trough["as_of_date"].strftime("%Y-%m-%d"), delta_color="off")
    gap = 100 * (btc_trough["compute_per_btc"] / power_trough["compute_per_btc"] - 1)
    right.metric("Above its own low at the BTC low", f"{gap:+.1f}%")

    divergence = make_subplots(rows=1, cols=1)
    divergence.add_trace(go.Scatter(x=ornn["as_of_date"], y=normalize_index(ornn["compute_per_btc"].tolist()),
                                    name="GPU-hours per BTC (Ornn)", mode="lines"))
    divergence.add_trace(go.Scatter(x=ornn["as_of_date"], y=normalize_index(ornn["btc_usd"].tolist()),
                                    name="BTC/USD", mode="lines", line=dict(dash="dash")))
    divergence.add_vline(x=btc_trough["as_of_date"].timestamp() * 1000, line_dash="dot",
                         annotation_text="BTC low")
    divergence.update_layout(height=380, yaxis_title="Rebased to 100",
                             margin=dict(l=40, r=24, t=24, b=32), legend=dict(orientation="h"))
    st.plotly_chart(divergence, use_container_width=True)
    st.caption(
        f"At the BTC low, compute purchasing power sat {gap:+.1f}% above its own eventual low: "
        f"the non-confirmation this project was built to watch. It made its actual low on "
        f"{power_trough['as_of_date'].strftime('%Y-%m-%d')}, after BTC's, so the divergence has "
        "not resolved upward so far."
    )


# ---------------------------------------------------------------------------
# Panel 2: commitment ratio and term structure
# ---------------------------------------------------------------------------

def panel_term_structure(catalog: pd.DataFrame, surface: dict[str, str]) -> None:
    st.subheader("What buyers pay to lock capacity")
    st.caption(
        "The cheapest honest monthly read here. It needs no capex estimate, no opex assumption, "
        "and no residual sample. A falling ratio means operators are paying more to secure "
        "occupancy and the on-demand list is drifting away from what actually clears."
    )

    chips = sorted(catalog[catalog["commitment_term"] == "1Y"]["gpu_model"].unique())
    chip = st.selectbox("Chip", chips, index=chips.index("H100") if "H100" in chips else 0)

    ratio = economics.commitment_ratio(
        catalog, chip, tier=surface["operator_tier"],
        form_factor=surface["form_factor"], region=surface["region"],
    )
    if ratio is None:
        st.info(f"No committed and on-demand pair for {chip} on this surface.")
    else:
        columns = st.columns(4)
        columns[0].metric("On-demand", f"${ratio['on_demand']:.2f}/hr")
        columns[1].metric("1-year committed", f"${ratio['committed']:.2f}/hr")
        columns[2].metric("Committed / on-demand", f"{ratio['ratio']:.2f}")
        columns[3].metric("Discount to lock", f"{ratio['discount_pct']:.0f}%")

    curve = economics.term_structure(
        catalog, chip, tier=surface["operator_tier"],
        form_factor=surface["form_factor"], region=surface["region"],
    )
    if curve.empty:
        st.info("No term curve on this surface.")
        return
    figure = go.Figure(go.Scatter(x=curve["term"], y=curve["usd_per_gpu_hour"],
                                  mode="lines+markers", name=chip))
    figure.update_layout(height=360, yaxis_title="USD per GPU-hour", xaxis_title="Commitment term",
                         margin=dict(l=40, r=24, t=24, b=32))
    st.plotly_chart(figure, use_container_width=True)
    st.dataframe(curve, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Panel 3: carry
# ---------------------------------------------------------------------------

def panel_carry(catalog: pd.DataFrame, surface: dict[str, str]) -> None:
    st.subheader("Does the asset earn more than its financing")
    st.warning(
        "The depreciation basis flips the sign of this answer, so it is a control rather than "
        "a number I picked. Used-price decay is what a card fetches on a marketplace. Covenant "
        "amortization is what the loan actually takes, and CCIR reports that every disclosed "
        "GPU-backed schedule amortizes collateral toward zero on a three-to-four year cash clock. "
        "On an H100 SXM5 at the 3-year committed rate those two give +3pp and -9pp."
    )

    controls = st.columns(3)
    basis = controls[0].radio("Depreciation basis", ["covenant", "used_curve"],
                              format_func=lambda x: "Covenant amortization" if x == "covenant" else "Used-price curve")
    amortization_years = controls[0].slider("Covenant amortization (years)", 2.0, 5.0, 3.5, 0.5,
                                            disabled=basis != "covenant")
    utilization = controls[1].slider("Billed utilization", 0.40, 1.00, 0.90, 0.05)
    opex_share = controls[1].slider("Opex share of revenue", 0.10, 0.70, 0.45, 0.05)
    capex_multiple = controls[2].slider("Deployed capex multiple", 1.0, 2.5, 1.0, 0.1,
                                        help="1.0 prices the bare card. Node, network, and power "
                                             "typically take deployed cost to 1.4-2.0x.")
    cost_of_debt = controls[2].slider("Cost of debt (%)", 4.0, 14.0, 6.88, 0.25)
    term = controls[2].selectbox("Rate to underwrite on", ["3Y", "1Y", "OnDemand"], index=0)

    assumptions = economics.CarryAssumptions(
        utilization=utilization, opex_share=opex_share, cost_of_debt_pct=cost_of_debt,
        capex_multiple=capex_multiple, depreciation_basis=basis,
    )

    hardware = load_hardware()
    if hardware.empty:
        st.error("Hardware table did not parse.")
        return

    rows = []
    for _, card in hardware.dropna(subset=["pct_of_launch"]).iterrows():
        chip = ccir_pages.normalize_chip(card["model"])
        match = catalog[
            (catalog["gpu_model"] == chip)
            & (catalog["operator_tier"] == surface["operator_tier"])
            & (catalog["form_factor"] == surface["form_factor"])
            & (catalog["interruptibility"] == "ALL")
            & (catalog["commitment_term"] == term)
            & (catalog["region"] == surface["region"])
        ]
        if match.empty:
            continue
        rent = float(match.iloc[0]["price_headline"])
        capex = economics.launch_price(card["executed_median_usd"], card["pct_of_launch"])
        depreciation = (economics.covenant_depreciation(amortization_years) if basis == "covenant"
                        else economics.used_curve_depreciation(card["pct_of_launch"], card["age_years"]))
        result = economics.carry(rent, capex, depreciation, assumptions)
        breakeven = economics.breakeven_rent(capex, depreciation, assumptions)
        rows.append({
            "Card": card["model"],
            "Rent": round(rent, 2),
            "Launch basis": round(capex),
            "Deployed": round(result["deployed_capex_usd"]),
            "Depreciation": f"{result['depreciation_pct']:.0f}%",
            "Net yield": f"{result['net_yield_pct']:.0f}%",
            "Carry": round(result["carry_pp"], 1),
            "Breakeven rent": round(breakeven, 2),
            "Headroom": f"{100 * (rent / breakeven - 1):+.0f}%",
            "Used n": int(card["executed_n"]) if pd.notna(card["executed_n"]) else 0,
        })

    if not rows:
        st.info(f"No {term} rates on this surface match the residual table.")
        return

    table = pd.DataFrame(rows).sort_values("Carry")
    st.dataframe(table, use_container_width=True, hide_index=True)

    negative = table[table["Carry"] < 0]
    if not negative.empty:
        st.error(
            f"{len(negative)} of {len(table)} cards do not cover their financing under these "
            f"assumptions: {', '.join(negative['Card'].tolist())}."
        )

    figure = go.Figure(go.Bar(x=table["Card"], y=table["Carry"],
                              marker_color=["#c0392b" if v < 0 else "#27ae60" for v in table["Carry"]]))
    figure.add_hline(y=0, line_dash="dot")
    figure.update_layout(height=360, yaxis_title="Carry over cost of debt (pp)",
                         margin=dict(l=40, r=24, t=24, b=32))
    st.plotly_chart(figure, use_container_width=True)
    st.caption(
        "Residual sample sizes are in the last column. Several are under ten sold units in 90 days, "
        "so the launch basis and any used-curve depreciation derived from them are thin. The launch "
        "basis is reconstructed from CCIR's percent-of-launch mark, not a delivered price."
    )


# ---------------------------------------------------------------------------
# Panel 4: credit
# ---------------------------------------------------------------------------

def panel_credit() -> None:
    st.subheader("What it costs to borrow against compute")
    credit = load_credit()
    if credit.empty:
        st.error("Credit table did not parse.")
        return

    vintages = economics.credit_spreads_by_vintage(credit)
    if vintages.empty:
        st.info("No SOFR-spread instruments parsed.")
        return

    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(go.Bar(x=vintages["vintage"], y=vintages["notional_musd"] / 1000,
                            name="Notional ($B)", opacity=0.5), secondary_y=True)
    figure.add_trace(go.Scatter(x=vintages["vintage"], y=vintages["median_spread_pct"],
                                name="Median SOFR spread (%)", mode="lines+markers",
                                line=dict(width=3)), secondary_y=False)
    figure.update_yaxes(title_text="Median SOFR spread (%)", secondary_y=False)
    figure.update_yaxes(title_text="Notional ($B)", secondary_y=True)
    figure.update_layout(height=400, margin=dict(l=40, r=24, t=24, b=32), legend=dict(orientation="h"))
    st.plotly_chart(figure, use_container_width=True)
    st.dataframe(vintages, use_container_width=True, hide_index=True)
    st.caption(
        "Spread compression here is not necessarily a reprice of GPU collateral. CCIR attributes "
        "much of it to credit enhancement, meaning investment-grade take-or-pay offtake behind the "
        "facility. An operator with no named offtaker does not borrow at the median. Fixed-coupon "
        "instruments are excluded rather than pooled with spreads."
    )


# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="BTC and the cost of compute", layout="wide")
    st.title("BTC and the cost of compute")
    st.caption(
        "Financial methodology and visualization tooling, not investment advice. "
        "Compute rates, residuals and debt: CCIR (ccir.io). BTC/USD: Coinbase."
    )

    with st.spinner("Loading CCIR..."):
        try:
            catalog, panel = load_catalog(), load_panel()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load CCIR data: {exc}")
            st.stop()

    first, last = panel["as_of_date"].min().date(), panel["as_of_date"].max().date()
    btc = load_btc((first - timedelta(days=2)).isoformat(), (last + timedelta(days=1)).isoformat())

    st.sidebar.header("Price surface")
    options = ccir.surface_options(panel)

    def pick(label: str, dimension: str, preferred: str) -> str:
        values = options[dimension]
        return st.sidebar.selectbox(label, values,
                                    index=values.index(preferred) if preferred in values else 0)

    surface = {
        "operator_tier": pick("Operator tier", "operator_tier", "T2"),
        "form_factor": pick("Form factor", "form_factor", "SXM"),
        "interruptibility": pick("Interruptibility", "interruptibility", "ALL"),
        "commitment_term": pick("Commitment term", "commitment_term", "OnDemand"),
        "region": pick("Region", "region", "ALL"),
    }
    st.sidebar.caption(f"Tier: {ccir.TIER_LABELS.get(surface['operator_tier'], surface['operator_tier'])}")

    st.sidebar.header("Basket")
    weights = {chip: st.sidebar.number_input(chip, 0.0, 1.0, value=default, step=0.05)
               for chip, default in DEFAULT_WEIGHTS.items()}
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        st.sidebar.error(f"Weights sum to {sum(weights.values()):.2f}, not 1.00.")
        st.stop()

    power, terms, carry_tab, credit_tab = st.tabs(
        ["Compute per BTC", "Cost of locking capacity", "Carry vs financing", "Credit"]
    )
    with power:
        panel_purchasing_power(panel, btc, surface, weights)
    with terms:
        panel_term_structure(catalog, surface)
    with carry_tab:
        panel_carry(catalog, surface)
    with credit_tab:
        panel_credit()


if __name__ == "__main__":
    main()
