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

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bipp import btc as btc_history
from bipp import ccir, ccir_pages, store, theme
from bipp.pipeline import fetch_coinbase_btc_usd, fetch_live_dataset
from bipp.theme import MONEY, PLOT_CONFIG, POWER, fact

BASKET = {"H100": 0.5, "H200": 0.3, "B200": 0.2}
SURFACE = dict(operator_tier="T2", form_factor="SXM", interruptibility="GTD",
               commitment_term="OnDemand", region="ALL")
# interruptibility="GTD", not "ALL". The page says "on demand", and pooling
# interruptible capacity into that is a different product: spot GPUs can be
# evicted, and they price about a third cheaper. CCIR uses guaranteed as its
# own on-demand anchor on /term. The pooled surface also carried much of the
# apparent panel dispersion: the guaranteed basket band is $3.86-$5.50 against
# the pooled $2.82-$5.16, so most of that width was the spot mix, not provider
# disagreement about the same product.


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

def _two_figures(value: float) -> str:
    """16,979 -> "17,000". The panel's quartiles span nearly two to one, so five
    significant figures assert a precision the data cannot carry."""
    if value <= 0:
        return "0"
    step = 10 ** (math.floor(math.log10(value)) - 1)
    return f"{round(value / step) * step:,.0f}"


def card_rent(panel, series, latest_btc) -> None:
    # Curated list, same order as the card beside it. Anything CCIR cannot
    # price today simply drops out rather than showing a dead option.
    priced = []
    for label, rent, _ in ccir_pages.CHIPS:
        answer = ccir.best_rate(panel, rent) if rent else None
        if answer:
            priced.append((label, answer[0], answer[2], answer[3], answer[1]))
    options = ["The 50/30/20 basket"] + [label for label, _, _, _, _ in priced]
    choice = remember("rent", options)
    if choice == "The 50/30/20 basket":
        hours, foot = series["compute_per_btc"].iloc[-1], "three chips, blended"
        band = ccir.basket_band(ccir.select_surface(panel, **SURFACE), BASKET)
    else:
        rate, market, sources, series_id = next(
            (r, m, n, sid) for label, r, m, n, sid in priced if label == choice)
        hours = latest_btc / rate
        # A thin cell says so on the card. Showing a 4-source price identically
        # to a 10-source one is the part that would mislead.
        foot = (f"${rate:.2f} an hour, {sources} sources, indicative"
                if sources < ccir.INDICATIVE_BELOW
                else f"${rate:.2f} an hour, {market.lower()}")
        band = ccir.price_band(panel, series_id)
    # The panel's own middle half, beside the headline. CCIR publishes p25 and
    # p75 next to every price because the headline alone hides how far providers
    # disagree; on the basket that is roughly 15,000 to 21,000 hours.
    if band:
        foot += f" · {_two_figures(latest_btc / band[1])} to {_two_figures(latest_btc / band[0])} across the panel"
    st.markdown(fact(_two_figures(hours), "GPU-hours per Bitcoin", foot),
                unsafe_allow_html=True)
    control("rent", options,
            "Hours of one chip that a Bitcoin rents. Data centre parts price on the "
            "neocloud SXM market; consumer cards are only listed by marketplaces, so "
            "those are used where nothing better exists. Same chips as the card beside "
            "this one wherever both markets carry them.")


def card_own(hardware, latest_btc) -> None:
    if hardware.empty:
        st.markdown(fact("n/a", "Cards per Bitcoin", "table unavailable"), unsafe_allow_html=True)
        return
    available = set(hardware["model"].astype(str))
    options = [label for label, _, own in ccir_pages.CHIPS if own and own in available]
    if not options:
        st.markdown(fact("n/a", "Cards per Bitcoin", "no curated chip priced"),
                    unsafe_allow_html=True)
        return
    default = options.index("H100") if "H100" in options else 0
    choice = remember("own", options, default)
    row = hardware[hardware["model"].astype(str) == ccir_pages.OWN_KEYS[choice]].iloc[0]
    st.markdown(
        fact(f"{latest_btc / float(row['executed_median_usd']):,.1f}", "Cards per Bitcoin",
             f"${row['executed_median_usd']:,.0f} used, {row['age_years']:.1f} years old"),
        unsafe_allow_html=True,
    )
    control("own", options,
            "Whole chips a Bitcoin buys outright, at prices actually transacted second "
            "hand. Fewer choices than renting, and deliberately: this table values used "
            "data centre hardware as loan collateral, so it carries no consumer cards. "
            "A 5090 can be rented here and not bought here for that reason, and a B200 "
            "is too new to have a second-hand price at all.")


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


def _day(ts) -> str:
    """'30 June 2026'. Built by hand because %-d is glibc-only and %#d is
    Windows-only, so either literal breaks on the other platform."""
    return f"{ts.day} {ts:%B %Y}"


def chart_stack(shares) -> go.Figure:
    """Two readings of one pile of debt. The gap is the message, so both lines
    carry a legend entry and an end label rather than relying on colour alone."""
    committed = shares["committed"] * 100
    marked = shares["marked"] * 100
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=shares["date"], y=committed, mode="lines", name="Committed at signing",
        line=dict(width=2.2, color=POWER, shape="hv"),
        fill="tozeroy", fillcolor="rgba(57,135,229,0.09)",
    ))
    figure.add_trace(go.Scatter(
        x=shares["date"], y=marked, mode="lines", name="Marked to Bitcoin today",
        line=dict(width=1.8, color=MONEY),
    ))
    for series, colour in ((committed, POWER), (marked, MONEY)):
        figure.add_trace(go.Scatter(
            x=[shares["date"].iloc[-1]], y=[series.iloc[-1]], mode="markers+text",
            marker=dict(size=7, color=colour), text=[f"  {series.iloc[-1]:.1f}%"],
            textposition="middle right",
            textfont=dict(color=colour, size=12, family="JetBrains Mono, monospace"),
            showlegend=False, hoverinfo="skip",
            # Without this the label is clipped to the plot area and vanishes.
            # chart_power gets away without it only because it sets no explicit
            # x range, so plotly auto-pads instead.
            cliponaxis=False,
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
            '<p class="dek">Data centres are built with debt. The borrowing CCIR records '
            'against AI computing since 2023, read two ways. Blue is what each deal was '
            'worth against the whole of Bitcoin on the day it was signed, added up and then '
            'left alone. Orange is all of it measured against Bitcoin today.</p>',
            unsafe_allow_html=True,
        )
        shares = btc_history.debt_share_series(stack, load_btc_long())
        st.plotly_chart(theme.chart(chart_stack(shares), 340, right=62),
                        use_container_width=True, config=PLOT_CONFIG)

        committed = shares["committed"].iloc[-1] * 100
        marked = shares["marked"].iloc[-1] * 100
        gap = marked - committed
        # Deliberately not "the borrowing grew faster". Debt growth moves both
        # lines, so the gap cannot attribute itself. What it measures is where
        # Bitcoin's market cap sits now against the deal-size-weighted level
        # that prevailed when the money was raised.
        below = "below" if gap > 0 else "above"

        # The widest the two readings have ever been apart, and the last time
        # they swapped places. Both are computed, never written down, because
        # the answer changes every time Bitcoin moves.
        spread = (shares["marked"] - shares["committed"]) * 100
        peak_at = spread.abs().idxmax()
        crossings = spread[(spread.shift() * spread) < 0]
        headline = (f'Signed at the time, the buildout borrowed '
                    f"<b>{committed:.1f}% of Bitcoin</b>. Against Bitcoin today the same "
                    f"debt is <b>{marked:.1f}%</b>.")

        aside = (f"The gap is {abs(gap):.1f} points. It says Bitcoin's market cap today sits "
                 f'{below} the deal-weighted level that prevailed when this money was raised. '
                 f'Borrowing moves both lines, so the gap on its own does not say which grew '
                 f'faster. Widest it has been: {abs(spread[peak_at]):.1f} points on '
                 f'{_day(shares["date"].iloc[peak_at])}.')
        if len(crossings):
            aside += (f' The two readings last swapped places in '
                      f'{shares["date"].iloc[crossings.index[-1]]:%B %Y}.')

        # Coverage stated on the page, not assumed. Silently dropping rows that
        # failed to parse once cut this chart to 53% of the tracker while the
        # text claimed it showed everything.
        cov = btc_history.debt_coverage(credit)
        aside += (f' Covers {cov["rows_shown"]} of the {cov["rows_total"]} rows CCIR records '
                  f'and ${cov["usd_shown"] / 1000:,.0f}B of ${cov["usd_total"] / 1000:,.0f}B.')
        parts = [f'${usd / 1000:,.1f}B {reason}' for reason, (n, usd) in cov["excluded"].items()
                 if n]
        if parts:
            aside += ' Left out: ' + '; '.join(parts) + '.'
        vague = sum(v for k, v in cov["precision"].items() if k != "day")
        if vague:
            aside += (f' {vague} included rows carry only a month or a year for their issue '
                      f'date, so their step lands on the first of that period.')
        st.markdown(
            f'<div class="readout">{headline}'
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
hour, neocloud SXM, guaranteed on demand, published each morning. Guaranteed
rather than pooled with interruptible capacity: spot GPUs can be evicted and
price about a third cheaper, so blending them would make the number depend on
the spot share of the panel. The headline is shown to two significant figures
with the panel's own interquartile range beside it, because providers disagree
by roughly forty percent about the same product on the same day. The record here runs
{first} to {last}. It begins three weeks after Bitcoin's 2026-06-30 low, which is
why the marketplace index is drawn alongside: it reaches back to 2026-05-19 and
can see either side of that low.

**Card prices** are transacted secondary-market medians. Samples are thin, often
under ten sales in 90 days.

**Token prices** are posted output prices, not model quality and not a measure of
intelligence. Only four of 63 tracked models have changed price since the record
began on 2026-07-03, so there is a level here but no trend yet.

**Borrowing** is CCIR's compute-debt tracker, and the chart states its own
coverage rather than implying it shows everything. Three rows name an
incremental tranche or an upsized facility, $4.1B in total, about 1.2% of the
plotted stack; whether those sit on top of the parent loan or inside it cannot
be told from the table. No duplicated issuer-and-instrument pair appears in the
175 rows.

Two limits are larger than that. Notional is the amount at close, not the drawn
balance: nothing here models drawdown, amortisation, repayment or refinancing,
so this is issuance, not debt outstanding. And "borrowing against AI computing"
covers instruments that are not economically alike, from GPU-collateralised
facilities to campus securitisations, corporate convertibles and unsecured
notes. Read the total as a financing footprint, not as compute-backed debt.

An earlier version of this note claimed eight rows and $8.1B. That figure did
not survive being measured and has been replaced by the one above.

Compute data: CCIR (ccir.io). Bitcoin price: Coinbase. Supply: computed from
block height. Not investment advice.
"""


if __name__ == "__main__":
    main()
