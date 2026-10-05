"""What the interface displays, as pure functions.

Separated from the Streamlit shell so it can be tested. A UI that cannot be tested is a UI
where FR-030 -- the interface must not compute, derive or re-round a financial value -- is
enforced by nobody.

Every function here either passes a recorded value through untouched or formats it for
display. `assert_passthrough` exists so a test can prove that: it compares what the rows
carry against what the decision holds, field by field.

Full precision throughout. The memo quotes figures rounded to a renderable precision; this
is the other half of that, and the reason FR-013 asks for the record to stay inspectable. An
analyst reading "2.0%" should be able to see the 1.9886 behind it.
"""

from __future__ import annotations

from typing import Any

from ledgermind.agent.schema import Decision

OFFER_LABELS = (
    ("advance_amount", "Advance"),
    ("repayment_pct", "Repayment, % of monthly revenue"),
    ("expected_duration_months", "Expected duration, months"),
    ("total_repayable", "Total repayable"),
    ("policy_version", "Policy version"),
)

ANALYSIS_LABELS = (
    ("revenue_metrics", "Revenue", (
        ("avg_monthly_revenue", "Average monthly revenue"),
        ("total_revenue", "Total revenue"),
        ("mom_growth_pct", "Growth, % per month"),
        ("months_covered", "Months covered"),
        ("trend", "Trend"),
    )),
    ("volatility", "Stability", (
        ("stability_score", "Stability score"),
        ("revenue_cv_pct", "Variation after trend, %"),
        ("revenue_stdev", "Residual standard deviation"),
        ("insufficient_history", "History below the minimum"),
    )),
    ("flags", "Cashflow", (
        ("overdraft_count", "Overdraft episodes"),
        ("bounced_payment_count", "Returned payments"),
        ("large_one_off_count", "Large one-off inflows"),
        ("min_balance", "Lowest balance"),
        ("declining_balance", "Balance eroding"),
    )),
    ("reconciliation", "Sales reconciliation", (
        ("reconciliation_ratio_pct", "Banked vs sales records, %"),
        ("reconciliation_tolerance_pct", "Tolerance, %"),
        ("months_compared", "Months compared"),
        ("reconciled", "Reconciles"),
    )),
    ("risk", "Risk", (
        ("risk_score", "Risk score"),
        ("risk_score_max", "out of"),
        ("risk_tier", "Tier"),
    )),
)


def offer_rows(decision: Decision) -> list[dict[str, Any]]:
    """The offer, exactly as `compute_offer` returned it."""
    if decision.declined:
        return [
            # The raw value, not a label. The shell renders it; keeping the
            # transformation out of here is what lets `assert_passthrough` stay strict.
            {"field": "declined", "label": "Decision", "value": decision.offer["declined"]},
            {"field": "decline_reason", "label": "Reason",
             "value": decision.offer.get("decline_reason")},
            {"field": "policy_version", "label": "Policy version",
             "value": decision.offer.get("policy_version")},
        ]
    return [
        {"field": field, "label": label, "value": decision.offer[field]}
        for field, label in OFFER_LABELS
        if field in decision.offer
    ]


def analysis_sections(decision: Decision) -> list[dict[str, Any]]:
    """Every analysis result, grouped, at the precision the tools produced."""
    sections = []
    for attribute, title, fields in ANALYSIS_LABELS:
        result = getattr(decision, attribute, None)
        if attribute == "reconciliation":
            # Not carried as its own attribute on the Decision; read from the facts.
            result = {
                key.split(".", 1)[1]: value
                for key, value in decision.facts.items()
                if key.startswith("reconcile.")
            }
        if not result:
            continue
        rows = [
            {"field": field, "label": label, "value": result[field]}
            for field, label in fields
            if field in result
        ]
        if rows:
            sections.append({"title": title, "rows": rows})
    return sections


def provenance_rows(decision: Decision) -> list[dict[str, Any]]:
    """Every figure the memo quoted, the analyses it could have come from, and their values.

    `analyses` is a list because resolution is existence-based: when two recorded facts hold
    the same value, both are reported rather than one being guessed at. Reporting all of
    them is honest; picking one would invent provenance.
    """
    rows = []
    for entry in decision.provenance:
        keys = sorted(set(entry.fact_keys))
        rows.append(
            {
                "wrote": entry.numeral,
                "read_as": entry.value,
                "analyses": keys,
                "recorded": [decision.recorded_value(key) for key in keys],
            }
        )
    return rows


def unresolved_rows(decision: Decision) -> list[str]:
    """Figures that failed verification.

    On a fallback this is what the *model* wrote, carried through from the last rejected
    attempt -- the fallback's own result is clean, having passed. On a verified run it is
    empty.
    """
    verification = decision.verification
    return list(verification.rejected_numerals or verification.unresolved_numerals)


def assert_passthrough(decision: Decision) -> None:
    """Prove the rows carry recorded values untouched (FR-030).

    Called from the tests rather than the app. The interface may format a value for reading;
    it may not change one. Comparing identity field by field is the only way to show that
    without inspecting the rendering code.
    """
    for row in offer_rows(decision):
        if row["field"] in decision.offer:
            assert row["value"] == decision.offer[row["field"]], row["field"]

    for section in analysis_sections(decision):
        for row in section["rows"]:
            sources = [
                decision.revenue_metrics, decision.volatility, decision.flags, decision.risk,
                {k.split(".", 1)[1]: v for k, v in decision.facts.items()
                 if k.startswith("reconcile.")},
            ]
            found = [src[row["field"]] for src in sources if row["field"] in src]
            assert found, row["field"]
            assert row["value"] in found, row["field"]

    for row in provenance_rows(decision):
        for key, value in zip(row["analyses"], row["recorded"]):
            assert value == decision.facts[key], key


def format_value(value: Any) -> str:
    """Readable, without changing the number.

    Thousands separators and a trailing percent sign are presentation. Rounding is not, so
    nothing here rounds: the point of this panel is the precision the memo could not show.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:,}" if abs(value) >= 1000 else f"{value}"
        return text
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)
