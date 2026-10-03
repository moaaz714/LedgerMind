"""Offer bound checks (FR-022).

A breach here is **a defect in the offer logic, not prose to regenerate.** That distinction
is the whole reason this is separate from grounding: if `compute_offer` produced terms
outside policy, asking the model to rewrite the memo would be treating a broken calculation
as a wording problem. The offer is withheld and the condition is surfaced.

In practice this should never fire -- `compute_offer` reads the same constants -- which is
exactly why it is worth having. It is the check that catches a future edit to the offer
logic that drifts out of bounds, and `test_offer` cannot catch that because it would drift
with it.
"""

from __future__ import annotations

from ledgermind import policy as rules


def check_offer_policy(offer: dict, risk: dict) -> tuple[str, ...]:
    """Every way this offer leaves its declared bounds. Empty means compliant."""
    breaches: list[str] = []

    if offer.get("policy_version") != rules.POLICY_VERSION:
        breaches.append(
            f"offer stamped policy version {offer.get('policy_version')!r}, "
            f"expected {rules.POLICY_VERSION!r}"
        )

    if offer.get("declined"):
        # A declined offer must be zeroed, not merely flagged: a non-zero advance beside a
        # decline is the kind of contradiction an interface could render as a live offer.
        for field in ("advance_amount", "repayment_pct", "expected_duration_months", "total_repayable"):
            if offer.get(field):
                breaches.append(f"declined offer carries a non-zero {field}: {offer[field]}")
        if not offer.get("decline_reason"):
            breaches.append("declined offer carries no decline_reason")
        return tuple(breaches)

    tier = risk.get("risk_tier")
    if tier not in rules.TIER_TERMS:
        breaches.append(f"offer made against unknown tier {tier!r}")
        return tuple(breaches)

    terms = rules.TIER_TERMS[tier]
    advance = offer["advance_amount"]

    if advance < rules.MIN_VIABLE_ADVANCE:
        breaches.append(
            f"advance {advance:,.2f} is below the minimum viable advance "
            f"{rules.MIN_VIABLE_ADVANCE:,.2f}"
        )
    if advance > rules.ADVANCE_CAP_ABSOLUTE:
        breaches.append(
            f"advance {advance:,.2f} exceeds the cap {rules.ADVANCE_CAP_ABSOLUTE:,.2f}"
        )
    if advance % rules.ADVANCE_ROUNDING_UNIT != 0:
        breaches.append(
            f"advance {advance:,.2f} is not a multiple of {rules.ADVANCE_ROUNDING_UNIT}"
        )

    expected_pct = terms.repayment_pct * 100
    if abs(offer["repayment_pct"] - expected_pct) > 1e-9:
        breaches.append(
            f"repayment {offer['repayment_pct']:.2f}% is outside tier {tier}'s band "
            f"({expected_pct:.2f}%)"
        )

    duration = offer["expected_duration_months"]
    if not rules.MIN_DURATION_MONTHS <= duration <= rules.MAX_DURATION_MONTHS:
        breaches.append(
            f"duration {duration} months is outside "
            f"{rules.MIN_DURATION_MONTHS}-{rules.MAX_DURATION_MONTHS}"
        )

    expected_total = advance * terms.factor_rate
    if abs(offer["total_repayable"] - expected_total) > 0.01:
        breaches.append(
            f"total repayable {offer['total_repayable']:,.2f} does not match "
            f"advance x factor {expected_total:,.2f}"
        )

    if offer["advance_cap_applied"] and advance != rules.ADVANCE_CAP_ABSOLUTE:
        breaches.append(
            f"offer claims the cap was applied but the advance is {advance:,.2f}, "
            f"not {rules.ADVANCE_CAP_ABSOLUTE:,.2f}"
        )

    return tuple(breaches)
