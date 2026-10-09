"""Cross-sectional preprocessing for approved DEV price metrics only."""

from __future__ import annotations

import math
from typing import Any, Iterable


WINSOR_LOWER_PERCENTILE = 0.01
WINSOR_UPPER_PERCENTILE = 0.99
MINIMUM_CROSS_SECTION = 50


def linear_quantile(values: Iterable[float], probability: float) -> float:
    """Compute a deterministic linear-interpolation quantile for finite values."""

    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        raise ValueError("A quantile requires at least one finite value")
    if not 0 <= probability <= 1:
        raise ValueError("Quantile probability must be between zero and one")
    if len(ordered) == 1:
        return ordered[0]
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def preprocess_cross_section(rows: list[dict[str, Any]], direction: int) -> list[dict[str, Any]]:
    """Winsorize, z-score, and direction-normalize one date/metric cross-section.

    Input rows must all have a numeric raw value and be eligible for factor
    input. This function neither pools dates nor substitutes missing values.
    """

    if direction not in {-1, 1}:
        raise ValueError("Direction must be either -1 or +1")
    values = [float(row["raw_value"]) for row in rows]
    count = len(values)
    if count < MINIMUM_CROSS_SECTION:
        return [
            {
                **row,
                "cross_section_count": count,
                "winsor_lower": None,
                "winsor_upper": None,
                "winsorized_value": None,
                "z_score": None,
                "direction": direction,
                "direction_normalized_score": None,
                "preprocessing_available": False,
                "preprocessing_reason": "insufficient_cross_section_lt_50",
            }
            for row in rows
        ]
    lower = linear_quantile(values, WINSOR_LOWER_PERCENTILE)
    upper = linear_quantile(values, WINSOR_UPPER_PERCENTILE)
    winsorized = [min(max(value, lower), upper) for value in values]
    mean = sum(winsorized) / count
    variance = sum((value - mean) ** 2 for value in winsorized) / count
    standard_deviation = math.sqrt(variance)
    if not math.isfinite(standard_deviation) or standard_deviation == 0:
        return [
            {
                **row,
                "cross_section_count": count,
                "winsor_lower": lower,
                "winsor_upper": upper,
                "winsorized_value": min(max(float(row["raw_value"]), lower), upper),
                "z_score": None,
                "direction": direction,
                "direction_normalized_score": None,
                "preprocessing_available": False,
                "preprocessing_reason": "zero_cross_section_standard_deviation",
            }
            for row in rows
        ]
    result: list[dict[str, Any]] = []
    for row, winsorized_value in zip(rows, winsorized):
        z_score = (winsorized_value - mean) / standard_deviation
        result.append(
            {
                **row,
                "cross_section_count": count,
                "winsor_lower": lower,
                "winsor_upper": upper,
                "winsorized_value": winsorized_value,
                "z_score": z_score,
                "direction": direction,
                "direction_normalized_score": direction * z_score,
                "preprocessing_available": True,
                "preprocessing_reason": "",
            }
        )
    return result

