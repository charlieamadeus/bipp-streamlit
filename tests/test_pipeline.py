from __future__ import annotations

import unittest

import pandas as pd

from bipp.pipeline import filter_date_range, trailing_window


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5, freq="D"),
            "btc_usd": [100, 110, 120, 130, 140],
            "h100": [2.0, 2.1, 2.2, 2.3, 2.4],
            "h200": [3.0, 3.1, 3.2, 3.3, 3.4],
            "b200": [5.0, 5.1, 5.2, 5.3, 5.4],
        }
    )


class PipelineTests(unittest.TestCase):
    def test_trailing_window_is_inclusive(self) -> None:
        df = trailing_window(sample_frame(), 3)
        self.assertEqual(df["date"].dt.date.astype(str).tolist(), ["2026-01-03", "2026-01-04", "2026-01-05"])

    def test_filter_date_range_is_inclusive(self) -> None:
        df = filter_date_range(sample_frame(), "2026-01-02", "2026-01-04")
        self.assertEqual(df["date"].dt.date.astype(str).tolist(), ["2026-01-02", "2026-01-03", "2026-01-04"])

    def test_filter_date_range_rejects_reversed_dates(self) -> None:
        with self.assertRaises(ValueError):
            filter_date_range(sample_frame(), "2026-01-04", "2026-01-02")


if __name__ == "__main__":
    unittest.main()
