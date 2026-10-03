"""The trend fit, shared by the revenue and volatility tools.

Two tools need it: `compute_revenue_metrics` reports the growth rate, and
`compute_volatility` subtracts the trend line before measuring variation (FR-005). Shared
here rather than duplicated, so they cannot disagree about where the line sits.

Not shared with `ledgermind.data.gen`, which carries its own implementation of the same
formula on purpose. The generator computes from the series it *constructed*; these tools
compute from a series *recovered from a CSV*. Same definition, independently obtained
inputs -- which is what makes agreement between them evidence of anything. Importing the
generator's version would also point the decision path at generation code, which is a
boundary worth keeping.
"""

from __future__ import annotations

import math
import statistics


def log_linear_growth_pct(series: list[float]) -> float:
    """Average geometric growth per month, by least squares on log revenue (FR-004).

    Months at or below zero carry no logarithm and are excluded from the fit. They are
    still counted everywhere else -- in the mean, and in the residuals measured against
    this line -- so a dead month lowers the average without destroying the trend estimate.

    Returns 0.0 when fewer than two positive months remain, since a single point defines
    no slope.
    """
    points = [(index, math.log(value)) for index, value in enumerate(series) if value > 0]
    if len(points) < 2:
        return 0.0

    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    return (math.exp(slope) - 1) * 100


def residuals(series: list[float]) -> list[float]:
    """Each month's distance from the fitted trend line.

    The line is anchored at the mean detrended level, so the residuals sum to approximately
    zero and measure unpredictability rather than drift.
    """
    if not series:
        return []
    growth = 1 + log_linear_growth_pct(series) / 100
    anchor = statistics.fmean(value / growth**index for index, value in enumerate(series))
    return [value - anchor * growth**index for index, value in enumerate(series)]
