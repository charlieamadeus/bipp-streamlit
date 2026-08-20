"""Our own accumulating history.

CCIR keeps roughly 30 days of rental history and publishes residuals, token
prices and debt as snapshots with no history at all. Everything older than that
window is gone once it rolls off. This module appends a dated copy of each pull
to CSVs in the repo, so the record grows past what CCIR retains and the three
snapshot-only surfaces eventually become series.

Append-only by construction: a row already recorded for a given key and date is
never rewritten, so a bad pull cannot silently overwrite good history. Run
`py -3 scripts/snapshot.py` daily and commit the result.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

STORE = Path(__file__).resolve().parent.parent / "data" / "history"

# Each series: filename, and the columns that make a row unique within a day.
SERIES: dict[str, list[str]] = {
    "rates": ["as_of_date", "series_id"],
    "hardware": ["as_of_date", "model"],
    "tokens": ["as_of_date", "model", "pricing_basis"],
    "credit": ["as_of_date", "issuer", "instrument"],
}


def path_for(name: str) -> Path:
    if name not in SERIES:
        raise ValueError(f"Unknown series: {name}. Known: {sorted(SERIES)}")
    return STORE / f"{name}.csv"


def read(name: str) -> pd.DataFrame:
    """Everything recorded so far. Empty frame if nothing has been captured."""
    target = path_for(name)
    if not target.exists():
        return pd.DataFrame()
    frame = pd.read_csv(target)
    if "as_of_date" in frame.columns:
        frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], utc=True)
    return frame


def append(name: str, frame: pd.DataFrame, as_of: dt.date | None = None) -> dict[str, int]:
    """Add today's pull, keeping any row already on record for that key.

    Returns counts so the caller can report what actually landed rather than
    assuming the write did something.
    """
    keys = SERIES[name] if name in SERIES else None
    if keys is None:
        raise ValueError(f"Unknown series: {name}")
    if frame.empty:
        return {"incoming": 0, "added": 0, "already_present": 0, "total": len(read(name))}

    incoming = frame.copy()
    if "as_of_date" not in incoming.columns or as_of is not None:
        incoming["as_of_date"] = pd.Timestamp(as_of or dt.date.today(), tz="UTC")
    incoming["as_of_date"] = pd.to_datetime(incoming["as_of_date"], utc=True)

    missing = [k for k in keys if k not in incoming.columns]
    if missing:
        raise ValueError(f"{name} pull is missing key columns: {missing}")

    existing = read(name)
    if existing.empty:
        combined, added = incoming, len(incoming)
    else:
        combined = pd.concat([existing, incoming], ignore_index=True)
        before = len(existing)
        # keep="first" is what makes this append-only: the row already on
        # record wins, and a re-run of the same day changes nothing.
        combined = combined.drop_duplicates(subset=keys, keep="first")
        added = len(combined) - before

    STORE.mkdir(parents=True, exist_ok=True)
    combined = combined.sort_values(keys).reset_index(drop=True)
    combined.to_csv(path_for(name), index=False)
    return {
        "incoming": len(incoming),
        "added": added,
        "already_present": len(incoming) - added,
        "total": len(combined),
    }


def coverage() -> pd.DataFrame:
    """What the store holds, per series. Reported by the snapshot script."""
    rows = []
    for name in SERIES:
        frame = read(name)
        if frame.empty:
            rows.append({"series": name, "rows": 0, "days": 0, "first": None, "last": None})
            continue
        days = frame["as_of_date"].dt.date
        rows.append({
            "series": name,
            "rows": len(frame),
            "days": days.nunique(),
            "first": str(days.min()),
            "last": str(days.max()),
        })
    return pd.DataFrame(rows)


def merge_rates(live: pd.DataFrame) -> pd.DataFrame:
    """Stored rate history plus today's live pull, stored rows winning.

    The overlap is genuine: CCIR republishes the same 30-day window every day.
    Preferring the stored copy means a later restatement upstream cannot quietly
    rewrite history we already captured.
    """
    stored = read("rates")
    if stored.empty:
        return live
    if live.empty:
        return stored
    combined = pd.concat([stored, live], ignore_index=True)
    combined = combined.drop_duplicates(subset=["as_of_date", "series_id"], keep="first")
    return combined.sort_values(["series_id", "as_of_date"]).reset_index(drop=True)
