"""Capture today's CCIR and Ornn pulls into our own history, then report.

Run daily and commit the result:

    py -3 scripts/snapshot.py
    git add data/history && git commit -m "snapshot $(date +%F)"

CCIR retains about 30 days of rental history and publishes residuals, token
prices and debt with no history at all. Ornn's public compute index keeps a
trailing three months. Every day this does not run is a day that eventually
cannot be recovered.

DGX Spark (Amazon Buy Box) is a soft channel: its failures never set a non-zero
exit. Hard failures (rates, ornn, hardware, tokens, credit) still return 1.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bipp import ccir, ccir_pages, dgx_spark, store  # noqa: E402
from bipp.pipeline import fetch_ornn_panel  # noqa: E402

RATE_COLUMNS = [
    "series_id", "as_of_date", "price_headline", "price_median", "promotion_status",
    "gpu_model", "segment", "operator_tier", "form_factor", "interruptibility",
    "region", "commitment_term", "product_class", "n_sources", "confidence_level",
    "price_p25", "price_p75",
]
ORNN_COLUMNS = ["as_of_date", "h100", "h200", "b200"]
DGX_COLUMNS = ["as_of_date", "asin", "price_usd", "available", "seller", "ships_from"]


def main() -> int:
    results: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    soft_failures: list[str] = []

    try:
        panel = ccir.load_panel(ccir.fetch_catalog(), ccir.fetch_history())
        columns = [c for c in RATE_COLUMNS if c in panel.columns]
        results["rates"] = store.append("rates", panel[columns])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"rates: {exc}")

    try:
        panel = fetch_ornn_panel()
        results["ornn"] = store.append("ornn", panel[ORNN_COLUMNS])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"ornn: {exc}")

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

    try:
        spark = dgx_spark.snapshot_today()
        columns = [c for c in DGX_COLUMNS if c in spark.columns]
        results["dgx_spark"] = store.append("dgx_spark", spark[columns])
    except Exception as exc:  # noqa: BLE001
        soft_failures.append(f"dgx_spark: {exc}")

    for name, counts in results.items():
        print(f"{name:9} +{counts['added']:>6} new  ({counts['already_present']} already on record)"
              f"  -> {counts['total']:,} rows")

    print()
    print(store.coverage().to_string(index=False))

    spark_store = store.read("dgx_spark")
    if dgx_spark.is_blocked(spark_store):
        print()
        print("dgx_spark: blocked")
        print(
            f"  no available=true row within {dgx_spark.BLOCKED_GAP_DAYS} days "
            f"(last={dgx_spark.last_available_date(spark_store)}; "
            "use a desktop session: py -3 scripts/snapshot.py, then commit "
            "data/history/dgx_spark.csv)"
        )

    if soft_failures:
        print()
        for failure in soft_failures:
            print(f"SOFT  {failure}")

    if failures:
        print()
        for failure in failures:
            print(f"FAILED  {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
