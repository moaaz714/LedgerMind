"""The evaluation harness and its measures (T031, T032, T034).

Driven by a scripted provider, so the measures are tested without model time. The harness's
job is arithmetic over finished decisions, and arithmetic does not need a language model to
verify -- which also means these tests catch a broken measure, where a real pass would just
print a number nobody could check.
"""

import json

import pytest

from ledgermind.agent import loop
from ledgermind.agent.schema import Decision, Provenance, VerificationResult
from ledgermind.data import gen, load
from ledgermind.eval import harness, metrics
from ledgermind.llm.base import Completion, ToolCall

SIX = [
    "compute_revenue_metrics",
    "compute_volatility",
    "detect_cashflow_flags",
    "reconcile_sales",
    "score_risk",
    "compute_offer",
]


class Honest:
    """Runs every analysis, then writes a memo composed from the merchant's own figures."""

    name = "fake/honest"

    def __init__(self):
        self.turn = 0
        self.results = {}

    def chat(self, messages, tools=None):
        if tools is None:
            return Completion(text=self._memo())
        self.turn += 1
        if self.turn <= len(SIX):
            return Completion(tool_calls=(ToolCall(name=SIX[self.turn - 1]),))
        return Completion(text=self._memo())

    def _memo(self):
        return self.memo


def _decision_for(merchant_id, root):
    """One real decision, with the memo built from the tool results so it verifies."""
    from ledgermind.tools.flags import detect_cashflow_flags
    from ledgermind.tools.offer import compute_offer
    from ledgermind.tools.reconcile import reconcile_sales
    from ledgermind.tools.revenue import compute_revenue_metrics
    from ledgermind.tools.scoring import score_risk
    from ledgermind.tools.volatility import compute_volatility

    merchant = load.load_merchant(root / merchant_id)
    tx, sl = merchant["transactions"], merchant["sales"]
    m = compute_revenue_metrics(tx)
    v = compute_volatility(m["monthly_revenue"])
    f = detect_cashflow_flags(tx)
    rc = reconcile_sales(tx, sl)
    r = score_risk(m, v, f, rc)
    o = compute_offer(m, r)

    if o["declined"]:
        memo = (
            f"Declined. Risk score {r['risk_score']} of {r['risk_score_max']} over "
            f"{m['months_covered']} months. {r['decline_reason']}."
        )
    else:
        memo = (
            f"Revenue averaged {m['avg_monthly_revenue']:,.2f} a month over "
            f"{m['months_covered']} months. We can advance {o['advance_amount']:,.2f} at "
            f"{o['repayment_pct']:.1f}% over {o['expected_duration_months']} months."
        )

    provider = Honest()
    provider.memo = memo
    return loop.run(merchant, provider)


@pytest.fixture(scope="module")
def three_decisions(merchant_root):
    """A monotonicity pair plus a declined merchant -- enough to exercise every measure."""
    ids = ["m15_declining_pairlow", "m16_declining_pairhigh", "m21_recon_overstated"]
    return {mid: _decision_for(mid, merchant_root) for mid in ids}


@pytest.fixture(scope="module")
def three_truths(merchant_root, three_decisions):
    return {
        mid: json.loads((merchant_root / mid / gen.TRUTH_FILENAME).read_text(encoding="utf-8"))
        for mid in three_decisions
    }


# --- SC-001, the measure that leads --------------------------------------------------


def test_monotonicity_holds_for_a_real_pair(three_decisions):
    """Same revenue, more volatility, advance must not be larger."""
    pairs = metrics.monotonicity(
        three_decisions, [("m15_declining_pairlow", "m16_declining_pairhigh")]
    )
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.erratic_cv > pair.steady_cv, "the pair must differ in volatility"
    assert pair.holds
    assert pair.erratic_advance <= pair.steady_advance


def test_monotonicity_detects_a_violation():
    """The measure must be able to fail, or reporting it proves nothing."""
    def fake(advance, cv):
        return Decision(
            merchant_id="x", revenue_metrics={}, volatility={"revenue_cv": cv}, flags={},
            risk={}, offer={"advance_amount": advance}, memo="",
            verification=VerificationResult(passed=True),
        )

    pairs = metrics.monotonicity(
        {"steady": fake(50_000, 0.1), "erratic": fake(90_000, 0.5)}, [("steady", "erratic")]
    )
    assert pairs[0].holds is False


def test_pairs_missing_from_a_subset_are_skipped():
    """A --limit run must not report a pair it did not evaluate."""
    assert metrics.monotonicity({}, [("a", "b")]) == []


# --- SC-002, which is a check on the gate rather than on the model -------------------


def test_grounding_is_total_for_a_displayed_memo(three_decisions, three_truths):
    """Always 100%, by construction: a memo that failed was never displayed.

    Below 100% would mean an unverified memo reached a consumer, which Article II forbids.
    So this measure watches the gate, not the model.
    """
    measures = metrics.collect(three_decisions, three_truths, ())
    assert measures.grounding_total > 0
    assert measures.grounding_faithfulness == 1.0


def test_grounding_counts_are_recounted_from_the_memo(three_decisions):
    """Not read off the provenance, so the measure is independent of the guardrail's own
    bookkeeping."""
    decision = three_decisions["m15_declining_pairlow"]
    resolved, total = metrics.grounding_counts(decision)
    assert total >= 4
    assert resolved == total


# --- SC-003, SC-005, SC-009 ----------------------------------------------------------


def test_no_policy_breaches_on_real_offers(three_decisions, three_truths):
    assert metrics.collect(three_decisions, three_truths, ()).policy_breaches == []


def test_a_tampered_offer_is_caught():
    from ledgermind import policy

    decision = Decision(
        merchant_id="x", revenue_metrics={}, volatility={}, flags={},
        risk={"risk_tier": "A"},
        offer={
            "advance_amount": policy.ADVANCE_CAP_ABSOLUTE * 3, "repayment_pct": 13.0,
            "expected_duration_months": 12, "total_repayable": 1.0,
            "advance_cap_applied": False, "declined": False, "decline_reason": None,
            "policy_version": policy.POLICY_VERSION,
        },
        memo="", verification=VerificationResult(passed=True),
    )
    measures = metrics.collect({"x": decision}, {}, ())
    assert measures.policy_breaches
    assert not measures.all_pass


def test_tools_agree_with_recorded_truth(three_decisions, three_truths):
    measures = metrics.collect(three_decisions, three_truths, ())
    assert measures.truth_mismatches == []


def test_a_truth_mismatch_is_reported():
    decision = Decision(
        merchant_id="x",
        revenue_metrics={"avg_monthly_revenue": 1.0, "total_revenue": 1.0,
                         "mom_growth_pct": 0.0, "months_covered": 1},
        volatility={"revenue_cv": 0.0}, flags={"overdraft_count": 0,
        "bounced_payment_count": 0, "large_one_off_count": 0},
        risk={}, offer={"declined": True, "decline_reason": "x", "advance_amount": 0.0,
                        "repayment_pct": 0.0, "expected_duration_months": 0,
                        "total_repayable": 0.0, "advance_cap_applied": False,
                        "policy_version": "1.0.0"},
        memo="", verification=VerificationResult(passed=True),
    )
    found = metrics.truth_mismatches(decision, {"true_avg_monthly_revenue": 99_999.0})
    assert len(found) == 1
    assert found[0].field == "true_avg_monthly_revenue"
    assert found[0].gap > 99_000


def test_the_reconciliation_fixture_is_declined_for_reconciliation(three_decisions, three_truths):
    measures = metrics.collect(three_decisions, three_truths, ())
    assert measures.reconciliation_errors == []
    declined = dict(measures.declines)
    assert "m21_recon_overstated" in declined
    assert "sales records imply" in declined["m21_recon_overstated"]


# --- SC-004 ---------------------------------------------------------------------------


def test_identical_offers_pass_consistency():
    offer = {"advance_amount": 1.0, "declined": False}
    measures = metrics.Measures(consistency_offers=[offer, dict(offer), dict(offer)])
    assert measures.consistency_holds


def test_differing_offers_fail_consistency():
    measures = metrics.Measures(
        consistency_offers=[{"advance_amount": 1.0}, {"advance_amount": 2.0}]
    )
    assert measures.consistency_holds is False
    assert metrics.Measures(consistency_offers=[]).consistency_holds, "no runs is not a failure"


# --- the harness ----------------------------------------------------------------------


def test_the_harness_runs_a_subset_and_reports(merchant_root):
    """--limit exists because a full pass costs minutes of model time."""
    class Scripted:
        name = "fake/scripted"
        def __init__(self): self.per_merchant = {}
        def chat(self, messages, tools=None):
            ident = next(
                (m["content"] for m in messages if m["role"] == "user"), ""
            )
            state = self.per_merchant.setdefault(ident, {"turn": 0})
            if tools is None:
                return Completion(text="x")
            state["turn"] += 1
            if state["turn"] <= len(SIX):
                return Completion(tool_calls=(ToolCall(name=SIX[state["turn"] - 1]),))
            return Completion(text="x")

    report = harness.run_eval(
        Scripted(), root=merchant_root, limit=2, consistency_runs=0,
        consistency_merchant="none",
    )
    assert report.merchant_count == 2
    assert "LedgerMind evaluation" in report.render()
    assert report.as_dict()["merchant_count"] == 2


def test_a_run_that_fails_costs_one_line_not_the_pass(merchant_root):
    """One uncooperative model should not abort the measurement."""
    class Broken:
        name = "fake/broken"
        def chat(self, messages, tools=None):
            return Completion(tool_calls=(ToolCall(name="compute_revenue_metrics"),))

    report = harness.run_eval(
        Broken(), root=merchant_root, limit=2, consistency_runs=0, consistency_merchant="none"
    )
    assert len(report.failures) == 2
    assert report.passed is False
    assert "RUNS THAT DID NOT REACH A DECISION" in report.render()


def test_the_report_states_how_many_merchants_it_covered(merchant_root):
    """So a truncated pass cannot be mistaken for a full one."""
    class Scripted:
        name = "fake"
        def __init__(self): self.state = {}
        def chat(self, messages, tools=None):
            key = len([m for m in messages if m["role"] == "tool"])
            if tools is None or key >= len(SIX):
                return Completion(text="x")
            return Completion(tool_calls=(ToolCall(name=SIX[key]),))

    report = harness.run_eval(
        Scripted(), root=merchant_root, limit=3, consistency_runs=0, consistency_merchant="none"
    )
    assert "merchants  3" in report.render()


def test_the_report_serialises_for_the_interface(three_decisions, three_truths):
    measures = metrics.collect(
        three_decisions, three_truths, [("m15_declining_pairlow", "m16_declining_pairhigh")]
    )
    report = harness.Report(measures=measures, elapsed=1.0, provider="fake", merchant_count=3)
    payload = report.as_dict()
    assert json.dumps(payload), "must be JSON-serialisable for T038"
    assert payload["monotonicity_holds"] is True
    assert payload["grounding_faithfulness"] == 1.0


def test_all_pass_requires_every_criterion(three_decisions, three_truths):
    measures = metrics.collect(three_decisions, three_truths, ())
    assert measures.all_pass

    measures.policy_breaches.append(("x", "broken"))
    assert not measures.all_pass
