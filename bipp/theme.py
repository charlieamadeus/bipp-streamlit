"""Page styling.

Streamlit's defaults read like a form. This makes the page read like a
publication: one measured column, an editorial serif for headings, a mono
for every number so digits line up between cards, and Streamlit's own chrome
turned off.
"""

from __future__ import annotations

# dataviz reference palette, dark steps, validated against a dark surface.
POWER = "#3987e5"        # what a Bitcoin buys
MONEY = "#d95926"        # the second source
INK = "#e9e7e2"
INK_DIM = "#9a978f"
INK_FAINT = "#6b6862"
SURFACE = "#0e1013"
RAISED = "#15181d"
RULE = "rgba(150,148,140,0.15)"

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Instrument+Serif:ital@0;1&"
    "family=Inter:wght@400;500;600&"
    "family=JetBrains+Mono:wght@400;500&"
    'display=swap" rel="stylesheet">'
)

CSS = f"""<style>
  :root {{
    --ink: {INK}; --dim: {INK_DIM}; --faint: {INK_FAINT};
    --rule: {RULE}; --raised: {RAISED}; --power: {POWER}; --money: {MONEY};
  }}
  /* Streamlit's own chrome: header bar, hamburger, footer, deploy button. */
  header[data-testid="stHeader"], #MainMenu, footer,
  [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
  .stApp {{ background: {SURFACE}; }}
  /* One measured column instead of a full-bleed left margin. */
  [data-testid="stMainBlockContainer"] {{
    max-width: 1080px; padding: 3.4rem 2rem 5rem; margin: 0 auto;
  }}
  html, body, [class*="css"], .stMarkdown, p, li, div {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  h1 {{
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-weight: 400 !important; font-size: 3.6rem !important;
    line-height: 1.02 !important; letter-spacing: -0.015em !important;
    color: var(--ink) !important; margin: 0 0 1.1rem !important; padding: 0 !important;
  }}
  h2, h3 {{
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-weight: 400 !important; font-size: 1.95rem !important;
    letter-spacing: -0.01em !important; color: var(--ink) !important;
    margin: 3.2rem 0 0.4rem !important; padding: 0 !important;
  }}
  /* Standfirst under the title. */
  .standfirst {{
    font-size: 1.22rem; line-height: 1.55; color: var(--dim);
    max-width: 34em; margin: 0 0 2.6rem; font-weight: 400;
  }}
  .standfirst b {{ color: var(--ink); font-weight: 500; }}
  /* One-line dek under a section heading. */
  .dek {{
    font-size: 0.97rem; line-height: 1.6; color: var(--dim);
    max-width: 40em; margin: 0 0 1.5rem;
  }}
  /* Fact cards. */
  .num {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 2.35rem; font-weight: 500; line-height: 1;
    letter-spacing: -0.035em; color: var(--ink); margin: 0.1rem 0 0;
  }}
  .cap {{
    font-size: 0.665rem; letter-spacing: 0.13em; text-transform: uppercase;
    color: var(--faint); margin-top: 0.62rem; font-weight: 500;
  }}
  .foot {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.7rem; color: var(--faint); margin-top: 0.3rem;
    min-height: 2.1em; line-height: 1.5;
  }}
  [data-testid="stVerticalBlockBorderWrapper"] {{
    background: var(--raised); border: 1px solid var(--rule) !important;
    border-radius: 3px !important; padding: 1.15rem 1.25rem 1.05rem !important;
  }}
  /* Selectboxes: a quiet control, not the loudest thing in the card. */
  .stSelectbox div[data-baseweb="select"] > div {{
    background: transparent !important; border: none !important;
    border-bottom: 1px solid var(--rule) !important; border-radius: 0 !important;
    min-height: 0 !important; padding: 0 0 0.42rem !important;
  }}
  .stSelectbox div[data-baseweb="select"] div[value],
  .stSelectbox div[data-baseweb="select"] input {{
    font-size: 0.775rem !important; color: var(--dim) !important;
    font-family: 'Inter', sans-serif !important;
  }}
  .stSelectbox svg {{ fill: var(--faint) !important; width: 15px; height: 15px; }}
  .stSelectbox {{ margin-bottom: 0.55rem !important; }}
  [data-testid="stTooltipIcon"] svg {{ width: 13px; height: 13px; }}
  /* The read-out under a chart: a pull-quote, not an alert box. */
  .readout {{
    font-size: 1.13rem; line-height: 1.62; color: var(--ink);
    border-left: 2px solid var(--power); padding: 0.15rem 0 0.15rem 1.15rem;
    margin: 1.5rem 0 0.5rem; max-width: 44em; font-weight: 400;
  }}
  .readout b {{ font-weight: 600; }}
  .readout .aside {{
    display: block; margin-top: 0.7rem; font-size: 0.9rem;
    color: var(--dim); line-height: 1.55;
  }}
  /* Expanders. */
  [data-testid="stExpander"] {{ border: none !important; background: transparent !important; }}
  [data-testid="stExpander"] details {{
    border: none !important; border-top: 1px solid var(--rule) !important;
    border-radius: 0 !important; background: transparent !important;
  }}
  [data-testid="stExpander"] details summary {{ padding-left: 0 !important; }}
  [data-testid="stExpander"] summary {{
    font-size: 0.83rem !important; color: var(--dim) !important;
    padding: 0.85rem 0 !important; font-weight: 500 !important;
  }}
  [data-testid="stExpander"] summary:hover {{ color: var(--ink) !important; }}
  [data-testid="stExpander"] p, [data-testid="stExpander"] li {{
    font-size: 0.9rem !important; line-height: 1.68 !important; color: var(--dim) !important;
    max-width: 46em;
  }}
  [data-testid="stExpander"] strong {{ color: var(--ink) !important; }}
  .stPlotlyChart {{ margin: 0.2rem 0 0; }}
  hr {{ border-color: var(--rule); margin: 3rem 0 0; }}
</style>"""


def apply(st) -> None:
    st.markdown(FONTS + CSS, unsafe_allow_html=True)


def chart(figure, height: int, right: int = 4):
    """Shared chart styling: no modebar, no chart-junk, palette text colours.

    `right` widens the right margin for charts that label their last point
    outside the plot area; at the default 4px such a label is clipped.
    """
    figure.update_layout(
        height=height, margin=dict(l=4, r=right, t=6, b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12, color=INK_DIM, family="Inter, sans-serif"),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=RAISED, bordercolor=RULE,
                        font=dict(color=INK, family="JetBrains Mono, monospace", size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.03, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=INK_DIM)),
    )
    figure.update_xaxes(showgrid=False, showline=False, zeroline=False,
                        ticks="", tickfont=dict(size=11, color=INK_FAINT))
    figure.update_yaxes(showgrid=True, gridcolor=RULE, zeroline=False, showline=False,
                        ticks="", tickfont=dict(size=11, color=INK_FAINT),
                        title_font=dict(size=11, color=INK_FAINT))
    return figure


PLOT_CONFIG = {"displayModeBar": False, "scrollZoom": False}


def fact(number: str, label: str, foot: str = "") -> str:
    return (f'<div class="num">{number}</div>'
            f'<div class="cap">{label}</div><div class="foot">{foot}</div>')
