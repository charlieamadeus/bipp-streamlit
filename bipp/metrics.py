from __future__ import annotations

from math import prod
from typing import Iterable, Mapping


def validate_weights(weights: Mapping[str, float], expected_keys: Iterable[str]) -> None:
    missing = set(expected_keys) - set(weights)
    extra = set(weights) - set(expected_keys)
    if missing:
        raise ValueError(f"Missing weights: {sorted(missing)}")
    if extra:
        raise ValueError(f"Unexpected weights: {sorted(extra)}")
    if any(value < 0 for value in weights.values()):
        raise ValueError("Weights must be non-negative")
    if abs(sum(weights.values()) - 1.0) > 0.000001:
        raise ValueError("Weights must sum to 1.0")


def weighted_basket(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    validate_weights(weights, values.keys())
    if any(value <= 0 for value in values.values()):
        raise ValueError("Component values must be positive")
    return sum(values[key] * weights[key] for key in values)


def geometric_basket(values: Mapping[str, float]) -> float:
    if not values:
        raise ValueError("At least one value is required")
    if any(value <= 0 for value in values.values()):
        raise ValueError("Geometric basket requires positive values")
    return prod(values.values()) ** (1 / len(values))


def compute_per_btc(btc_usd: float, hardware_basket: float) -> float:
    if btc_usd <= 0:
        raise ValueError("BTC/USD must be positive")
    if hardware_basket <= 0:
        raise ValueError("Hardware basket must be positive")
    return btc_usd / hardware_basket


def normalize_index(values: list[float], base_index: int = 0) -> list[float]:
    if not values:
        raise ValueError("At least one value is required")
    if base_index < 0 or base_index >= len(values):
        raise ValueError("Base index is out of range")
    base = values[base_index]
    if base <= 0:
        raise ValueError("Base value must be positive")
    return [100 * value / base for value in values]

