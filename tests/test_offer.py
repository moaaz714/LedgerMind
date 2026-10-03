"""Offer computation (FR-008), and the policy bounds it must never leave."""

import math

import pytest

from ledgermind import policy
from ledgermind.tools.offer import compute_offer

TIERS = ("A", "B", "C", "D")


def _metrics(average=50_000.0):
    return {"avg_monthly_revenue": average, "mom_growth_pct": 1.0, "months_covered": 15, "trend": "flat"}


def _risk(tier="B", declined=False, reason=None):
    return {
        "risk_score": 30,
        "risk_tier": tier,
        "declined": declined,
        "decline_reason": reason,
        "drivers": [],
    }


# --- terms come from policy, never from here ------------------------------------------


@pytest.mark.parametrize("tier", TIERS)
def test_terms_match_the_policy_table(tier):
    offer = compute_offer(_metrics(), _risk(tier=tier))
    terms = policy.TIER_TERMS[tier]
    assert offer["repayment_pct"] == pytest.approx(terms.repayment_pct * 100)
    assert offer["total_repayable"] == pytest.approx(offer["advance_amount"] * terms.factor_rate, abs=0.01)
    assert offer["policy_version"] == policy.POLICY_VERSION


@pytest.mark.parametrize("tier", TIERS)
def test_every_term_is_inside_its_policy_bound(tier):
    offer = compute_offer(_metrics(), _risk(tier=tier))
    assert 0 < offer["advance_amount"] <= policy.ADVANCE_CAP_ABSOLUTE
    assert offer["advance_amount"] >= policy.MIN_VIABLE_ADVANCE
    assert policy.MIN_DURATION_MONTHS <= offer["expected_duration_months"] <= policy.MAX_DURATION_MONTHS


@pytest.mark.parametrize("tier", TIERS)
def test_advance_rounds_down_never_up(tier):
    """Never lend more than the formula allows."""
    offer = compute_offer(_metrics(average=47_812.34), _risk(tier=tier))
    formula = policy.TIER_TERMS[tier].advance_multiple * 47_812.34
    assert offer["advance_amount"] <= formula
    assert offer["advance_amount"] % policy.ADVANCE_ROUNDING_UNIT == 0


# --- the cap ---------------------------------------------------------------------------


def test_cap_binds_for_a_large_merchant_and_is_recorded():
    huge = policy.ADVANCE_CAP_ABSOLUTE * 10
    offer = compute_offer(_metrics(average=huge), _risk(tier="A"))
    assert offer["advance_amount"] == policy.ADVANCE_CAP_ABSOLUTE
    assert offer["advance_cap_applied"] is True


def test_cap_flag_is_false_when_the_formula_decided():
    offer = compute_offer(_metrics(average=20_000.0), _risk(tier="C"))
    assert offer["advance_cap_applied"] is False


# --- declines --------------------------------------------------------------------------


def test_declined_risk_yields_a_declined_offer_not_an_exception():
    """The agent may call this out of turn; it must get a usable answer."""
    offer = compute_offer(_metrics(), _risk(tier=None, declined=True, reason="risk score 90"))
    assert offer["declined"] is True
    assert offer["decline_reason"] == "risk score 90"
    assert offer["advance_amount"] == 0.0


def test_declined_offer_zeroes_its_fields_rather_than_omitting_them():
    """Zeroed so the fact ledger has something to register and a decline memo has figures
    to cite (FR-021's decline variant). A missing key would look like a model error."""
    offer = compute_offer(_metrics(), _risk(tier=None, declined=True, reason="x"))
    for field in ("advance_amount", "repayment_pct", "expected_duration_months", "total_repayable"):
        assert field in offer
        assert offer[field] == 0


def test_tiny_merchant_is_declined_rather_than_offered_zero():
    """Spec edge case: an advance below the minimum viable amount is an explicit refusal."""
    smallest = policy.MIN_VIABLE_ADVANCE / policy.TIER_TERMS["D"].advance_multiple
    offer = compute_offer(_metrics(average=smallest * 0.5), _risk(tier="D"))
    assert offer["declined"] is True
    assert "minimum viable advance" in offer["decline_reason"]
    assert offer["advance_amount"] == 0.0


def test_merchant_just_above_the_floor_is_offered():
    smallest = policy.MIN_VIABLE_ADVANCE / policy.TIER_TERMS["D"].advance_multiple
    offer = compute_offer(_metrics(average=smallest * 1.5), _risk(tier="D"))
    assert offer["declined"] is False
    assert offer["advance_amount"] >= policy.MIN_VIABLE_ADVANCE


# --- monotonicity: the criterion that tests the lending logic -------------------------


def test_worse_tier_never_gets_a_larger_advance():
    """SC-001, at the tool level.

    Holding revenue fixed, a worse tier must never produce a bigger advance. This holds by
    construction because the policy multiples strictly decrease -- this asserts the
    construction has not been broken.
    """
    advances = [compute_offer(_metrics(), _risk(tier=t))["advance_amount"] for t in TIERS]
    assert advances == sorted(advances, reverse=True), advances


def test_worse_tier_clears_faster():
    durations = [compute_offer(_metrics(), _risk(tier=t))["expected_duration_months"] for t in TIERS]
    assert durations == sorted(durations, reverse=True), durations


def test_worse_tier_costs_more_per_unit_advanced():
    ratios = [
        compute_offer(_metrics(), _risk(tier=t))["total_repayable"]
        / compute_offer(_metrics(), _risk(tier=t))["advance_amount"]
        for t in TIERS
    ]
    assert ratios == sorted(ratios), ratios


# --- duration ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", TIERS)
def test_duration_matches_the_policy_identity(tier):
    """Computed from real figures here; policy derives it from the table alone.

    The two must agree, which is what makes the static check in test_policy meaningful.
    """
    offer = compute_offer(_metrics(), _risk(tier=tier))
    assert offer["expected_duration_months"] == math.ceil(
        policy.TIER_TERMS[tier].implied_duration_months
    )


# --- no literals (Article IV) ----------------------------------------------------------


def test_offer_module_contains_no_decision_literals():
    """Every bound must come from policy; a number here would be a defect.

    Reads the source and asserts the only numeric literals are structural rather than
    decisional: 0 zeroes a declined offer, 2 is decimal places for currency rounding, and
    100 converts a fraction to a percentage. None of those three decide anything -- change
    any of them and no offer changes amount or tier. An advance multiple or a cap would.

    Booleans are excluded: `isinstance(True, int)` is true in Python, so `declined=True`
    would otherwise register as a numeric literal.
    """
    import ast
    import pathlib

    STRUCTURAL = {0, 0.0, 2, 100}

    source = pathlib.Path("ledgermind/tools/offer.py").read_text(encoding="utf-8")
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    assert literals <= STRUCTURAL, f"unexpected numeric literals in offer.py: {literals - STRUCTURAL}"
