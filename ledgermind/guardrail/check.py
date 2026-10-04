"""`check_output`: the single gate before anything is displayed (Article II).

Every consumer goes through here -- the interface and the evaluation alike -- so there is no
code path that reaches a display without passing it. That is the point: a guardrail with a
bypass is decoration.

Three checks, composed:

* **grounding** -- every numeral in the prose resolves to a recorded fact (FR-017 to FR-020)
* **completeness** -- the memo actually cites the decision's figures (FR-021)
* **policy** -- the offer's terms are inside their bounds (FR-022)

Completeness exists because grounding alone rewards vagueness. A memo containing no figures
passes grounding perfectly: there is nothing to resolve. "This merchant looks solid, we
recommend proceeding" would sail through. Any filter creates pressure toward whatever
satisfies it, and for grounding that pressure points at saying less -- so the floor has to
be stated separately.
"""

from __future__ import annotations

from ledgermind import policy as rules
from ledgermind.agent.ledger import FactLedger
from ledgermind.agent.schema import Provenance, VerificationResult
from ledgermind.guardrail.grounding import check_grounding, extract_numerals
from ledgermind.guardrail.policy import check_offer_policy

# An approved memo must cite the money and at least one revenue measure. Without the
# advance and the percentage it is not an offer; without a revenue figure there is no
# stated basis for it.
REQUIRED_APPROVED = ("offer.advance_amount", "offer.repayment_pct")
REVENUE_ALTERNATIVES = (
    "revenue.avg_monthly_revenue",
    "revenue.total_revenue",
    "revenue.mom_growth_pct",
    "revenue.months_covered",
)

# A declined memo cites the metrics that drove the refusal instead. The advance and
# percentage do not exist, which is what made the original FR-021 unsatisfiable for every
# decline until it was amended.
DECLINE_ALTERNATIVES = (
    "risk.risk_score",
    "revenue.months_covered",
    "volatility.revenue_cv",
    "volatility.stability_score",
    "flags.overdraft_count",
    "flags.bounced_payment_count",
    "flags.flag_count",
)


def _missing_citations(cited: frozenset[str], declined: bool) -> tuple[str, ...]:
    """Which required figures the memo failed to quote."""
    if not declined:
        missing = [key for key in REQUIRED_APPROVED if key not in cited]
        if not any(key in cited for key in REVENUE_ALTERNATIVES):
            missing.append("a revenue metric")
        return tuple(missing)

    # The decline-reason citation is checked in `check_output`, which has the resolved
    # numerals to compare against; here we only require a driving metric.
    if not any(key in cited for key in DECLINE_ALTERNATIVES):
        return ("a metric that drove the decision",)
    return ()


def check_output(
    memo: str,
    ledger: FactLedger,
    offer: dict,
    risk: dict,
    attempt: int = 1,
    used_fallback: bool = False,
) -> tuple[VerificationResult, tuple[Provenance, ...]]:
    """Verify a candidate memo. Returns the result and the provenance of what it cited."""
    grounding = check_grounding(memo, ledger)
    breaches = check_offer_policy(offer, risk)
    cited = grounding.cited_keys()

    declined = bool(offer.get("declined"))
    missing = _missing_citations(cited, declined)

    if declined and offer.get("decline_reason"):
        reason_values = {numeral.value for numeral in extract_numerals(offer["decline_reason"])}
        quoted = {numeral.value for numeral, _ in grounding.resolved}
        if reason_values and not (reason_values & quoted):
            missing = missing + ("the figure named in the decline reason",)

    provenance = tuple(
        Provenance(numeral=numeral.text, value=numeral.value, fact_keys=keys)
        for numeral, keys in grounding.resolved
    )

    result = VerificationResult(
        passed=grounding.passed and not breaches and not missing,
        unresolved_numerals=tuple(numeral.text for numeral in grounding.unresolved),
        policy_breaches=breaches,
        incomplete=bool(missing),
        missing_citations=tuple(missing),
        attempt=attempt,
        used_fallback=used_fallback,
    )
    return result, provenance


def fallback_memo(
    revenue_metrics: dict, volatility: dict, flags: dict, risk: dict, offer: dict
) -> str:
    """A memo built only from recorded facts, for when generation keeps failing (FR-024).

    Every figure below comes from a tool, so this text grounds by construction -- which is
    asserted in the tests, because a fallback that could itself fail verification would
    leave the system with no safe output at all.

    The notice is part of the text rather than a flag the caller may forget to render. The
    system must never substitute this silently (FR-025).
    """
    lines = [
        # No figure in this notice: the retry allowance is a policy constant, not a tool
        # output, so quoting it here would make the fallback fail its own grounding check.
        "NOTICE: narrative verification failed after the permitted attempts. The figures "
        "below are the tool-computed values; no generated explanation accompanies them.",
        "",
        f"Revenue: {revenue_metrics['months_covered']} months of history, averaging "
        f"{revenue_metrics['avg_monthly_revenue']:,.2f} per month, "
        # One decimal place, because that is the finest rung on the grounding ladder
        # (FR-018). Two would produce a numeral the checker cannot resolve.
        f"{revenue_metrics['mom_growth_pct']:+.1f}% per month ({revenue_metrics['trend']}).",
        f"Stability: score {volatility['stability_score']} of "
        f"{volatility['stability_score_max']}.",
        f"Cashflow: {flags['overdraft_count']} overdraft episodes, "
        f"{flags['bounced_payment_count']} returned payments, "
        f"{flags['large_one_off_count']} large one-off inflows.",
    ]

    if offer.get("declined"):
        lines.append(
            f"Decision: declined at risk score {risk['risk_score']} of {risk['risk_score_max']}."
        )
        lines.append(f"Reason: {offer['decline_reason']}.")
    else:
        lines.append(
            f"Decision: tier {risk['risk_tier']} at risk score {risk['risk_score']} of "
            f"{risk['risk_score_max']}."
        )
        lines.append(
            f"Offer: advance {offer['advance_amount']:,.2f}, repaid at "
            f"{offer['repayment_pct']:.2f}% of monthly revenue over an expected "
            f"{offer['expected_duration_months']} months, "
            f"{offer['total_repayable']:,.2f} total repayable."
        )

    return "\n".join(lines)
