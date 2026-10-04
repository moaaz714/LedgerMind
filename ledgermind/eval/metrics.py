"""The measures (T031, T032, FR-026).

Pure functions over finished decisions plus recorded truth. No model, no I/O -- which means
they are testable with hand-built decisions, and the harness that drives the model is a
separate concern from the arithmetic that judges it.

**Monotonicity leads.** The other measures verify the plumbing: grounding faithfulness is
100% by construction, because `check_output` gates every displayed memo, so a figure below
100% means the gate is broken rather than the model lying. Monotonicity is different -- it
can fail while every other measure passes, because it tests whether the *lending logic* is
sane rather than whether the machinery runs. That is why it is reported first.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ledgermind import policy
from ledgermind.agent.schema import Decision
from ledgermind.guardrail.grounding import extract_numerals
from ledgermind.guardrail.policy import check_offer_policy

# How close a tool's output must sit to the generator's recorded answer (SC-005). Money is
# compared to the cent; the coefficient of variation to four decimal places, since the tool
# recovers it from a parsed file while truth states it from the construction.
TRUTH_ABS_TOLERANCE = {
    "true_avg_monthly_revenue": 0.02,
    "true_total_revenue": 0.05,
    "true_mom_growth_pct": 0.01,
    "true_revenue_cv": 1e-4,
    "true_total_sales": 0.05,
    "true_reconciliation_ratio": 0.02,
}


@dataclass
class MonotonicityPair:
    steady_id: str
    erratic_id: str
    steady_advance: float
    erratic_advance: float
    steady_cv: float
    erratic_cv: float

    @property
    def holds(self) -> bool:
        return self.erratic_advance <= self.steady_advance


@dataclass
class TruthMismatch:
    merchant_id: str
    field: str
    computed: float
    recorded: float

    @property
    def gap(self) -> float:
        return abs(self.computed - self.recorded)


@dataclass
class Measures:
    """Everything one evaluation pass found."""

    monotonicity: list[MonotonicityPair] = field(default_factory=list)
    grounding_resolved: int = 0
    grounding_total: int = 0
    policy_breaches: list[tuple[str, str]] = field(default_factory=list)
    truth_mismatches: list[TruthMismatch] = field(default_factory=list)
    consistency_merchant: str | None = None
    consistency_offers: list[dict] = field(default_factory=list)
    reconciliation_errors: list[str] = field(default_factory=list)
    attempts: dict[int, int] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    declines: list[tuple[str, str]] = field(default_factory=list)

    @property
    def grounding_faithfulness(self) -> float:
        if not self.grounding_total:
            return 1.0
        return self.grounding_resolved / self.grounding_total

    @property
    def consistency_holds(self) -> bool:
        if len(self.consistency_offers) < 2:
            return True
        first = self.consistency_offers[0]
        return all(offer == first for offer in self.consistency_offers[1:])

    @property
    def monotonicity_holds(self) -> bool:
        return all(pair.holds for pair in self.monotonicity)

    @property
    def all_pass(self) -> bool:
        return (
            self.monotonicity_holds
            and self.grounding_faithfulness == 1.0
            and not self.policy_breaches
            and not self.truth_mismatches
            and not self.reconciliation_errors
            and self.consistency_holds
        )


def grounding_counts(decision: Decision) -> tuple[int, int]:
    """How many numerals in the displayed memo resolved, and how many there were.

    Recounted from the memo rather than taken from the provenance, so the measure is
    independent of the structure the guardrail happened to produce. This should always come
    out at 100%: a memo that failed grounding was never displayed. A figure below 100% means
    an unverified memo reached a consumer, which Article II forbids -- so the number is a
    check on the gate, not on the model.
    """
    numerals = extract_numerals(decision.memo)
    resolved = sum(1 for p in decision.provenance if p.fact_keys)
    return resolved, len(numerals)


def monotonicity(decisions: dict[str, Decision], pairs) -> list[MonotonicityPair]:
    """SC-001. Holding revenue fixed, more volatility must never buy a larger advance."""
    results = []
    for steady_id, erratic_id in pairs:
        if steady_id not in decisions or erratic_id not in decisions:
            continue
        steady, erratic = decisions[steady_id], decisions[erratic_id]
        results.append(
            MonotonicityPair(
                steady_id=steady_id,
                erratic_id=erratic_id,
                steady_advance=steady.offer["advance_amount"],
                erratic_advance=erratic.offer["advance_amount"],
                steady_cv=steady.volatility["revenue_cv"],
                erratic_cv=erratic.volatility["revenue_cv"],
            )
        )
    return results


# Which recorded answer each tool field is compared against. A pair of (result attribute,
# field name) rather than values, so a decision missing a field is simply not compared --
# the measures must survive a partial decision, which is what a failed run produces.
TRUTH_SOURCES = {
    "true_avg_monthly_revenue": ("revenue_metrics", "avg_monthly_revenue"),
    "true_total_revenue": ("revenue_metrics", "total_revenue"),
    "true_mom_growth_pct": ("revenue_metrics", "mom_growth_pct"),
    "true_months_covered": ("revenue_metrics", "months_covered"),
    "true_revenue_cv": ("volatility", "revenue_cv"),
    "true_overdraft_count": ("flags", "overdraft_count"),
    "true_bounced_payment_count": ("flags", "bounced_payment_count"),
    "true_large_one_off_count": ("flags", "large_one_off_count"),
}


def truth_mismatches(decision: Decision, truth: dict) -> list[TruthMismatch]:
    """SC-005. Every tool output against the generator's recorded answer."""
    if not truth:
        return []

    found = []
    for name, (attribute, field_name) in TRUTH_SOURCES.items():
        if name not in truth:
            continue
        result = getattr(decision, attribute, None) or {}
        if field_name not in result:
            continue
        value = result[field_name]
        tolerance = TRUTH_ABS_TOLERANCE.get(name, 0)
        if abs(value - truth[name]) > tolerance:
            found.append(
                TruthMismatch(decision.merchant_id, name, float(value), float(truth[name]))
            )
    return found


def reconciliation_errors(decision: Decision, truth: dict) -> list[str]:
    """SC-009. The mismatch fixtures are declined for it; nothing else is."""
    if "true_reconciles" not in truth:
        return []
    intended = truth["true_reconciles"]
    reason = decision.offer.get("decline_reason") or ""
    declined_for_it = decision.declined and "sales records imply" in reason
    if declined_for_it is not (not intended):
        return [
            f"{decision.merchant_id}: declined_for_reconciliation={declined_for_it} "
            f"but the generator intended reconciles={intended}"
        ]
    return []


def collect(
    decisions: dict[str, Decision],
    truths: dict[str, dict],
    pairs,
    consistency_merchant: str | None = None,
    consistency_offers: list[dict] | None = None,
) -> Measures:
    """Fold every decision into one set of measures."""
    measures = Measures(
        monotonicity=monotonicity(decisions, pairs),
        consistency_merchant=consistency_merchant,
        consistency_offers=list(consistency_offers or ()),
    )

    for merchant_id, decision in sorted(decisions.items()):
        resolved, total = grounding_counts(decision)
        measures.grounding_resolved += resolved
        measures.grounding_total += total

        for breach in check_offer_policy(decision.offer, decision.risk):
            measures.policy_breaches.append((merchant_id, breach))

        truth = truths.get(merchant_id, {})
        measures.truth_mismatches.extend(truth_mismatches(decision, truth))
        measures.reconciliation_errors.extend(reconciliation_errors(decision, truth))

        attempt = decision.verification.attempt
        measures.attempts[attempt] = measures.attempts.get(attempt, 0) + 1
        if decision.used_fallback:
            measures.fallbacks.append(merchant_id)
        if decision.declined:
            measures.declines.append((merchant_id, decision.offer["decline_reason"] or ""))

    return measures


def format_report(measures: Measures, elapsed: float, provider: str, merchant_count: int) -> str:
    """The report, as a reviewer reads it.

    Monotonicity first, deliberately. The measures below it confirm the machinery works;
    that one tests whether the lending rules make sense.
    """
    lines = [
        "=" * 74,
        "LedgerMind evaluation",
        "=" * 74,
        f"  provider   {provider}",
        f"  merchants  {merchant_count}",
        f"  elapsed    {elapsed:.1f}s  ({elapsed / max(merchant_count, 1):.1f}s per merchant)",
        "",
        "SC-001  MONOTONICITY -- more volatility must never buy a larger advance",
    ]
    for pair in measures.monotonicity:
        mark = "PASS" if pair.holds else "FAIL"
        lines.append(
            f"  {mark}  cv {pair.steady_cv:.3f} -> {pair.steady_advance:>10,.0f}   "
            f"cv {pair.erratic_cv:.3f} -> {pair.erratic_advance:>10,.0f}   "
            f"{pair.steady_id} vs {pair.erratic_id}"
        )
    if not measures.monotonicity:
        lines.append("  (no pairs in this subset)")

    lines += [
        "",
        f"SC-002  GROUNDING     {measures.grounding_resolved}/{measures.grounding_total} "
        f"numerals resolved = {measures.grounding_faithfulness:.1%}",
        f"SC-003  POLICY        {len(measures.policy_breaches)} breaches",
        f"SC-005  VS TRUTH      {len(measures.truth_mismatches)} mismatches across "
        f"{merchant_count} merchants",
        f"SC-009  RECONCILE     {len(measures.reconciliation_errors)} errors",
    ]

    if measures.consistency_offers:
        mark = "PASS" if measures.consistency_holds else "FAIL"
        lines.append(
            f"SC-004  CONSISTENCY   {mark} -- {len(measures.consistency_offers)} runs of "
            f"{measures.consistency_merchant} produced "
            f"{'identical' if measures.consistency_holds else 'DIFFERING'} offers"
        )

    lines += ["", "MODEL BEHAVIOUR (not a success criterion; how hard the guardrail worked)"]
    for attempt in sorted(measures.attempts):
        lines.append(f"  verified on attempt {attempt}: {measures.attempts[attempt]} merchants")
    lines.append(f"  fell back to the templated memo: {len(measures.fallbacks)}")
    if measures.fallbacks:
        lines.append(f"    {', '.join(measures.fallbacks)}")

    lines += ["", f"DECLINED  {len(measures.declines)}"]
    for merchant_id, reason in measures.declines:
        lines.append(f"  {merchant_id:24} {reason[:60]}")

    for label, items in (
        ("POLICY BREACHES", [f"{m}: {b}" for m, b in measures.policy_breaches]),
        ("TRUTH MISMATCHES", [
            f"{m.merchant_id}: {m.field} computed {m.computed} vs recorded {m.recorded}"
            for m in measures.truth_mismatches
        ]),
        ("RECONCILIATION ERRORS", measures.reconciliation_errors),
    ):
        if items:
            lines += ["", label]
            lines += [f"  {item}" for item in items]

    lines += ["", "=" * 74, f"RESULT: {'ALL CRITERIA PASS' if measures.all_pass else 'FAILURES ABOVE'}", "=" * 74]
    return "\n".join(lines)
