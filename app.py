from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from bipp.pipeline import build_metrics, fetch_live_dataset, filter_date_range, load_fixture_dataset, trailing_window


ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = ROOT / "data" / "fixtures" / "synthetic_bipp.csv"
TIMEFRAME_DAYS = {
    "7D": 7,
    "30D": 30,
    "60D": 60,
    "90D": 90,
}


@st.cache_data(ttl=900, show_spinner=False)
def cached_live_dataset() -> pd.DataFrame:
    return fetch_live_dataset()


def draw_chart(df: pd.DataFrame, primary: str) -> go.Figure:
    top_name = "BIPP index" if primary == "bipp" else "Composite GPU-hours per BTC"
    top_col = "bipp" if primary == "bipp" else "compute_per_btc"

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(top_name, "BTC/USD", "Hardware basket"),
    )
    fig.add_trace(go.Scatter(x=df["date"], y=df[top_col], name=top_name, mode="lines"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["btc_usd"], name="BTC/USD", mode="lines"), row=2, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["hardware_basket"], name="Hardware basket", mode="lines"), row=3, col=1)
    fig.update_layout(height=720, margin=dict(l=36, r=24, t=64, b=32), legend=dict(orientation="h"))
    fig.update_yaxes(title_text=top_name, row=1, col=1)
    fig.update_yaxes(title_text="USD", row=2, col=1)
    fig.update_yaxes(title_text="Index value", row=3, col=1)
    return fig


def select_timeframe(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    st.sidebar.subheader("Time frame")
    timeframe = st.sidebar.radio("Window", ["30D", "7D", "60D", "90D", "All available", "Custom"], index=0)

    min_date = raw_df["date"].dt.date.min()
    max_date = raw_df["date"].dt.date.max()

    if timeframe == "Custom":
        selected = st.sidebar.date_input(
            "Custom dates",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        if not isinstance(selected, tuple) or len(selected) != 2:
            st.warning("Select a start and end date.")
            st.stop()
        window_df = filter_date_range(raw_df, selected[0], selected[1])
    elif timeframe == "All available":
        window_df = raw_df
    else:
        window_df = trailing_window(raw_df, TIMEFRAME_DAYS[timeframe])

    start_date = window_df["date"].dt.date.min()
    end_date = window_df["date"].dt.date.max()
    st.sidebar.caption(f"{start_date} to {end_date}")
    return window_df, timeframe


def percent_change(df: pd.DataFrame, column: str) -> str:
    first = df.iloc[0][column]
    latest = df.iloc[-1][column]
    return f"{((latest / first) - 1) * 100:+.1f}% window"


def main() -> None:
    st.set_page_config(page_title="BIPP", layout="wide")
    st.title("Bitcoin Compute Infrastructure Purchasing Power")

    source = st.sidebar.radio("Data source", ["Live Ornn + Coinbase", "Synthetic fixture"], index=0)

    st.sidebar.subheader("Basket")
    h100_weight = st.sidebar.number_input("H100 SXM", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
    h200_weight = st.sidebar.number_input("H200", min_value=0.0, max_value=1.0, value=0.3, step=0.05)
    b200_weight = st.sidebar.number_input("B200", min_value=0.0, max_value=1.0, value=0.2, step=0.05)
    weights = {"h100": h100_weight, "h200": h200_weight, "b200": b200_weight}

    primary = st.sidebar.radio("Primary chart", ["bipp", "compute_per_btc"], format_func=lambda x: "BIPP index" if x == "bipp" else "Compute per BTC")

    if source == "Live Ornn + Coinbase":
        try:
            with st.spinner("Fetching live Ornn and Coinbase data..."):
                raw_df = cached_live_dataset()
            source_label = "Live Ornn + Coinbase"
        except Exception as exc:
            raw_df = load_fixture_dataset(FIXTURE_PATH)
            source_label = "Synthetic fixture fallback"
            st.warning(f"Live fetch failed: {exc}. Showing synthetic fixture data.")
    else:
        raw_df = load_fixture_dataset(FIXTURE_PATH)
        source_label = "Synthetic fixture"

    window_df, timeframe = select_timeframe(raw_df)

    if abs(sum(weights.values()) - 1.0) > 0.000001:
        st.error("Basket weights must sum to 1.0.")
        st.stop()

    base_options = window_df["date"].dt.date.astype(str).tolist()
    base_date = st.sidebar.selectbox("BIPP base date", base_options, index=0)
    df = build_metrics(window_df, weights, base_date=base_date)

    latest = df.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BIPP", f"{latest['bipp']:.2f}", delta=f"{latest['bipp'] - 100:+.2f} from base")
    c2.metric("Compute per BTC", f"{latest['compute_per_btc']:.2f}", delta=percent_change(df, "compute_per_btc"))
    c3.metric("BTC/USD", f"${latest['btc_usd']:,.2f}", delta=percent_change(df, "btc_usd"))
    c4.metric("Hardware basket", f"{latest['hardware_basket']:.3f}", delta=percent_change(df, "hardware_basket"))

    start_date = df["date"].dt.date.min()
    end_date = df["date"].dt.date.max()
    st.caption(
        f"{source_label}. Window: {timeframe}, {start_date} to {end_date}. "
        "Live fetches use Streamlit's memory cache; raw API responses are not written to disk."
    )

    st.plotly_chart(draw_chart(df, primary), use_container_width=True)

    with st.expander("Data audit", expanded=False):
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download processed CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="bipp_processed.csv",
            mime="text/csv",
        )

    with st.expander("Method", expanded=False):
        st.markdown(
            """
`hardware_basket = 0.5 * H100_SXM + 0.3 * H200 + 0.2 * B200` by default.

`compute_per_btc = BTCUSD / hardware_basket`

`BIPP = 100 * compute_per_btc / compute_per_btc_base_date`

BIPP measures AI compute infrastructure purchasing power. It does not measure direct intelligence output, model quality, or investment return.
            """.strip()
        )


if __name__ == "__main__":
    main()
