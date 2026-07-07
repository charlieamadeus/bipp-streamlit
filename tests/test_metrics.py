from __future__ import annotations

import math
import unittest

from bipp.metrics import compute_per_btc, geometric_basket, normalize_index, validate_weights, weighted_basket


class MetricsTests(unittest.TestCase):
    def test_weighted_basket(self) -> None:
        value = weighted_basket(
            {"h100": 2.0, "h200": 3.0, "b200": 5.0},
            {"h100": 0.5, "h200": 0.3, "b200": 0.2},
        )
        self.assertAlmostEqual(value, 2.9)

    def test_validate_weights_rejects_bad_sum(self) -> None:
        with self.assertRaises(ValueError):
            validate_weights({"h100": 0.5, "h200": 0.5, "b200": 0.5}, ["h100", "h200", "b200"])

    def test_compute_per_btc(self) -> None:
        self.assertAlmostEqual(compute_per_btc(70000, 2.8), 25000)

    def test_normalize_index(self) -> None:
        self.assertEqual(normalize_index([20, 25, 10]), [100, 125, 50])

    def test_geometric_basket(self) -> None:
        self.assertTrue(math.isclose(geometric_basket({"a": 2.0, "b": 8.0}), 4.0))


if __name__ == "__main__":
    unittest.main()

