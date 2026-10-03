"""Revenue stability (FR-005), including the edge cases that break naive implementations."""

import pytest

from ledgermind import policy
from ledgermind.tools.volatility import compute_volatility


def _series(amounts, start_month=1, year=2026):
    months = []
    month, y = start_month, year
    for amount in amounts:
        months.append((f"{y:04d}-{month:02d}", float(amount)))
        month += 1
        if month == 13:
            y, month = y + 1, 1
    return months


# --- hand-computed arithmetic ---------------------------------------------------------


def test_hand_computed_coefficient_of_variation():
    """18k, 22k, 22k, 18k -- worked out on paper.

    The series is symmetric, so the fitted trend is flat and the residuals are exactly
    -2000, +2000, +2000, -2000 against a mean of 20,000. Sample standard deviation is
    sqrt(4 * 2000^2 / 3) = 2309.401, giving a coefficient of variation of 0.115470.

    Symmetry matters: with 18k, 22k, 18k, 22k the series ends high, the fit reads an upward
    slope, and the residuals are no longer the simple deviations from the mean.
    """
    result = compute_volatility(_series([18_000, 22_000, 22_000, 18_000]))
    assert result["revenue_stdev"] == pytest.approx(2309.40, abs=0.01)
    assert result["revenue_cv"] == pytest.approx(0.115470, abs=1e-6)
    assert result["stability_score"] == 80


def test_smooth_growth_is_not_volatility():
    """The entire reason for detrending.

    A business growing at a flawless 4% a month has no month-to-month uncertainty, so its
    volatility must be zero. On the raw series it would be 0.106 and score risk points.
    """
    result = compute_volatility(_series([10_000 * 1.04**i for i in range(15)]))
    assert result["revenue_cv"] == pytest.approx(0.0, abs=1e-9)
    assert result["stability_score"] == 95


def test_smooth_decline_is_not_volatility():
    """A smooth 12% monthly decline would otherwise take the maximum volatility penalty on
    top of the 25 points the trend component already charges it."""
    result = compute_volatility(_series([50_000 * 0.88**i for i in range(14)]))
    assert result["revenue_cv"] == pytest.approx(0.0, abs=1e-9)


# --- edge cases the spec calls out ----------------------------------------------------


def test_zero_revenue_month_stays_finite():
    """Must not divide by zero or return infinity (spec edge case)."""
    result = compute_volatility(_series([20_000, 20_000, 0, 20_000, 20_000, 20_000]))
    assert result["revenue_cv"] > 0
    assert result["revenue_cv"] < 10
    assert result["revenue_stdev"] == pytest.approx(result["revenue_stdev"])  # finite


def test_all_zero_revenue_does_not_explode():
    result = compute_volatility(_series([0, 0, 0, 0, 0, 0]))
    assert result["revenue_cv"] == 0.0
    assert result["revenue_stdev"] == 0.0


def test_single_month_has_no_variation():
    result = compute_volatility(_series([20_000]))
    assert result["revenue_cv"] == 0.0
    assert result["insufficient_history"] is True


def test_thin_file_is_flagged_not_computed_away():
    """A short file must say so rather than quietly report a stability figure."""
    short = compute_volatility(_series([20_000] * (policy.MIN_MONTHS_HISTORY - 1)))
    assert short["insufficient_history"] is True

    long_enough = compute_volatility(_series([20_000] * policy.MIN_MONTHS_HISTORY))
    assert long_enough["insufficient_history"] is False


def test_empty_input_is_refused():
    with pytest.raises(ValueError, match="at least one month"):
        compute_volatility([])


# --- band boundaries ------------------------------------------------------------------


def test_stability_score_comes_from_the_policy_bands():
    """Not recomputed here: the score must be whatever policy says for that CV."""
    result = compute_volatility(_series([18_000, 22_000, 22_000, 18_000]))
    expected, _ = policy.volatility_band(result["revenue_cv"])
    assert result["stability_score"] == expected


# --- recovery from a real CSV, against ground truth -----------------------------------


@pytest.mark.parametrize(
    "merchant_id",
    [
        "m02_healthy_mid",
        "m06_healthy_pairlow",
        "m10_volatile_pairhigh2",
        "m14_declining_distress",
        "m09_volatile_badmonth",
    ],
)
def test_matches_ground_truth(merchants, merchant_id):
    from ledgermind.tools.revenue import compute_revenue_metrics

    data = merchants[merchant_id]
    metrics = compute_revenue_metrics(data["inputs"]["transactions"])
    result = compute_volatility(metrics["monthly_revenue"])
    assert result["revenue_cv"] == pytest.approx(data["truth"]["true_revenue_cv"], abs=1e-4)
