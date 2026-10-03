"""User Story 2, proved without a model (T030).

The point of this file: every assertion here uses a **hand-written** memo. No provider is
constructed, no model is called, nothing is mocked. The grounding guarantee is demonstrable
on its own, independent of whatever the model happens to do on a given day -- which is why
the spec made US2 independently testable in the first place.

It is also the strongest thing to show someone. "Here is a memo with one invented figure,
and here is the system refusing to display it" takes thirty seconds and needs no setup.
"""

import pytest

from ledgermind import policy
from ledgermind.agent.ledger import FactLedger
from ledgermind.data import gen, load
from ledgermind.guardrail.check import check_output, fallback_memo
from ledgermind.tools.flags import detect_cashflow_flags
from ledgermind.tools.offer import compute_offer
from ledgermind.tools.registry import BY_NAME
from ledgermind.tools.revenue import compute_revenue_metrics
from ledgermind.tools.scoring import score_risk
from ledgermind.tools.volatility import compute_volatility


def _analyse(transactions):
    """Run every analysis and register it, exactly as the dispatcher will."""
    ledger = FactLedger()
    metrics = compute_revenue_metrics(transactions)
    ledger.register(BY_NAME["compute_revenue_metrics"].namespace, "compute_revenue_metrics", metrics)
    volatility = compute_volatility(metrics["monthly_revenue"])
    ledger.register(BY_NAME["compute_volatility"].namespace, "compute_volatility", volatility)
    flags = detect_cashflow_flags(transactions)
    ledger.register(BY_NAME["detect_cashflow_flags"].namespace, "detect_cashflow_flags", flags)
    risk = score_risk(metrics, volatility, flags)
    ledger.register(BY_NAME["score_risk"].namespace, "score_risk", risk)
    offer = compute_offer(metrics, risk)
    ledger.register(BY_NAME["compute_offer"].namespace, "compute_offer", offer)
    return ledger, metrics, volatility, flags, risk, offer


@pytest.fixture(scope="module")
def approved(merchant_root):
    """A merchant who gets an offer."""
    return _analyse(load.load_merchant(merchant_root / "m02_healthy_mid")["transactions"])


@pytest.fixture(scope="module")
def refused(merchant_root):
    """A merchant who is declined."""
    return _analyse(load.load_merchant(merchant_root / "m14_declining_distress")["transactions"])


def _honest_memo(metrics, flags, offer):
    return (
        f"Revenue averaged {metrics['avg_monthly_revenue']:,.0f} a month over "
        f"{metrics['months_covered']} months. We saw {flags['overdraft_count']} overdraft "
        f"episodes. We can advance {offer['advance_amount']:,.0f}, repaid at "
        f"{offer['repayment_pct']:.0f}% of monthly revenue over "
        f"{offer['expected_duration_months']} months."
    )


# --- the central assertion ------------------------------------------------------------


def test_an_honest_memo_passes(approved):
    ledger, metrics, _, flags, risk, offer = approved
    result, provenance = check_output(_honest_memo(metrics, flags, offer), ledger, offer, risk)
    assert result.passed, result.reason()
    assert provenance, "a passing memo must carry provenance for what it cited"


def test_a_memo_with_one_invented_figure_is_rejected(approved):
    """The demo. One figure changed; the memo never reaches the analyst."""
    ledger, metrics, _, flags, risk, offer = approved
    memo = _honest_memo(metrics, flags, offer).replace(
        f"{offer['advance_amount']:,.0f}", "999,999"
    )
    result, _ = check_output(memo, ledger, offer, risk)
    assert not result.passed
    assert "999,999" in result.unresolved_numerals
    assert "999,999" in result.reason()


def test_rejection_names_the_offending_figure(approved):
    """A guardrail that says only "rejected" cannot drive a useful retry."""
    ledger, metrics, _, flags, risk, offer = approved
    memo = _honest_memo(metrics, flags, offer) + " Revenue is trending toward 88,888 a month."
    result, _ = check_output(memo, ledger, offer, risk)
    assert result.unresolved_numerals == ("88,888",)


def test_a_miscount_is_rejected(approved):
    ledger, metrics, _, flags, risk, offer = approved
    memo = _honest_memo(metrics, flags, offer) + " Seventeen payments were returned."
    result, _ = check_output(memo, ledger, offer, risk)
    assert not result.passed


# --- completeness: the gap grounding alone leaves (FR-021) ----------------------------


def test_a_memo_with_no_figures_is_rejected_as_incomplete(approved):
    """Grounding alone would pass this perfectly -- there is nothing to resolve.

    Any filter creates pressure toward whatever satisfies it, and for grounding that
    pressure points at saying less. So the floor is stated separately.
    """
    ledger, _, _, _, risk, offer = approved
    result, _ = check_output("This merchant looks solid. We recommend proceeding.", ledger, offer, risk)
    assert not result.passed
    assert result.incomplete
    assert result.unresolved_numerals == (), "nothing was fabricated; it was simply empty"


def test_a_memo_omitting_the_advance_is_incomplete(approved):
    ledger, metrics, _, _, risk, offer = approved
    memo = f"Revenue averaged {metrics['avg_monthly_revenue']:,.0f} a month and looks stable."
    result, _ = check_output(memo, ledger, offer, risk)
    assert result.incomplete
    assert "offer.advance_amount" in result.missing_citations


def test_a_memo_omitting_revenue_is_incomplete(approved):
    ledger, _, _, _, risk, offer = approved
    memo = (
        f"We can advance {offer['advance_amount']:,.0f} at {offer['repayment_pct']:.0f}% "
        f"over {offer['expected_duration_months']} months."
    )
    result, _ = check_output(memo, ledger, offer, risk)
    assert result.incomplete
    assert "a revenue metric" in result.missing_citations


# --- declines (FR-021's decline variant, FR-033) --------------------------------------


def test_a_decline_memo_citing_the_reason_passes(refused):
    """A declined merchant has no advance, which is what made the original FR-021
    unsatisfiable for every decline until it was amended."""
    ledger, metrics, volatility, flags, risk, offer = refused
    assert offer["declined"], "fixture must be a declined merchant"
    memo = (
        f"Declined at a risk score of {risk['risk_score']} out of 100. Revenue fell "
        f"{metrics['mom_growth_pct']:.1f}% a month across {metrics['months_covered']} "
        f"months, with {flags['overdraft_count']} overdraft episodes."
    )
    result, _ = check_output(memo, ledger, offer, risk)
    assert result.passed, result.reason()


def test_a_decline_memo_without_the_reason_figure_is_incomplete(refused):
    ledger, metrics, _, _, risk, offer = refused
    memo = f"We are declining this application. Revenue covered {metrics['months_covered']} months."
    result, _ = check_output(memo, ledger, offer, risk)
    assert not result.passed
    assert "the figure named in the decline reason" in result.missing_citations


def test_a_decline_memo_is_not_asked_for_an_advance(refused):
    """The amended rule: declines are held to the driving metrics, not to offer terms."""
    ledger, metrics, _, flags, risk, offer = refused
    memo = (
        f"Declined at risk score {risk['risk_score']}. Revenue over "
        f"{metrics['months_covered']} months showed {flags['overdraft_count']} overdrafts."
    )
    result, _ = check_output(memo, ledger, offer, risk)
    assert "offer.advance_amount" not in result.missing_citations


# --- the fallback (FR-024, FR-025) ----------------------------------------------------


def test_the_fallback_memo_passes_its_own_verification(approved):
    """Load-bearing. A fallback that could fail verification would leave the system with
    no safe output at all -- the one state Article II forbids."""
    ledger, metrics, volatility, flags, risk, offer = approved
    memo = fallback_memo(metrics, volatility, flags, risk, offer)
    result, _ = check_output(memo, ledger, offer, risk, used_fallback=True)
    assert result.passed, result.reason()


def test_the_fallback_memo_passes_for_a_declined_merchant(refused):
    ledger, metrics, volatility, flags, risk, offer = refused
    memo = fallback_memo(metrics, volatility, flags, risk, offer)
    result, _ = check_output(memo, ledger, offer, risk, used_fallback=True)
    assert result.passed, result.reason()


def test_the_fallback_announces_itself(approved):
    """It must never be substituted silently (FR-025).

    The notice is inside the text rather than a flag the caller might forget to render.
    """
    _, metrics, volatility, flags, risk, offer = approved
    memo = fallback_memo(metrics, volatility, flags, risk, offer)
    assert "NOTICE" in memo
    assert "verification failed" in memo


def test_the_fallback_carries_the_result_flag(approved):
    ledger, metrics, volatility, flags, risk, offer = approved
    result, _ = check_output(
        fallback_memo(metrics, volatility, flags, risk, offer), ledger, offer, risk, used_fallback=True
    )
    assert result.used_fallback is True


@pytest.mark.parametrize("merchant_id", [spec.merchant_id for spec in gen.MERCHANT_SPECS])
def test_the_fallback_passes_for_every_merchant(merchant_root, merchant_id):
    """All twenty, approved and declined alike. The safe path must always be available."""
    ledger, metrics, volatility, flags, risk, offer = _analyse(
        load.load_merchant(merchant_root / merchant_id)["transactions"]
    )
    memo = fallback_memo(metrics, volatility, flags, risk, offer)
    result, _ = check_output(memo, ledger, offer, risk, used_fallback=True)
    assert result.passed, f"{merchant_id}: {result.reason()}"


# --- policy breaches are a defect, not prose to regenerate (FR-022) -------------------


def test_an_out_of_bounds_offer_is_caught(approved):
    ledger, metrics, _, flags, risk, offer = approved
    tampered = dict(offer, advance_amount=policy.ADVANCE_CAP_ABSOLUTE * 2)
    result, _ = check_output(_honest_memo(metrics, flags, tampered), ledger, tampered, risk)
    assert not result.passed
    assert any("exceeds the cap" in breach for breach in result.policy_breaches)


def test_a_mismatched_policy_version_is_caught(approved):
    ledger, metrics, _, flags, risk, offer = approved
    tampered = dict(offer, policy_version="0.0.1")
    result, _ = check_output(_honest_memo(metrics, flags, offer), ledger, tampered, risk)
    assert any("policy version" in breach for breach in result.policy_breaches)


def test_a_declined_offer_with_a_live_advance_is_caught(approved):
    """A non-zero advance beside a decline is the kind of contradiction an interface could
    render as a real offer."""
    ledger, metrics, _, flags, risk, offer = approved
    tampered = dict(offer, declined=True, decline_reason="risk score 90", advance_amount=50_000.0)
    result, _ = check_output(_honest_memo(metrics, flags, offer), ledger, tampered, risk)
    assert any("non-zero" in breach for breach in result.policy_breaches)


# --- retries are bounded (FR-023) -----------------------------------------------------


def test_the_retry_allowance_is_declared_and_small():
    assert 1 <= policy.MAX_REGENERATION_ATTEMPTS <= 5


def test_the_attempt_number_is_carried_through(approved):
    ledger, metrics, _, flags, risk, offer = approved
    result, _ = check_output(_honest_memo(metrics, flags, offer), ledger, offer, risk, attempt=3)
    assert result.attempt == 3
