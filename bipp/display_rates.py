"""Read the published rates for a page, using our dated history during an outage."""

from __future__ import annotations

import pandas as pd

from . import ccir, store


def load_panel(*, include_history: bool) -> tuple[pd.DataFrame, bool]:
    """Return the display panel and whether only archived observations were available."""
    try:
        live = ccir.load_panel(ccir.fetch_catalog(), ccir.fetch_history())
        if live.empty:
            raise ValueError("CCIR returned no matching rate observations.")
    except (OSError, ValueError, KeyError, EOFError):
        stored = store.read("rates")
        if stored.empty:
            raise
        return stored, True

    if include_history:
        try:
            return store.merge_rates(live), False
        except (OSError, ValueError, KeyError, EOFError):
            # The live data is still usable when local history cannot be read.
            pass
    return live, False
