"""Policy-bounded offer terms (FR-008).

Pure function, stdlib only, no I/O. Every bound comes from `policy`.

This function's return value *is* the offer the analyst sees. Nothing re-derives it, the
interface does not re-round it, and no number here is ever parsed out of model output --
that is Article I, and it is why the offer is structurally beyond the model's reach rather
than merely forbidden to it.
"""

from __future__ import annotations

import math

from ledgermind import policy


def _declined(reason: str) -> dict:
    """A declined offer, with every numeric field zeroed rather than absent.

    Zeroed rather than omitted so the fact ledger has something to register and a decline
    memo has figures to cite (FR-021's decline variant). A missing key would make a
    grounding failure look like a model error.
    """
    return {
        "advance_amount": 0.0,
        "repayment_pct": 0.0,
        "expected_duration_months": 0,
        "total_repayable": 0.0,
        "advance_cap_applied": False,
        "declined": True,
        "decline_reason": reason,
        "policy_version": policy.POLICY_VERSION,
    }


def compute_offer(revenue_metrics: dict, risk: dict) -> dict:
    """Size the advance, price it, and state how long it should take to clear.

    Returns a declined offer rather than raising when no offer can be made, so the agent
    calling this tool out of turn gets a usable answer instead of an exception it would
    have to interpret.
    """
    if risk["declined"] or risk["risk_tier"] is None:
        return _declined(risk["decline_reason"] or "declined at scoring")

    terms = policy.TIER_TERMS[risk["risk_tier"]]
    average_revenue = revenue_metrics["avg_monthly_revenue"]

    formula_amount = terms.advance_multiple * average_revenue
    capped = min(formula_amount, policy.ADVANCE_CAP_ABSOLUTE)

    # Round DOWN, never to nearest: never lend more than the formula allows.
    advance = math.floor(capped / policy.ADVANCE_ROUNDING_UNIT) * policy.ADVANCE_ROUNDING_UNIT

    if advance < policy.MIN_VIABLE_ADVANCE:
        return _declined(
            f"advance of {advance:,.0f} is below the minimum viable advance of "
            f"{policy.MIN_VIABLE_ADVANCE:,.0f}"
        )

    total_repayable = round(advance * terms.factor_rate, 2)
    monthly_payment = terms.repayment_pct * average_revenue
    duration = math.ceil(total_repayable / monthly_payment)

    return {
        "advance_amount": float(advance),
        "repayment_pct": terms.repayment_pct * 100,
        "expected_duration_months": duration,
        "total_repayable": total_repayable,
        # True when the concentration cap, not the formula, set the amount.
        "advance_cap_applied": formula_amount > policy.ADVANCE_CAP_ABSOLUTE,
        "declined": False,
        "decline_reason": None,
        # Stamped so an offer is always traceable to the rules that produced it.
        "policy_version": policy.POLICY_VERSION,
    }
