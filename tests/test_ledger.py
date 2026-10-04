"""The fact ledger (T021)."""

import pytest

from ledgermind import policy
from ledgermind.agent.ledger import FactLedger, rounding_candidates


@pytest.fixture
def ledger():
    return FactLedger()


# --- flattening -----------------------------------------------------------------------


def test_scalars_register_under_the_tool_namespace(ledger):
    ledger.register("revenue", "compute_revenue_metrics", {"avg_monthly_revenue": 47812.34})
    assert ledger.get("revenue.avg_monthly_revenue").value == 47812.34


def test_monthly_series_registers_one_fact_per_month(ledger):
    """The rule without which a legitimate memo fails grounding.

    A memo saying "January was the weakest month at 31,400" quotes a real figure. Stored as
    one list-valued entry, nothing in the ledger equals 31,400 and the memo is rejected.
    """
    ledger.register(
        "revenue",
        "compute_revenue_metrics",
        {"monthly_revenue": [("2026-01", 31_400.0), ("2026-02", 44_200.0)]},
    )
    assert ledger.get("revenue.monthly_revenue.2026-01").value == 31_400.0
    assert ledger.get("revenue.monthly_revenue.2026-02").value == 44_200.0
    assert ledger.resolve(31_400.0) == ("revenue.monthly_revenue.2026-01",)


def test_list_of_dicts_registers_each_field_by_index(ledger):
    ledger.register(
        "flags",
        "detect_cashflow_flags",
        {"large_one_offs": [{"date": "2026-03-04", "amount": 44_000.0, "description": "equipment sale"}]},
    )
    assert ledger.get("flags.large_one_offs.0.amount").value == 44_000.0
    assert ledger.get("flags.large_one_offs.0.description").value == "equipment sale"


def test_list_of_strings_registers_by_index(ledger):
    ledger.register("risk", "score_risk", {"drivers": ["revenue is stable", "no flags found"]})
    assert ledger.get("risk.drivers.0").value == "revenue is stable"


def test_non_numeric_values_are_recorded_but_not_quotable(ledger):
    """Recorded for provenance; never a numeral-match candidate."""
    ledger.register("risk", "score_risk", {"risk_tier": "B", "declined": False, "risk_score": 30})
    assert ledger.get("risk.risk_tier").value == "B"
    assert ledger.get("risk.risk_tier").is_numeric is False
    assert ledger.get("risk.declined").is_numeric is False, "a bool is not a figure"
    assert ledger.get("risk.risk_score").is_numeric is True


def test_booleans_are_not_numeric_despite_python():
    """`isinstance(True, int)` holds, so this has to be excluded explicitly.

    Otherwise a memo containing "1" would resolve against every False-valued flag.
    """
    ledger = FactLedger()
    ledger.register("flags", "detect_cashflow_flags", {"declining_balance": True})
    assert ledger.resolve(1.0) == ()


# --- the rounding ladder (FR-018) -----------------------------------------------------


def test_the_worked_example_from_the_spec():
    """47812.34 is matched by 47,812 / 47,800 / 48,000 and not by 52,000 or 50,000."""
    candidates = rounding_candidates(47_812.34)
    for allowed in (47_812.0, 47_810.0, 47_800.0, 48_000.0):
        assert allowed in candidates, allowed
    for refused in (52_000.0, 50_000.0, 47_000.0):
        assert refused not in candidates, refused


def test_ladder_has_exactly_the_declared_rungs():
    """No tolerance beyond the declared ladder is permitted (FR-018)."""
    assert policy.GROUNDING_ROUNDING_LADDER == (1, 10, 100, 1000)
    assert policy.GROUNDING_DECIMAL_PLACES == 1


def test_percentages_resolve_at_one_decimal_place():
    ledger = FactLedger()
    ledger.register("offer", "compute_offer", {"repayment_pct": 14.0})
    assert ledger.resolve(14.0) == ("offer.repayment_pct",)


def test_overdrawn_balance_resolves_written_either_way():
    """Prose says "1,000 overdrawn" as readily as "-1,000"; both mean the same fact."""
    ledger = FactLedger()
    ledger.register("flags", "detect_cashflow_flags", {"min_balance": -1_000.0})
    assert ledger.resolve(-1_000.0)
    assert ledger.resolve(1_000.0)


def test_a_figure_no_tool_produced_resolves_to_nothing():
    ledger = FactLedger()
    ledger.register("revenue", "compute_revenue_metrics", {"avg_monthly_revenue": 47_812.34})
    assert ledger.resolve(52_000.0) == ()


# --- existence-based resolution -------------------------------------------------------


def test_a_value_held_by_two_facts_reports_both():
    """The claim verified is that the figure came from a tool, not which one.

    Reporting both is honest; guessing one would invent provenance.
    """
    ledger = FactLedger()
    ledger.register("flags", "detect_cashflow_flags", {"overdraft_count": 3})
    ledger.register("risk", "score_risk", {"risk_score": 3})
    assert set(ledger.resolve(3.0)) == {"flags.overdraft_count", "risk.risk_score"}


# --- call accounting ------------------------------------------------------------------


def test_call_index_distinguishes_repeat_calls(ledger):
    ledger.register("revenue", "compute_revenue_metrics", {"avg_monthly_revenue": 100.0})
    ledger.register("revenue", "compute_revenue_metrics", {"avg_monthly_revenue": 200.0})
    assert ledger.call_count == 2
    assert [e.call_index for e in ledger.entries] == [1, 2]
    assert ledger.get("revenue.avg_monthly_revenue").value == 200.0, "latest wins"


def test_register_returns_what_became_quotable(ledger):
    added = ledger.register("volatility", "compute_volatility", {"revenue_cv": 0.07, "stability_score": 95})
    assert {entry.key for entry in added} == {"volatility.revenue_cv", "volatility.stability_score"}


def test_snapshot_is_a_plain_dict(ledger):
    ledger.register("offer", "compute_offer", {"advance_amount": 77_000.0})
    assert ledger.snapshot() == {"offer.advance_amount": 77_000.0}


def test_an_empty_ledger_resolves_nothing(ledger):
    assert len(ledger) == 0
    assert ledger.resolve(1.0) == ()
    assert ledger.get("anything") is None


# --- the magnitude condition on the ladder (FR-018) -----------------------------------


def test_a_coarse_rung_does_not_apply_to_a_small_value():
    """Read literally, the ladder let a number be rounded into a different number.

    Nearest-10 of an overdraft count of 3 is zero, so "no overdrafts" resolved against a
    count of three. Nearest-10 of a repayment rate of 13 is ten, so "repaid at 10%"
    resolved against a rate of thirteen. Both are material misstatements that passed.
    """
    assert 0.0 not in rounding_candidates(3)
    assert 10.0 not in rounding_candidates(13.0)
    assert 100.0 not in rounding_candidates(95)
    assert 0.0 not in rounding_candidates(0.4199)


def test_the_recorded_value_always_resolves():
    """Even when every rung is excluded, the figure quoted exactly must still ground."""
    for value in (0, 3, 13.0, 0.4199, 1.9886):
        assert float(value) in rounding_candidates(value), value


def test_zero_still_resolves_against_a_real_zero():
    ledger = FactLedger()
    ledger.register("flags", "detect_cashflow_flags", {"overdraft_count": 0})
    assert ledger.resolve(0.0) == ("flags.overdraft_count",)


def test_zero_no_longer_resolves_against_a_non_zero_fact():
    """The defect this condition exists to close."""
    ledger = FactLedger()
    ledger.register("flags", "detect_cashflow_flags", {"overdraft_count": 3})
    ledger.register("volatility", "compute_volatility", {"revenue_cv": 0.4199})
    assert ledger.resolve(0.0) == ()


def test_large_values_keep_every_rung():
    """The condition must not cost readability where rounding is genuinely sensible."""
    candidates = rounding_candidates(47_812.34)
    for allowed in (47_812.0, 47_810.0, 47_800.0, 48_000.0, 47_812.34):
        assert allowed in candidates, allowed


def test_a_small_decimal_keeps_its_one_decimal_rung():
    """1.9886 loses nearest-1 but keeps one decimal place, so "2.0%" still resolves."""
    candidates = rounding_candidates(1.9886)
    assert 2.0 in candidates
    assert 0.0 not in candidates


@pytest.mark.parametrize(
    "value,unit,rounded,applies",
    [
        # Values chosen so the rung under test produces a result no *other* rung produces.
        # 999 would be useless here: nearest-100 is excluded, but nearest-10 of 999 is also
        # 1000, so the value stays reachable and the assertion proves nothing.
        (1_040.0, 100, 1_000.0, True),    # at least ten times the unit
        (940.0, 100, 900.0, False),       # under ten times the unit
        (104.0, 10, 100.0, True),
        (94.0, 10, 90.0, False),
        (10.4, 1, 10.0, True),
        (9.4, 1, 9.0, False),
    ],
)
def test_the_boundary_is_ten_times_the_unit(value, unit, rounded, applies):
    assert float(round(value, -len(str(unit)) + 1)) == rounded, "fixture arithmetic"
    present = rounded in rounding_candidates(value)
    assert present is applies, (
        f"{value}: nearest-{unit} rung should {'apply' if applies else 'not apply'}"
    )


def test_the_magnitude_multiple_is_declared_in_policy():
    assert policy.GROUNDING_MIN_MAGNITUDE_MULTIPLE == 10
