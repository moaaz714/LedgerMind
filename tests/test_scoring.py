"""Risk scoring (FR-007)."""

import pytest

from ledgermind import policy
from ledgermind.tools.scoring import score_risk


def _metrics(growth=0.0, months=15, average=50_000.0, trend=None):
    return {
        "avg_monthly_revenue": average,
        "mom_growth_pct": growth,
        "months_covered": months,
        "trend": trend if trend is not None else policy.trend_label(growth),
    }


def _volatility(cv=0.05, insufficient=False):
    stability, _ = policy.volatility_band(cv)
    return {
        "revenue_cv": cv,
        "revenue_stdev": cv * 50_000,
        "stability_score": stability,
        "insufficient_history": insufficient,
    }


def _flags(overdrafts=0, bounced=0, declining=False):
    return {
        "overdraft_count": overdrafts,
        "bounced_payment_count": bounced,
        "large_one_off_count": 0,
        "flag_count": overdrafts + bounced + (1 if declining else 0),
        "min_balance": 1_000.0,
        "large_one_offs": [],
        "declining_balance": declining,
    }


# --- the score is the sum of its documented parts -------------------------------------


def test_perfect_merchant_scores_zero():
    result = score_risk(_metrics(growth=5.0), _volatility(cv=0.05), _flags())
    assert result["risk_score"] == 0
    assert result["risk_tier"] == "A"
    assert result["declined"] is False
    assert result["decline_reason"] is None


def test_score_equals_the_sum_of_its_components():
    """Checked against policy's own lookups, so the test cannot drift from the weights."""
    metrics = _metrics(growth=-4.0, months=10)
    volatility = _volatility(cv=0.42)
    flags = _flags(overdrafts=2, bounced=1, declining=True)

    _, volatility_points = policy.volatility_band(0.42)
    expected = (
        volatility_points
        + policy.trend_risk_points(-4.0)
        + min(2 * policy.OVERDRAFT_RISK_POINTS_EACH, policy.OVERDRAFT_RISK_POINTS_MAX)
        + min(1 * policy.BOUNCED_PAYMENT_RISK_POINTS_EACH, policy.BOUNCED_PAYMENT_RISK_POINTS_MAX)
        + policy.DECLINING_BALANCE_RISK_POINTS
        + policy.history_risk_points(10)
    )
    assert score_risk(metrics, volatility, flags)["risk_score"] == expected


def test_worst_case_reaches_one_hundred():
    result = score_risk(
        _metrics(growth=-20.0, months=policy.MIN_MONTHS_HISTORY),
        _volatility(cv=0.9),
        _flags(overdrafts=10, bounced=10, declining=True),
    )
    assert result["risk_score"] == 100
    assert result["declined"] is True


def test_flag_points_are_capped_per_kind():
    """One indicator must not consume the whole flag allowance.

    Measured as a difference from the no-flag baseline, because the baseline merchant here
    is flat rather than growing and so already carries trend points.
    """
    baseline = score_risk(_metrics(), _volatility(), _flags())["risk_score"]
    many = score_risk(_metrics(), _volatility(), _flags(overdrafts=50))["risk_score"]
    few = score_risk(_metrics(), _volatility(), _flags(overdrafts=3))["risk_score"]
    assert many == few
    assert many - baseline == policy.OVERDRAFT_RISK_POINTS_MAX


# --- determinism (FR-007) -------------------------------------------------------------


def test_same_inputs_always_give_the_same_tier():
    args = (_metrics(growth=-3.0, months=14), _volatility(cv=0.3), _flags(overdrafts=1))
    results = [score_risk(*args) for _ in range(20)]
    assert len({r["risk_tier"] for r in results}) == 1
    assert len({r["risk_score"] for r in results}) == 1


# --- declines -------------------------------------------------------------------------


def test_thin_file_is_declined_before_scoring():
    result = score_risk(
        _metrics(months=policy.MIN_MONTHS_HISTORY - 1),
        _volatility(insufficient=True),
        _flags(),
    )
    assert result["declined"] is True
    assert result["risk_tier"] is None
    assert "below the minimum" in result["decline_reason"]
    assert str(policy.MIN_MONTHS_HISTORY) in result["decline_reason"]


def test_high_score_is_declined_with_the_threshold_named():
    result = score_risk(
        _metrics(growth=-15.0), _volatility(cv=0.8), _flags(overdrafts=5, bounced=3, declining=True)
    )
    assert result["declined"] is True
    assert result["risk_tier"] is None
    assert str(policy.DECLINE_RISK_SCORE) in result["decline_reason"]


def test_declined_merchants_have_no_tier():
    """Inventing a tier for a refused merchant would put a misleading grade in front of
    an analyst."""
    result = score_risk(_metrics(growth=-20.0), _volatility(cv=0.9), _flags(overdrafts=9, bounced=9, declining=True))
    assert result["risk_tier"] is None


def test_every_tier_boundary_is_reachable():
    for upper, tier in policy.TIER_BANDS[:-1]:
        assert policy.tier_for_score(upper) == tier


# --- drivers --------------------------------------------------------------------------


def test_drivers_are_plain_language_and_carry_no_numbers():
    """Drivers are the reasoning, not the figures.

    The memo may quote any number it likes -- grounding verifies it -- but these strings
    are what a credit committee reads, and a bare 0.38 is not an argument.
    """
    result = score_risk(
        _metrics(growth=-5.0), _volatility(cv=0.45), _flags(overdrafts=2, bounced=1, declining=True)
    )
    assert result["drivers"]
    for driver in result["drivers"]:
        assert not any(character.isdigit() for character in driver), driver


def test_clean_merchant_still_gets_a_driver():
    result = score_risk(_metrics(growth=0.5), _volatility(cv=0.15), _flags())
    assert result["drivers"] != []


def test_drivers_name_the_problems_that_are_present():
    result = score_risk(_metrics(growth=-5.0), _volatility(cv=0.45), _flags(overdrafts=1, bounced=1, declining=True))
    joined = " ".join(result["drivers"])
    assert "overdrawn" in joined
    assert "returned" in joined
    assert "eroded" in joined
    assert "declining" in joined


# --- against the real merchant set ----------------------------------------------------


def test_real_merchants_span_the_tiers(merchants):
    """If the set does not reach every tier, whole branches of the offer logic go untested."""
    from ledgermind.tools.flags import detect_cashflow_flags
    from ledgermind.tools.revenue import compute_revenue_metrics
    from ledgermind.tools.volatility import compute_volatility

    seen = set()
    for data in merchants.values():
        transactions = data["inputs"]["transactions"]
        metrics = compute_revenue_metrics(transactions)
        volatility = compute_volatility(metrics["monthly_revenue"])
        flags = detect_cashflow_flags(transactions)
        seen.add(score_risk(metrics, volatility, flags)["risk_tier"])

    assert {"A", "B", "C", "D"} <= seen, f"tiers missing from the merchant set: {seen}"
    assert None in seen, "no merchant in the set is declined"


# --- reconciliation (T045, FR-035) ----------------------------------------------------


def _reconciliation(reconciled=True, ratio=1.0):
    return {
        "sales_total": 100_000.0,
        "expected_banked_total": 97_500.0,
        "banked_total": 97_500.0 * ratio,
        "reconciliation_ratio": ratio,
        "reconciliation_ratio_pct": round(ratio * 100, 4),
        "period_ratio": ratio,
        "months_compared": 15,
        "mismatched_month_count": 0 if reconciled else 15,
        "max_month_gap_pct": abs(ratio - 1) * 100,
        "reconciliation_tolerance_pct": policy.RECONCILIATION_TOLERANCE_PCT,
        "reconciled": reconciled,
    }


def test_a_non_reconciling_merchant_is_declined():
    result = score_risk(_metrics(), _volatility(), _flags(), _reconciliation(reconciled=False, ratio=0.74))
    assert result["declined"] is True
    assert result["risk_tier"] is None
    assert "74.0%" in result["decline_reason"]
    assert f"{policy.RECONCILIATION_TOLERANCE_PCT:.1f}%" in result["decline_reason"]


def test_a_reconciling_merchant_is_unaffected():
    """FR-035: reconciliation adds no risk points.

    The same merchant scored with and without a passing reconciliation must land on the
    same score and tier -- otherwise the declared weights no longer describe the score.
    """
    without = score_risk(_metrics(growth=-3.0), _volatility(cv=0.3), _flags(overdrafts=1))
    with_recon = score_risk(
        _metrics(growth=-3.0), _volatility(cv=0.3), _flags(overdrafts=1), _reconciliation()
    )
    assert without["risk_score"] == with_recon["risk_score"]
    assert without["risk_tier"] == with_recon["risk_tier"]


def test_reconciliation_adds_no_points_even_when_it_fails():
    """A failing reconciliation declines outright; it must not also inflate the score.

    The score it reports is the decline threshold, the same marker a thin file produces --
    not a number derived from the mismatch.
    """
    failing = score_risk(_metrics(), _volatility(), _flags(), _reconciliation(reconciled=False))
    assert failing["risk_score"] == policy.DECLINE_RISK_SCORE


def test_history_is_checked_before_reconciliation():
    """FR-035: a file too short to assess is declined for being too short.

    Reconciliation over a handful of months is unreliable, so the more fundamental problem
    must be the one reported.
    """
    result = score_risk(
        _metrics(months=policy.MIN_MONTHS_HISTORY - 1),
        _volatility(insufficient=True),
        _flags(),
        _reconciliation(reconciled=False),
    )
    assert "below the minimum" in result["decline_reason"]
    assert "reconciles" not in result["decline_reason"]


def test_a_failing_reconciliation_appears_in_the_drivers():
    result = score_risk(_metrics(), _volatility(), _flags(), _reconciliation(reconciled=False))
    assert any("sales records" in driver for driver in result["drivers"])


def test_omitting_reconciliation_leaves_scoring_unchanged():
    """The argument is optional so the three-argument call sites still work."""
    result = score_risk(_metrics(), _volatility(), _flags())
    assert result["declined"] is False
