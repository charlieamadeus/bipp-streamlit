"""What one Bitcoin buys.

Everything on this page is denominated in Bitcoin. No dollars, no percent
yields, no assumption dials. Two questions, in order:

  Is one Bitcoin buying more or less computing power than it used to?
  And is that because money moved, or because computing power did?

Financial methodology and visualization tooling, not investment advice.
Compute rates, residuals, token prices and debt: CCIR (ccir.io).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bipp import btc as btc_history
from bipp import ccir, ccir_pages, store, theme
from bipp.pipeline import fetch_coinbase_btc_usd, fetch_live_dataset
from bipp.theme import MONEY, PLOT_CONFIG, POWER, fact

BASKET = {"H100": 0.5, "H200": 0.3, "B200": 0.2}
SURFACE = dict(operator_tier="T2", form_factor="SXM",
               interruptibility="ALL", commitment_term="OnDemand", region="ALL")


# --------------------------------------------------------------------- data

@st.cache_data(ttl=3600, show_spinner=False)
def load_rates() -> pd.DataFrame:
    """Live CCIR merged over our own store, stored rows winning."""
    live = ccir.load_panel(ccir.fetch_catalog(), ccir.fetch_history())
    try:
        return store.merge_rates(live)
    except Exception:  # noqa: BLE001 - a missing store is not fatal
        return live


@st.cache_data(ttl=3600, show_spinner=False)
def load_ornn() -> pd.DataFrame | None:
    """Ornn's marketplace index as compute-per-BTC, back to 2026-05-19."""
    try:
        frame = fetch_live_dataset().rename(
            columns={"h100": "H100", "h200": "H200", "b200": "B200"})
    except Exception:  # noqa: BLE001
        return None
    frame["hardware_basket"] = sum(frame[c] * w for c, w in BASKET.items())
    frame["compute_per_btc"] = frame["btc_usd"] / frame["hardware_basket"]
    frame["as_of_date"] = pd.to_datetime(frame["date"], utc=True)
    return frame.sort_values("as_of_date").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_btc_window(start: str, end: str) -> pd.DataFrame:
    return fetch_coinbase_btc_usd(start, end)


@st.cache_data(ttl=3600, show_spinner=False)
def load_btc_long() -> pd.Series:
    return btc_history.fetch_daily("2023-01-01", str(pd.Timestamp.utcnow().date()))


@st.cache_data(ttl=3600, show_spinner=False)
def load_hardware() -> pd.DataFrame:
    return ccir_pages.fetch_hardware()


@st.cache_data(ttl=3600, show_spinner=False)
def load_tokens() -> pd.DataFrame:
    return ccir_pages.fetch_tokens()


@st.cache_data(ttl=3600, show_spinner=False)
def load_credit() -> pd.DataFrame:
    return ccir_pages.fetch_credit()


def remember(key: str, options: list[str], default: int = 0) -> str:
    """Read a selection before its widget exists.

    Streamlit renders in source order, so declaring a widget above its number
    would put the dropdown where the number belongs. Seeding session_state lets
    the value be known first and the control be drawn underneath it.
    """
    if key not in st.session_state or st.session_state[key] not in options:
        st.session_state[key] = options[min(default, len(options) - 1)]
    return st.session_state[key]


def control(key: str, options: list[str], help_text: str) -> None:
    st.selectbox(key, options, key=key, label_visibility="collapsed", help=help_text)


# -------------------------------------------------------------------- cards

def card_rent(panel, series, latest_btc) -> None:
    # Every chip that prices anywhere on the ladder, not just the default
    # surface: that is what puts a 3090 and a 4090 beside a B300.
    priceable = ccir.priceable_chips(panel)
    options = ["The 50/30/20 basket"] + [chip for chip, _, _ in priceable]
    choice = remember("rent", options)
    if choice == "The 50/30/20 basket":
        hours, foot = series["compute_per_btc"].iloc[-1], "three chips, blended"
    else:
        rate, market = next((r, m) for c, r, m in priceable if c == choice)
        hours, foot = latest_btc / rate, f"${rate:.2f} an hour, {market.lower()}"
    st.markdown(fact(f"{hours:,.0f}", "GPU-hours per Bitcoin", foot), unsafe_allow_html=True)
    control("rent", options,
            "Hours of one chip that a Bitcoin rents. Datacentre parts price on the "
            "neocloud SXM market; consumer cards like the 3090 and 4090 are only "
            "listed by marketplaces, so those are used where nothing better exists.")


def card_own(hardware, latest_btc) -> None:
    if hardware.empty:
        st.markdown(fact("n/a", "Cards per Bitcoin", "table unavailable"), unsafe_allow_html=True)
        return
    by_age = hardware.sort_values("age_years")
    models = by_age["model"].tolist()
    default = next((i for i, m in enumerate(models) if m.startswith("H100 80GB SXM")), 0)
    choice = remember("own", models, default)
    row = by_age[by_age["model"] == choice].iloc[0]
    st.markdown(
        fact(f"{latest_btc / float(row['executed_median_usd']):,.1f}", "Cards per Bitcoin",
             f"${row['executed_median_usd']:,.0f} used, {row['age_years']:.1f} years old"),
        unsafe_allow_html=True,
    )
    control("own", models,
            "Whole chips a Bitcoin buys outright, at prices actually transacted on the "
            "secondary market. Datacentre parts only: nobody publishes a "
            "secondary-market median for consumer cards, so no 3090 or 4090 here.")


def card_produce(tokens, latest_btc) -> None:
    if tokens.empty:
        st.markdown(fact("n/a", "Tokens per Bitcoin", "table unavailable"), unsafe_allow_html=True)
        return
    flagships = ccir_pages.frontier_models(tokens)
    if flagships.empty:
        st.markdown(fact("n/a", "Tokens per Bitcoin", "no flagship priced"), unsafe_allow_html=True)
        return
    # Curated order, strongest lab first, so the head of the list is the
    # standard rather than whatever happens to be priciest.
    # The lab goes in the footer, not the label: the card is too narrow to show
    # "Claude Fable 5 - Anthropic" without truncating it mid-word.
    labels = [str(r.model) for r in flagships.itertuples()]
    choice = remember("produce", labels)
    row = flagships.iloc[labels.index(choice)]
    price = float(row["output_usd_per_mtok"])
    billions = latest_btc / price / 1000
    shown = f"{billions:,.1f}B" if billions < 1000 else f"{billions / 1000:,.1f}T"
    st.markdown(fact(shown, "Output tokens per Bitcoin",
                     f"${price:,.2f} a million · {row['lab']}"),
                unsafe_allow_html=True)
    control("produce", labels,
            "Words out of an AI model that a Bitcoin pays for. One flagship from each "
            "frontier lab. This is what output costs, not what it is worth.")


def card_borrow(credit, stack) -> None:
    if stack.empty:
        st.markdown(fact("n/a", "Of Bitcoin's market cap", "table unavailable"),
                    unsafe_allow_html=True)
        return
    views = ["Everything borrowed", "Secured only", "Contingent guarantees"]
    choice = remember("borrow", views)
    if choice == "Contingent guarantees":
        _, contingent = btc_history.split_contingent(credit)
        subset = btc_history.debt_in_btc(contingent, load_btc_long(), exclude_contingent=False)
        share = subset["share_at_issue"].sum() if not subset.empty else 0.0
        foot = "promised, not drawn"
    elif choice == "Secured only":
        secured = stack[stack["seniority"] == "secured"]
        share = secured["share_at_issue"].sum()
        foot = f"{len(secured)} loans with collateral"
    else:
        share = stack["cumulative_share"].iloc[-1]
        foot = f"{stack['cumulative_btc'].iloc[-1] / 1e6:.2f}M BTC, {len(stack)} deals"
    st.markdown(fact(f"{share * 100:,.1f}%", "Of Bitcoin's market cap", foot),
                unsafe_allow_html=True)
    control("borrow", views,
            "Money raised against AI computing since 2023. Each deal is measured against "
            "Bitcoin's whole market capitalisation on the day it was signed, and those shares "
            "are added up, so a deal's contribution never changes once it is struck.")


# ------------------------------------------------------------------- charts

def chart_power(series, ornn) -> go.Figure:
    join = series["as_of_date"].iloc[0]
    figure = go.Figure()
    lines = [("Advertised prices", series["as_of_date"],
              100 * series["compute_per_btc"] / series["compute_per_btc"].iloc[0], POWER, 2.9)]

    if ornn is not None and not ornn.empty:
        anchor = ornn[ornn["as_of_date"] <= join]
        if not anchor.empty:
            # Both rebased to the day the newer record starts, so the older line
            # carries the history before it and the two meet at 100 on the join.
            lines.append(("Marketplace index", ornn["as_of_date"],
                          100 * ornn["compute_per_btc"] / anchor["compute_per_btc"].iloc[-1],
                          MONEY, 1.5))

    for name, x, y, colour, width in lines:
        figure.add_trace(go.Scatter(x=x, y=y, name=name, mode="lines",
                                    line=dict(width=width, color=colour)))
        figure.add_trace(go.Scatter(
            x=[x.iloc[-1]], y=[y.iloc[-1]], mode="markers+text",
            marker=dict(size=7, color=colour), text=[f"  {y.iloc[-1]:.0f}"],
            textposition="middle right",
            textfont=dict(color=colour, size=12, family="JetBrains Mono, monospace"),
            showlegend=False, hoverinfo="skip",
        ))
    figure.add_hline(y=100, line_dash="dot", line_color="rgba(150,148,140,0.22)")
    return figure


def chart_stack(stack) -> go.Figure:
    share = stack["cumulative_share"] * 100
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=stack["issued_on"], y=share, mode="lines", showlegend=False,
        line=dict(width=2.2, color=POWER, shape="hv"),
        fill="tozeroy", fillcolor="rgba(57,135,229,0.09)", name="Share of all Bitcoin",
    ))
    figure.add_trace(go.Scatter(
        x=[stack["issued_on"].iloc[-1]], y=[share.iloc[-1]], mode="markers+text",
        marker=dict(size=7, color=POWER), text=[f"{share.iloc[-1]:.1f}%  "],
        textposition="middle left",
        textfont=dict(color=POWER, size=12, family="JetBrains Mono, monospace"),
        showlegend=False, hoverinfo="skip",
    ))
    figure.update_yaxes(ticksuffix="%")
    return figure


# --------------------------------------------------------------------- page

def main() -> None:
    st.set_page_config(page_title="What one Bitcoin buys", layout="wide",
                       initial_sidebar_state="collapsed")
    theme.apply(st)
    st.title("What one Bitcoin buys")
    st.markdown(
        '<p class="standfirst">Bitcoin has a hard limit: 21 million coins, ever. '
        '<b>Computing power has none.</b> The world keeps building more chips, and it is '
        'borrowing heavily to do it. This page uses the fixed thing as a ruler for the '
        'growing one.</p>',
        unsafe_allow_html=True,
    )

    with st.spinner(""):
        try:
            panel = load_rates()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load compute rates: {exc}")
            st.stop()

    first, last = panel["as_of_date"].min().date(), panel["as_of_date"].max().date()
    window = load_btc_window((first - timedelta(days=2)).isoformat(),
                             (last + timedelta(days=1)).isoformat())
    try:
        series = ccir.attach_btc(
            ccir.build_basket(ccir.select_surface(panel, **SURFACE), BASKET), window)
    except ValueError as exc:
        st.error(f"Could not build the compute basket: {exc}")
        st.stop()

    latest_btc = float(series["btc_usd"].iloc[-1])
    hardware, tokens, credit = load_hardware(), load_tokens(), load_credit()
    stack = btc_history.debt_in_btc(credit, load_btc_long()) if not credit.empty else pd.DataFrame()

    slots = st.columns(4, gap="medium")
    with slots[0]:
        with st.container(border=True):
            card_rent(panel, series, latest_btc)
    with slots[1]:
        with st.container(border=True):
            card_own(hardware, latest_btc)
    with slots[2]:
        with st.container(border=True):
            card_produce(tokens, latest_btc)
    with slots[3]:
        with st.container(border=True):
            card_borrow(credit, stack)

    # -------------------------------------------------------- purchasing power
    st.subheader("Is it buying more, or less")
    st.markdown(
        '<p class="dek">A GPU-hour is one hour of one AI chip, rented from a data centre. '
        'Both lines start at 100, so only the change matters. Rising means a Bitcoin '
        'commands more computing power than it did.</p>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(theme.chart(chart_power(series, load_ornn()), 360),
                    use_container_width=True, config=PLOT_CONFIG)

    split = btc_history.decompose(series["compute_per_btc"], series["btc_usd"],
                                  series["hardware_basket"])
    direction = "more" if split["purchasing_power_pct"] >= 0 else "less"
    driver = "Bitcoin moving" if split["driver"] == "money" else "compute prices moving"
    st.markdown(
        f'<div class="readout">Over the last {len(series)} days one Bitcoin buys '
        f'<b>{abs(split["purchasing_power_pct"]):.1f}% {direction}</b> computing power.'
        f'<span class="aside">Bitcoin moved {split["btc_pct"]:+.1f}% and compute '
        f'{split["compute_price_pct"]:+.1f}%, so this is mostly {driver}.</span></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Why 50/30/20, and what that basket hides"):
        st.markdown(BASKET_NOTE)

    # ------------------------------------------------------------------ stack
    if not stack.empty:
        st.subheader("What the buildout borrowed, measured in Bitcoin")
        st.markdown(
            '<p class="dek">Data centres are built with debt. Every loan and bond raised '
            'against AI computing since 2023, each one measured against the entire market '
            'capitalisation of Bitcoin on the day it was signed, then added up.</p>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(theme.chart(chart_stack(stack), 320),
                        use_container_width=True, config=PLOT_CONFIG)

        recent = stack[stack["issued_on"] >= stack["issued_on"].max() - pd.Timedelta(days=90)]
        share = stack["cumulative_share"].iloc[-1] * 100
        aside = (f'Priced at signing, so the line moves only when someone borrows, never when '
                 f'Bitcoin does. The last 90 days added '
                 f'{recent["share_at_issue"].sum() * 100:.1f} points across '
                 f'{len(recent)} deals.')

        _, contingent = btc_history.split_contingent(credit)
        guaranteed = btc_history.debt_in_btc(contingent, load_btc_long(),
                                             exclude_contingent=False)
        if not guaranteed.empty:
            aside += (f' Excluded: a contingent guarantee worth another '
                      f'{guaranteed["share_at_issue"].sum() * 100:.1f}% on its own, '
                      f'NVIDIA standing behind the leases under OpenAI. A promise to pay is '
                      f'not money drawn.')
        st.markdown(
            f'<div class="readout">The AI buildout has borrowed the equivalent of '
            f"<b>{share:.1f}% of Bitcoin's market capitalisation</b>, each deal "
            f'measured on the day it was signed.'
            f'<span class="aside">{aside}</span></div>',
            unsafe_allow_html=True,
        )

    # ------------------------------------------------------------- fine print
    with st.expander("New here? Start with this"):
        st.markdown(PRIMER)
    with st.expander("Sources, and what these numbers cannot tell you"):
        st.markdown(SOURCES.format(first=first, last=last))


BASKET_NOTE = """
Chips are not interchangeable. An H100 is the workhorse of the last training
cycle, an H200 its higher-memory successor, a B200 the current generation at
roughly twice the hourly price. Quoting any one alone would make the chart a
story about that chip rather than about computing power. So three are blended:

**basket price = 0.5 x H100 + 0.3 x H200 + 0.2 x B200**

The weights lean towards H100 because it is still the most widely deployed, and
away from B200 because it was scarce for most of the period.

**The weights are reasoned, not measured.** Nobody publishes how many of each
chip are actually rented, so 50/30/20 is a judgement, not an observation. It is
the softest assumption on this page, and if real deployment shares are ever
published they should replace it. Meanwhile the first card lets you pick any
single chip and see the difference: H100 alone and B200 alone sit about 70%
apart.

One technical note. The basket averages *prices* and then converts, which is how
a price index normally works. Averaging the *hours* instead gives a slightly
higher number, because the average of several reciprocals is not the reciprocal
of their average. Both are defensible; this page does the first.
"""

PRIMER = """
**Bitcoin** is money with a hard cap. Twenty-one million coins will ever exist,
about 20.1 million have been mined, and nobody can decide to make more. That is
the only reason it works as a ruler here: a measuring stick has to hold still.

**A GPU** is the specialised chip AI models are trained and run on. NVIDIA makes
almost all the ones that matter. Unlike Bitcoin, the supply of them keeps
growing.

**A GPU-hour** is renting one of those chips for an hour, the way you rent a
server. Three to six dollars, depending on the chip and the deal. It is the unit
the industry buys computing power in.

**A token** is roughly three quarters of a word. Using an AI model costs a price
per million tokens it reads and writes. That is the *finished product* of all
this hardware, so its price is a different question from what the chips cost,
and the two need not move together.

**A neocloud** is a company that does nothing but rent out GPUs, like CoreWeave
or Nebius, rather than Amazon or Microsoft renting them alongside everything
else. Their prices are the cleanest read on what computing power actually costs.

**Why this matters.** Building AI takes enormous borrowed money, secured against
chips that lose value fast. If computing power keeps getting cheaper faster than
the debt is repaid, somebody eventually eats the difference. Watching what a
fixed quantity of money buys is one way of watching that race.
"""

SOURCES = """
**Compute rates** are CCIR (ccir.io) advertised prices, US dollars per chip per
hour, neocloud SXM on demand, published each morning. The record here runs
{first} to {last}. It begins three weeks after Bitcoin's 2026-06-30 low, which is
why the marketplace index is drawn alongside: it reaches back to 2026-05-19 and
can see either side of that low.

**Card prices** are transacted secondary-market medians. Samples are thin, often
under ten sales in 90 days.

**Token prices** are posted output prices, not model quality and not a measure of
intelligence. Only four of 63 tracked models have changed price since the record
began on 2026-07-03, so there is a level here but no trend yet.

**Borrowing** is CCIR's compute-debt tracker. Eight rows name an incremental
tranche or an upsized facility, $8.1B in total; whether those sit on top of the
parent loan or inside it cannot be told from the table, so the stack may be
overstated by up to about 2%. No duplicated issuer-and-instrument pair appears
anywhere in the 175 rows. Notional is face value, not market value, and the
tracker mixes debt secured on chips with debt secured on buildings.

Compute data: CCIR (ccir.io). Bitcoin price: Coinbase. Supply: computed from
block height. Not investment advice.
"""


if __name__ == "__main__":
    main()
