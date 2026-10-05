"""What the interface displays (T035-T038, FR-029, FR-030).

The Streamlit shell cannot be unit-tested usefully, so everything that decides *what* is
shown lives in `view.py` and is tested here. Otherwise FR-030 -- the interface must not
compute, derive or re-round a financial value -- would be enforced by nobody, which for the
one module whose entire job is displaying money is the wrong place to have no coverage.
"""

import pytest

from ledgermind.agent import loop
from ledgermind.app import view
from ledgermind.data import load
from ledgermind.llm.base import Completion, ToolCall

SIX = [
    "compute_revenue_metrics",
    "compute_volatility",
    "detect_cashflow_flags",
    "reconcile_sales",
    "score_risk",
    "compute_offer",
]


class Scripted:
    name = "fake/scripted"

    def __init__(self, memo, repeats=1):
        self.memo = memo
        self.repeats = repeats
        self.turn = 0

    def chat(self, messages, tools=None):
        if tools is None:
            return Completion(text=self.memo)
        self.turn += 1
        if self.turn <= len(SIX):
            return Completion(tool_calls=(ToolCall(name=SIX[self.turn - 1]),))
        return Completion(text=self.memo)


def _decide(merchant_root, merchant_id, memo):
    return loop.run(load.load_merchant(merchant_root / merchant_id), Scripted(memo))


@pytest.fixture(scope="module")
def approved(merchant_root):
    memo = (
        "Revenue averaged 57,100 a month across eighteen months, growing 2.0% a month with "
        "a stability score of 95 out of 100. We can advance 77,000 at 13% over 12 months."
    )
    return _decide(merchant_root, "m02_healthy_mid", memo)


@pytest.fixture(scope="module")
def declined(merchant_root):
    memo = (
        "Banked revenue is 74.1% of what the sales records imply, outside the tolerance of "
        "8.0%, across 15 months. Declined at a risk score of 75."
    )
    return _decide(merchant_root, "m21_recon_overstated", memo)


@pytest.fixture(scope="module")
def fell_back(merchant_root):
    """A run where the model never produced a verifiable memo."""
    memo = "Revenue averaged 57,100 a month. We can advance 999,999 at 13% over 12 months."
    merchant = load.load_merchant(merchant_root / "m02_healthy_mid")
    return loop.run(merchant, Scripted(memo))


# --- FR-030: the interface displays, it does not compute ------------------------------


@pytest.mark.parametrize("name", ["approved", "declined", "fell_back"])
def test_every_displayed_value_is_a_recorded_value(request, name):
    """The assertion FR-030 reduces to: identity, field by field.

    Not a reading of the rendering code -- a comparison of what the rows carry against what
    the decision holds.
    """
    view.assert_passthrough(request.getfixturevalue(name))


def test_the_offer_rows_are_the_tools_return_value(approved):
    for row in view.offer_rows(approved):
        assert row["value"] == approved.offer[row["field"]]


def test_nothing_in_the_offer_panel_is_rounded(approved):
    """The memo rounds; this panel is the other half of FR-013."""
    rows = {r["field"]: r["value"] for r in view.offer_rows(approved)}
    assert rows["advance_amount"] == approved.offer["advance_amount"]
    assert rows["total_repayable"] == approved.offer["total_repayable"]


def test_full_precision_survives_to_the_analysis_panel(approved):
    """A growth rate the memo had to write as 2.0% is shown as what was computed."""
    sections = {s["title"]: s for s in view.analysis_sections(approved)}
    growth = next(
        r for r in sections["Revenue"]["rows"] if r["field"] == "mom_growth_pct"
    )
    recorded = approved.revenue_metrics["mom_growth_pct"]
    assert growth["value"] == recorded
    assert recorded != round(recorded, 1), "fixture must carry precision the memo cannot show"


# --- FR-029: provenance ---------------------------------------------------------------


def test_every_quoted_figure_has_its_analysis_and_recorded_value(approved):
    rows = view.provenance_rows(approved)
    assert rows
    for row in rows:
        assert row["analyses"], row
        assert len(row["recorded"]) == len(row["analyses"])
        for key, value in zip(row["analyses"], row["recorded"]):
            assert value == approved.facts[key]


def test_an_ambiguous_figure_reports_every_match(approved):
    """Resolution is existence-based, so several facts may hold one value.

    Reporting them all is honest; picking one would invent provenance.
    """
    rows = view.provenance_rows(approved)
    assert any(len(row["analyses"]) > 1 for row in rows), (
        "this merchant should have at least one figure matching more than one fact"
    )


def test_the_advance_is_traceable_to_the_offer(approved):
    cited = {key for row in view.provenance_rows(approved) for key in row["analyses"]}
    assert "offer.advance_amount" in cited


# --- the declined view ----------------------------------------------------------------


def test_a_declined_merchant_shows_the_reason_not_an_advance(declined):
    rows = {r["field"]: r["value"] for r in view.offer_rows(declined)}
    # The raw value. Labelling it "DECLINED" happens in the shell, which is what keeps
    # assert_passthrough able to compare rows against the decision field by field.
    assert rows["declined"] is True
    assert "sales records imply" in rows["decline_reason"]
    assert "advance_amount" not in rows, "a decline has no advance to display"


def test_the_reconciliation_section_is_built_from_the_facts(declined):
    sections = {s["title"]: s for s in view.analysis_sections(declined)}
    assert "Sales reconciliation" in sections
    rows = {r["field"]: r["value"] for r in sections["Sales reconciliation"]["rows"]}
    assert rows["reconciled"] is False
    assert rows["reconciliation_ratio_pct"] == declined.facts["reconcile.reconciliation_ratio_pct"]


# --- T038: the fallback must announce itself ------------------------------------------


def test_the_fallback_is_visible_to_the_interface(fell_back):
    assert fell_back.used_fallback is True
    assert view.unresolved_rows(fell_back), (
        "the interface must be able to name the figures that failed"
    )
    assert "999,999" in view.unresolved_rows(fell_back)


def test_a_verified_run_has_nothing_unresolved(approved):
    assert view.unresolved_rows(approved) == []


# --- formatting changes presentation, never the number --------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (77000.0, "77,000.0"),
        (1.9886, "1.9886"),
        (13.0, "13.0"),
        (18, "18"),
        (True, "yes"),
        (False, "no"),
        (None, "—"),
        ("flat", "flat"),
    ],
)
def test_format_value(value, expected):
    assert view.format_value(value) == expected


def test_formatting_never_rounds():
    """Separators and a dash for absent are presentation. Rounding would not be."""
    assert view.format_value(1.9886) == "1.9886"
    assert view.format_value(0.070063) == "0.070063"
    assert "1.99" != view.format_value(1.9886)
