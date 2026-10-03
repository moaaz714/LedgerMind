"""Revenue stability from the monthly revenue series (FR-005).

Measured **after the trend is removed**. On the raw series this would conflate trend with
unpredictability: a perfectly smooth 2%-per-month growth line has a raw coefficient of
variation of 0.106 and a smooth 12% monthly decline reaches 0.52, so a business with no
month-to-month uncertainty at all would score as volatile, and a declining business would
be penalised once by the trend measure and again by this one. Detrending keeps the two
independent, which is what their separate risk weights claim.

Pure function, stdlib only, no I/O (Article IV).
"""

from __future__ import annotations

import statistics

from ledgermind import policy
from ledgermind.tools import trend


def compute_volatility(monthly_revenue: list[tuple[str, float]]) -> dict:
    """Residual variation in monthly revenue, and the stability score it implies.

    Takes the series produced by `compute_revenue_metrics`, not raw transactions, so the
    month-grouping decision is made in exactly one place.
    """
    if not monthly_revenue:
        raise ValueError("compute_volatility requires at least one month")

    series = [amount for _, amount in monthly_revenue]
    months = len(series)
    insufficient = months < policy.MIN_MONTHS_HISTORY
    mean = statistics.fmean(series)

    if months < 2 or mean == 0:
        # One month defines no variation, and a merchant who banked nothing has no
        # meaningful ratio. Zero is honest here; `insufficient_history` carries the warning.
        stability, _ = policy.volatility_band(0.0)
        return {
            "revenue_cv": 0.0,
            "revenue_stdev": 0.0,
            "stability_score": stability,
            "stability_score_max": policy.SCORE_SCALE_MAX,
            "insufficient_history": insufficient,
        }

    residuals = trend.residuals(series)
    stdev = statistics.stdev(residuals)
    cv = stdev / mean
    stability, _ = policy.volatility_band(cv)

    return {
        "revenue_cv": round(cv, 6),
        "revenue_stdev": round(stdev, 2),
        "stability_score": stability,
        "stability_score_max": policy.SCORE_SCALE_MAX,
        "insufficient_history": insufficient,
    }
