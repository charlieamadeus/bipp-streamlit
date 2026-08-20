"""CCIR page scrapers for the three axes with no CSV endpoint.

CCIR ships CSV for rental rates only (rates_daily.csv, rates_history.csv).
Token prices, hardware residuals, and the GPU-backed debt tracker are rendered
as static HTML tables. This module reads those tables.

Reliability note: these are scrapers against page markup, not a published data
contract. Rental rates come from CSV and are stable; anything in here can break
on a site redesign without warning. Every parser returns an empty frame rather
than a wrong one when the table shape changes, and the app labels these three
axes as scraped so a blank panel is never mistaken for a real reading.

Attribution requirement: cite as "CCIR (ccir.io)" with the publication date.
"""

from __future__ import annotations

import html
import re
from urllib.request import Request, urlopen

import pandas as pd


TOKENS_URL = "https://ccir.io/tokens"
HARDWARE_URL = "https://ccir.io/hardware"
CREDIT_URL = "https://ccir.io/credit"

TOKEN_HEADER = ["Model", "Input", "Cached", "Output", "30d", "Last reprice"]
TOKEN_MEDIAN_HEADER = ["Model", "Providers", "Input median", "Output median", "Output range"]
HARDWARE_HEADER_START = ["Model", "Intro", "Age"]
CREDIT_HEADER_START = ["Issuer", "Instrument", "Type", "Size $M"]


def _fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "bipp-streamlit/2.0 (research)"})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def _strip(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", markup)).replace("\xa0", " ").strip()


def _tables(page: str) -> list[tuple[list[str], list[list[str]]]]:
    """Return (header, rows) for every table on the page."""
    out = []
    for block in re.findall(r"<table.*?</table>", page, re.S):
        header = [_strip(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", block, re.S)]
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
            cells = [_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if cells:
                rows.append(cells)
        if header and rows:
            out.append((header, rows))
    return out


def _tables_with_headings(page: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """Same, plus the nearest preceding h3, which on /tokens names the provider.

    CCIR groups token prices under an h2 construction ("Frontier posted -
    first-party" versus "Open-weight models - by serving breadth") and an h3 per
    provider ("OA OpenAI standard tier"). The provider is what makes a flagship
    filter possible without hand-curating a model list.
    """
    out = []
    for match in re.finditer(r"<table.*?</table>", page, re.S):
        before = page[:match.start()]
        headings = re.findall(r"<h3[^>]*>(.*?)</h3>", before, re.S)
        provider = _strip(headings[-1]) if headings else ""
        block = match.group()
        header = [_strip(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", block, re.S)]
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
            cells = [_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if cells:
                rows.append(cells)
        if header and rows:
            out.append((provider, header, rows))
    return out


def _money(text: str) -> float | None:
    """Parse the leading number out of $2.00, $27,155, 1,438, 86.2% [B], 1.41x.

    Deliberately unit-blind: no B/M suffix scaling happens here. Grade markers
    like "76.6% [B]" sit in the same cells as magnitudes, so a suffix rule
    would read that B as billions. Callers know their column's unit and apply
    scaling themselves.
    """
    if not text:
        return None
    cleaned = text.replace(",", "").replace("$", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _year(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else None


def fetch_tokens(page: str | None = None) -> pd.DataFrame:
    """Token prices in USD per 1 million tokens.

    pricing_basis distinguishes first-party posted prices from cross-provider
    medians for open-weight models. CCIR keeps these unpooled on purpose: a
    posted price and a cross-provider median answer different questions, so
    they are kept apart here too.
    """
    page = page or _fetch_html(TOKENS_URL)
    records = []
    for provider, header, rows in _tables_with_headings(page):
        if header[:4] == TOKEN_HEADER[:4]:
            for cells in rows:
                if len(cells) < 4:
                    continue
                records.append({
                    "model": cells[0],
                    "provider": provider,
                    "input_usd_per_mtok": _money(cells[1]),
                    "cached_usd_per_mtok": _money(cells[2]),
                    "output_usd_per_mtok": _money(cells[3]),
                    "change_30d": cells[4] if len(cells) > 4 else "",
                    "last_reprice": cells[5] if len(cells) > 5 else "",
                    "pricing_basis": "first_party_posted",
                })
        elif header[:2] == TOKEN_MEDIAN_HEADER[:2]:
            for cells in rows:
                if len(cells) < 4:
                    continue
                records.append({
                    "model": cells[0],
                    "provider": provider,
                    "providers": cells[1],
                    "input_usd_per_mtok": _money(cells[2]),
                    "output_usd_per_mtok": _money(cells[3]),
                    "output_range": cells[4] if len(cells) > 4 else "",
                    "pricing_basis": "cross_provider_median",
                })
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    return frame.dropna(subset=["output_usd_per_mtok"]).reset_index(drop=True)


# The models worth pricing in Bitcoin, in order, strongest lab first.
#
# This is a curated list, not a rule, and that is deliberate. Ranking a lab's
# models by posted price recovers a capability tier only roughly: it put a
# robotics build and a code-speed variant on the page, and it cannot know that
# a cheaper model is the one people actually use. Ordering is a judgement about
# which lab sets the standard, so it lives here where it can be argued with.
#
# Names are matched exactly against CCIR's model column. A name that stops
# being published simply drops out rather than breaking the list.
FLAGSHIPS: list[tuple[str, str]] = [
    ("Claude Fable 5", "Anthropic"),
    ("Claude Opus 5", "Anthropic"),
    ("Claude Sonnet 5", "Anthropic"),
    ("gpt-5.6-sol", "OpenAI"),
    ("gpt-5.6-terra", "OpenAI"),
    ("gpt-5.6-luna", "OpenAI"),
    ("gemini-3.1-pro-preview", "Google"),
    ("grok-4.6", "xAI"),
    ("deepseek-v4-pro", "DeepSeek"),
    ("kimi-k3", "Moonshot"),
]


# One curated list drives both hardware cards, so the two menus read the same
# way, in the same order, under the same names. Ordered by capability: the
# current frontier part first, down to what somebody has in a desktop.
#
# `rent` is the rental panel's gpu_model; `own` is the resale panel's model
# string. Either may be None, because the two surfaces do not cover the same
# market. CCIR's resale page prices used ENTERPRISE cards, because that page
# exists to value collateral, and a gaming card is not collateral for a data
# centre loan. So a 5090 can be rented on this page and not bought on it, and
# Blackwell parts are too new to have a second-hand market at all.
#
# GH200 is left out on purpose: 4 sources over 15 days with a 25.6% worst daily
# move, against B300's 10 sources over 31. It is a thin cell, not a bad chip.
CHIPS: list[tuple[str, str | None, str | None]] = [
    ("B300",      "B300",  None),
    ("B200",      "B200",  None),
    ("H200",      "H200",  "H200 141GB"),
    ("H100",      "H100",  "H100 80GB SXM5"),
    ("A100",      "A100",  "A100 80GB SXM4"),
    ("L40S",      "L40S",  "L40S 48GB"),
    ("RTX A6000", "A6000", "RTX A6000 48GB"),
    ("RTX 5090",  "5090",  None),
    ("RTX 3090",  "3090",  None),
]

RENT_KEYS = {label: rent for label, rent, _ in CHIPS if rent}
OWN_KEYS = {label: own for label, _, own in CHIPS if own}


def frontier_models(tokens: pd.DataFrame) -> pd.DataFrame:
    """The curated flagship list, in FLAGSHIPS order.

    First-party posted prices only. CCIR never pools a posted price with a
    cross-provider median because they answer different questions, so neither
    does this.
    """
    if tokens.empty or "model" not in tokens.columns:
        return tokens
    posted = tokens[tokens["pricing_basis"] == "first_party_posted"]
    if posted.empty:
        return posted

    rows = []
    for rank, (name, lab) in enumerate(FLAGSHIPS):
        match = posted[posted["model"].astype(str).str.strip() == name]
        if match.empty:
            continue
        row = match.iloc[0].to_dict()
        row["lab"] = lab
        row["rank"] = rank
        rows.append(row)
    if not rows:
        return posted.iloc[0:0]
    return pd.DataFrame(rows).sort_values("rank").reset_index(drop=True)


HARDWARE_HEADER_START = ["Model", "Intro", "Age"]
CREDIT_HEADER_START = ["Issuer", "Instrument", "Type", "Size $M"]


def fetch_hardware(page: str | None = None) -> pd.DataFrame:
    """Secondary-market GPU prices in USD per card.

    executed_median_usd is transacted; posted_ask_usd is dealer listing. The
    gap between them is the bid-ask on physical compute, and it widens with
    age: V100 asks 1.18x executed, A100 80GB SXM4 asks 2.30x.

    Datacentre parts only. CCIR publishes no secondary-market price for
    consumer cards (no 3090, 4090 or 5090 appears anywhere on the page), and
    Ornn's index covers rental rates rather than card prices, so there is no
    source here for what a Bitcoin buys in home hardware.
    """
    page = page or _fetch_html(HARDWARE_URL)
    for header, rows in _tables(page):
        if header[:3] != HARDWARE_HEADER_START:
            continue
        records = []
        for cells in rows:
            if len(cells) < 6:
                continue
            records.append({
                "model": cells[0],
                "intro_year": _year(cells[1]),
                "age_years": _year(cells[2]),
                "executed_median_usd": _money(cells[3]),
                "executed_n": _money(cells[4]),
                "posted_ask_usd": _money(cells[5]),
                "ask_over_executed": _money(cells[7]) if len(cells) > 7 else None,
                "pct_of_launch": _money(cells[8]) if len(cells) > 8 else None,
            })
        frame = pd.DataFrame(records)
        if frame.empty:
            return frame
        return frame.dropna(subset=["executed_median_usd"]).reset_index(drop=True)
    return pd.DataFrame()


def fetch_credit(page: str | None = None) -> pd.DataFrame:
    """GPU-backed and compute-adjacent debt instruments.

    size_musd is notional in USD millions. rate is left as posted text because
    it mixes fixed coupons and SOFR spreads, which must not be silently pooled.
    """
    page = page or _fetch_html(CREDIT_URL)
    for header, rows in _tables(page):
        if header[:4] != CREDIT_HEADER_START:
            continue
        records = []
        for cells in rows:
            if len(cells) < 6:
                continue
            records.append({
                "issuer": cells[0],
                "instrument": cells[1],
                "type": cells[2],
                "size_musd": _money(cells[3]),
                "rate": cells[4],
                "issued": cells[5],
                "maturity": cells[6] if len(cells) > 6 else "",
                "seniority": cells[7] if len(cells) > 7 else "",
                "status": cells[9] if len(cells) > 9 else "",
            })
        frame = pd.DataFrame(records)
        if frame.empty:
            return frame
        return frame.dropna(subset=["size_musd"]).reset_index(drop=True)
    return pd.DataFrame()


BTC_TERMINAL_SUPPLY = 21_000_000


def gpus_per_btc(hardware: pd.DataFrame, btc_usd: float) -> pd.DataFrame:
    """How many physical cards one BTC buys, at executed and at ask."""
    if hardware.empty:
        return hardware
    out = hardware.copy()
    out["gpus_per_btc_executed"] = btc_usd / out["executed_median_usd"]
    out["gpus_per_btc_ask"] = btc_usd / out["posted_ask_usd"]
    return out.sort_values("gpus_per_btc_executed").reset_index(drop=True)


def tokens_per_btc(tokens: pd.DataFrame, btc_usd: float) -> pd.DataFrame:
    """Billions of tokens one BTC buys, at posted output and input prices."""
    if tokens.empty:
        return tokens
    out = tokens.copy()
    out["output_btok_per_btc"] = btc_usd / out["output_usd_per_mtok"] / 1_000
    out["input_btok_per_btc"] = btc_usd / out["input_usd_per_mtok"] / 1_000
    return out.sort_values("output_btok_per_btc").reset_index(drop=True)


def credit_in_btc(credit: pd.DataFrame, btc_usd: float) -> pd.DataFrame:
    """Debt notional restated in BTC."""
    if credit.empty:
        return credit
    out = credit.copy()
    out["size_btc"] = out["size_musd"] * 1_000_000 / btc_usd
    return out.sort_values("size_btc", ascending=False).reset_index(drop=True)


def credit_summary(credit: pd.DataFrame, btc_usd: float) -> dict[str, float]:
    if credit.empty:
        return {}
    notional_usd = float(credit["size_musd"].sum()) * 1_000_000
    btc_equivalent = notional_usd / btc_usd
    return {
        "instruments": int(len(credit)),
        "notional_usd": notional_usd,
        "btc_equivalent": btc_equivalent,
        "pct_of_terminal_supply": 100 * btc_equivalent / BTC_TERMINAL_SUPPLY,
    }


# Hardware rows name memory and form factor ("H100 80GB SXM5"); rental rows
# name the bare chip ("H100"). Match on the leading chip token.
def normalize_chip(model: str) -> str:
    token = (model or "").strip().split()
    if not token:
        return ""
    return token[0].upper().replace("-", "")


def payback_hours(hardware: pd.DataFrame, rental_usd_per_hour: dict[str, float]) -> pd.DataFrame:
    """Hours of rental revenue needed to recover a card's current market price."""
    if hardware.empty:
        return hardware
    out = hardware.copy()
    out["chip"] = out["model"].map(normalize_chip)
    out["rental_usd_per_hour"] = out["chip"].map(
        {normalize_chip(k): v for k, v in rental_usd_per_hour.items()}
    )
    out["payback_hours"] = out["executed_median_usd"] / out["rental_usd_per_hour"]
    out["payback_months_full_util"] = out["payback_hours"] / (24 * 30.44)
    return out.dropna(subset=["payback_hours"]).reset_index(drop=True)
