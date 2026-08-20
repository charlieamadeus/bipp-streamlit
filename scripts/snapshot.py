"""Capture today's CCIR pull into our own history, then report what landed.

Run daily and commit the result:

    py -3 scripts/snapshot.py
    git add data/history && git commit -m "snapshot $(date +%F)"

CCIR retains about 30 days of rental history and publishes residuals, token
prices and debt with no history at all. Every day this does not run is a day
that eventually cannot be recovered.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bipp import ccir, ccir_pages, store  # noqa: E402

RATE_COLUMNS = [
    "series_id", "as_of_date", "price_headline", "price_median", "promotion_status",
    "gpu_model", "segment", "operator_tier", "form_factor", "interruptibility",
    "region", "commitment_term", "product_class", "n_sources", "confidence_level",
    "price_p25", "price_p75",
]


def main() -> int:
    results: dict[str, dict[str, int]] = {}
    failures: list[str] = []

    try:
        panel = ccir.load_panel(ccir.fetch_catalog(), ccir.fetch_history())
        columns = [c for c in RATE_COLUMNS if c in panel.columns]
        results["rates"] = store.append("rates", panel[columns])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"rates: {exc}")

    for name, fetch in [("hardware", ccir_pages.fetch_hardware),
                        ("tokens", ccir_pages.fetch_tokens),
                        ("credit", ccir_pages.fetch_credit)]:
        try:
            frame = fetch()
            if frame.empty:
                failures.append(f"{name}: parsed to an empty frame, page layout may have changed")
                continue
            results[name] = store.append(name, frame)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")

    for name, counts in results.items():
        print(f"{name:9} +{counts['added']:>6} new  ({counts['already_present']} already on record)"
              f"  -> {counts['total']:,} rows")

    print()
    print(store.coverage().to_string(index=False))

    if failures:
        print()
        for failure in failures:
            print(f"FAILED  {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
