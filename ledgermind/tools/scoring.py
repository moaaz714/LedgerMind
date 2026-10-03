"""Rule-based risk scoring (FR-007).

Pure function, stdlib only, no I/O. Every threshold and weight comes from `policy`; there
are no numeric literals here, because a bound living in this file would be a defect
(Article IV).

Rule-based rather than trained, for two reasons. There are no labelled lending outcomes to
train on. More importantly, a credit committee has to be able to read the reasoning line by
line -- an unexplainable risk score would fail the use case even if it were more accurate.

The score runs 0-100 where **higher means more risk**. Same inputs always give the same
tier (FR-007): nothing here consults a clock, a random source, or any state.
"""

from __future__ import annotations

from ledgermind import policy


def _flag_points(flags: dict) -> int:
    """Risk points from cashflow flags, each kind capped separately.

    Capping per kind rather than only in total stops one indicator from consuming the whole
    allowance: a merchant with fifteen overdrafts and no other problem should not score the
    same as one with overdrafts, bounced payments and an eroding balance.
    """
    return (
        min(
            flags["overdraft_count"] * policy.OVERDRAFT_RISK_POINTS_EACH,
            policy.OVERDRAFT_RISK_POINTS_MAX,
        )
        + min(
            flags["bounced_payment_count"] * policy.BOUNCED_PAYMENT_RISK_POINTS_EACH,
            policy.BOUNCED_PAYMENT_RISK_POINTS_MAX,
        )
        + (policy.DECLINING_BALANCE_RISK_POINTS if flags["declining_balance"] else 0)
    )


def _drivers(revenue_metrics: dict, volatility: dict, flags: dict) -> list[str]:
    """Plain-language reasons, for the memo to draw on.

    Deliberately free of numbers. The memo may quote any figure it likes -- grounding will
    verify it -- but these strings are the *reasoning*, and "revenue swung by more than a
    third month to month" is what a credit committee needs to read, not a bare 0.38.
    """
    drivers = []
    _, volatility_points = policy.volatility_band(volatility["revenue_cv"])
    if volatility_points >= policy.RISK_WEIGHT_VOLATILITY // 2:
        drivers.append("revenue varies substantially month to month after allowing for the trend")
    elif volatility_points == 0:
        drivers.append("revenue is highly stable month to month")

    if revenue_metrics["trend"] == "declining":
        drivers.append("revenue is declining over the period")
    elif revenue_metrics["trend"] == "rising":
        drivers.append("revenue is growing over the period")

    if flags["overdraft_count"]:
        drivers.append("the account has gone overdrawn during the period")
    if flags["bounced_payment_count"]:
        drivers.append("payments have been returned for insufficient funds")
    if flags["declining_balance"]:
        drivers.append("the account balance has eroded across the period")
    if volatility["insufficient_history"]:
        drivers.append("the file is shorter than the minimum assessable history")
    if not drivers:
        drivers.append("no material risk indicators found")
    return drivers


def score_risk(revenue_metrics: dict, volatility: dict, flags: dict) -> dict:
    """Assign a risk score and tier, or decline.

    `risk_tier` is None when declined: a refused merchant has no tier, and inventing one
    would put a misleading grade in front of an analyst.
    """
    drivers = _drivers(revenue_metrics, volatility, flags)

    if volatility["insufficient_history"]:
        # Too short to assess. Declined before scoring, because the score's history
        # component only spans histories at or above the minimum.
        return {
            "risk_score": policy.DECLINE_RISK_SCORE,
            "risk_score_max": policy.SCORE_SCALE_MAX,
            "decline_threshold": policy.DECLINE_RISK_SCORE,
            "min_months_history": policy.MIN_MONTHS_HISTORY,
            "risk_tier": None,
            "declined": True,
            "decline_reason": (
                f"history of {revenue_metrics['months_covered']} months is below the "
                f"minimum of {policy.MIN_MONTHS_HISTORY}"
            ),
            "drivers": drivers,
        }

    _, volatility_points = policy.volatility_band(volatility["revenue_cv"])
    risk_score = (
        volatility_points
        + policy.trend_risk_points(revenue_metrics["mom_growth_pct"])
        + _flag_points(flags)
        + policy.history_risk_points(revenue_metrics["months_covered"])
    )
    tier = policy.tier_for_score(risk_score)

    return {
        "risk_score": risk_score,
        "risk_score_max": policy.SCORE_SCALE_MAX,
        # The bar this merchant was judged against, returned whether or not they were
        # declined. `decline_reason` names these numbers in prose, and a memo explaining
        # either outcome will naturally cite them -- so they have to be recorded facts, or
        # an honest memo quoting the threshold would be rejected as fabricated. Same
        # principle as FR-009.
        "decline_threshold": policy.DECLINE_RISK_SCORE,
        "min_months_history": policy.MIN_MONTHS_HISTORY,
        "risk_tier": tier,
        "declined": tier is None,
        "decline_reason": (
            None
            if tier is not None
            else f"risk score {risk_score} is at or above the decline threshold of {policy.DECLINE_RISK_SCORE}"
        ),
        "drivers": drivers,
    }
