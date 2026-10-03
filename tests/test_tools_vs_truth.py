"""Every tool against every merchant's ground truth (T016, SC-005).

This is the test the whole of Day 1 exists to make possible. Each tool reads a real CSV and
must recover the figures the generator recorded while writing it -- two independent paths to
the same numbers, meeting in the middle.

It is also the last line of defence before the model is introduced on Day 2. From here on,
when an agent run produces a strange decision, the arithmetic underneath is already known
to be correct and the question is about the loop, not the maths.
"""

import pytest

from ledgermind import policy
from ledgermind.data import gen
from ledgermind.tools.flags import detect_cashflow_flags
from ledgermind.tools.offer import compute_offer
from ledgermind.tools.registry import TOOLS, get, schemas
from ledgermind.tools.revenue import compute_revenue_metrics
from ledgermind.tools.scoring import score_risk
from ledgermind.tools.volatility import compute_volatility

MERCHANT_IDS = [spec.merchant_id for spec in gen.MERCHANT_SPECS]


def _run_pipeline(transactions):
    """Every analysis, in dependency order, exactly as the dispatcher will call them."""
    metrics = compute_revenue_metrics(transactions)
    volatility = compute_volatility(metrics["monthly_revenue"])
    flags = detect_cashflow_flags(transactions)
    risk = score_risk(metrics, volatility, flags)
    offer = compute_offer(metrics, risk)
    return metrics, volatility, flags, risk, offer


@pytest.fixture(scope="module")
def results(merchants):
    return {
        merchant_id: _run_pipeline(data["inputs"]["transactions"])
        for merchant_id, data in merchants.items()
    }


# --- SC-005: every tool agrees with recorded truth ------------------------------------


@pytest.mark.parametrize("merchant_id", MERCHANT_IDS)
def test_all_tools_match_ground_truth(merchants, results, merchant_id):
    truth = merchants[merchant_id]["truth"]
    metrics, volatility, flags, _, _ = results[merchant_id]

    assert metrics["months_covered"] == truth["true_months_covered"]
    assert metrics["avg_monthly_revenue"] == pytest.approx(truth["true_avg_monthly_revenue"], abs=0.02)
    assert metrics["total_revenue"] == pytest.approx(truth["true_total_revenue"], abs=0.05)
    assert metrics["mom_growth_pct"] == pytest.approx(truth["true_mom_growth_pct"], abs=0.01)
    assert volatility["revenue_cv"] == pytest.approx(truth["true_revenue_cv"], abs=1e-4)
    assert flags["overdraft_count"] == truth["true_overdraft_count"]
    assert flags["bounced_payment_count"] == truth["true_bounced_payment_count"]
    assert flags["large_one_off_count"] == truth["true_large_one_off_count"]

    recorded = dict(truth["true_monthly_revenue"])
    for month, amount in metrics["monthly_revenue"]:
        assert amount == pytest.approx(recorded[month], abs=0.02), f"{merchant_id} {month}"


# --- SC-002, SC-003: every offer inside policy ----------------------------------------


@pytest.mark.parametrize("merchant_id", MERCHANT_IDS)
def test_no_offer_breaches_policy(results, merchant_id):
    _, _, _, risk, offer = results[merchant_id]
    if offer["declined"]:
        assert offer["advance_amount"] == 0.0
        assert offer["decline_reason"]
        return

    terms = policy.TIER_TERMS[risk["risk_tier"]]
    assert policy.MIN_VIABLE_ADVANCE <= offer["advance_amount"] <= policy.ADVANCE_CAP_ABSOLUTE
    assert offer["repayment_pct"] == pytest.approx(terms.repayment_pct * 100)
    assert policy.MIN_DURATION_MONTHS <= offer["expected_duration_months"] <= policy.MAX_DURATION_MONTHS
    assert offer["policy_version"] == policy.POLICY_VERSION


# --- SC-001: monotonicity, the measure that tests the lending logic -------------------


@pytest.mark.parametrize("pair", gen.MONOTONICITY_PAIRS, ids=lambda p: f"{p[0]}_vs_{p[1]}")
def test_more_volatility_never_buys_a_larger_advance(merchants, results, pair):
    """SC-001. These pairs share a base revenue and trend and differ only in volatility.

    This can fail while every other measure passes, which makes it the one that tests
    whether the lending logic is sane rather than whether the plumbing works.
    """
    steady_id, erratic_id = pair
    steady_vol, erratic_vol = (
        results[steady_id][1]["revenue_cv"],
        results[erratic_id][1]["revenue_cv"],
    )
    assert erratic_vol > steady_vol, "the pair must actually differ in volatility"

    steady_rev = merchants[steady_id]["truth"]["true_avg_monthly_revenue"]
    erratic_rev = merchants[erratic_id]["truth"]["true_avg_monthly_revenue"]
    assert steady_rev == pytest.approx(erratic_rev, rel=0.02), "revenue must be held constant"

    assert results[erratic_id][4]["advance_amount"] <= results[steady_id][4]["advance_amount"]


# --- SC-004: determinism --------------------------------------------------------------


@pytest.mark.parametrize("merchant_id", ["m02_healthy_mid", "m13_declining_weak"])
def test_repeated_runs_are_identical(merchants, merchant_id):
    """SC-004 for the deterministic layer. The model's turn comes on Day 2."""
    transactions = merchants[merchant_id]["inputs"]["transactions"]
    runs = [_run_pipeline(transactions) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


# --- coverage: the set must exercise every branch -------------------------------------


def test_the_set_exercises_every_branch(results):
    tiers = {risk["risk_tier"] for _, _, _, risk, _ in results.values()}
    assert {"A", "B", "C", "D"} <= tiers, f"tiers reached: {tiers}"
    assert None in tiers, "no declined merchant"

    assert any(offer["advance_cap_applied"] for *_, offer in results.values()), "cap never engaged"
    assert any(offer["declined"] for *_, offer in results.values())
    assert any(volatility["insufficient_history"] for _, volatility, *_ in results.values())
    assert any(flags["overdraft_count"] for _, _, flags, _, _ in results.values())
    assert any(flags["bounced_payment_count"] for _, _, flags, _, _ in results.values())
    assert any(flags["large_one_off_count"] for _, _, flags, _, _ in results.values())
    assert any(flags["declining_balance"] for _, _, flags, _, _ in results.values())


# --- the registry (T014) --------------------------------------------------------------


def test_registry_lists_every_tool_exactly_once():
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names)) == 5


def test_registry_declares_the_fields_each_tool_actually_returns(merchants):
    """The registry's `returns` list is the fact ledger's registration contract.

    If a tool returns a field the registry does not declare, the dispatcher will not
    register it, and a memo quoting that figure would fail grounding for no good reason.
    """
    transactions = merchants["m02_healthy_mid"]["inputs"]["transactions"]
    metrics, volatility, flags, risk, offer = _run_pipeline(transactions)
    actual = {
        "compute_revenue_metrics": metrics,
        "compute_volatility": volatility,
        "detect_cashflow_flags": flags,
        "score_risk": risk,
        "compute_offer": offer,
    }
    for tool in TOOLS:
        assert set(tool.returns) == set(actual[tool.name]), tool.name


def test_registry_resolves_names_and_refuses_unknown_ones():
    assert get("compute_offer").function is compute_offer
    with pytest.raises(KeyError, match="unknown tool"):
        get("compute_vibes")


def test_schemas_are_shaped_for_a_model():
    for schema in schemas():
        assert set(schema) == {"name", "description", "parameters"}
        assert schema["description"].strip()
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"]["required"]
