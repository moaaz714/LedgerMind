"""Sales reconciliation (T044, FR-034)."""

import pytest

from ledgermind import policy
from ledgermind.tools.reconcile import reconcile_sales

NET = 1 - policy.EXPECTED_PROCESSOR_FEE_PCT / 100


def _tx(date, amount, balance=10_000.0, description="card settlement"):
    return {"date": date, "amount": amount, "description": description, "balance": balance}


def _sale(date, amount):
    return {"date": date, "amount": amount}


# --- hand-computed arithmetic ---------------------------------------------------------


def test_a_perfectly_reconciling_merchant():
    """1,000 of sales should bank 975 at a 2.5% fee. Worked out by hand."""
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0), _tx("2026-02-10", 975.0)],
        [_sale("2026-01-05", 1_000.0), _sale("2026-02-05", 1_000.0)],
    )
    assert result["sales_total"] == 2_000.0
    assert result["expected_banked_total"] == pytest.approx(1_950.0)
    assert result["banked_total"] == 1_950.0
    assert result["reconciliation_ratio"] == pytest.approx(1.0)
    assert result["period_ratio"] == pytest.approx(1.0)
    assert result["months_compared"] == 2
    assert result["mismatched_month_count"] == 0
    assert result["max_month_gap_pct"] == pytest.approx(0.0)
    assert result["reconciled"] is True


def test_overstated_sales_are_caught():
    """Sales claim twice what was banked."""
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0), _tx("2026-02-10", 975.0)],
        [_sale("2026-01-05", 2_000.0), _sale("2026-02-05", 2_000.0)],
    )
    assert result["reconciliation_ratio"] == pytest.approx(0.5)
    assert result["reconciled"] is False


def test_deposits_overshooting_sales_are_caught():
    """Reconciliation is two-sided: more banked than sales explain is also unexplained."""
    result = reconcile_sales(
        [_tx("2026-01-10", 1_950.0), _tx("2026-02-10", 1_950.0)],
        [_sale("2026-01-05", 1_000.0), _sale("2026-02-05", 1_000.0)],
    )
    assert result["reconciliation_ratio"] == pytest.approx(2.0)
    assert result["reconciled"] is False


def test_the_tolerance_is_applied_in_both_directions():
    inside = policy.RECONCILIATION_TOLERANCE_PCT / 100 * 0.9
    for direction in (1 + inside, 1 - inside):
        result = reconcile_sales(
            [_tx("2026-01-10", 975.0 * direction)], [_sale("2026-01-05", 1_000.0)]
        )
        assert result["reconciled"] is True, direction

    outside = policy.RECONCILIATION_TOLERANCE_PCT / 100 * 1.5
    for direction in (1 + outside, 1 - outside):
        result = reconcile_sales(
            [_tx("2026-01-10", 975.0 * direction)], [_sale("2026-01-05", 1_000.0)]
        )
        assert result["reconciled"] is False, direction


# --- the month union, and months present in only one record --------------------------


def test_months_come_from_the_union_never_the_overlap():
    """Trimming to the overlap would let a merchant whose sales stopped being banked look
    reconciled by having those months quietly dropped."""
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0)],
        [_sale("2026-01-05", 1_000.0), _sale("2026-02-05", 1_000.0), _sale("2026-03-05", 1_000.0)],
    )
    assert result["months_compared"] == 3


def test_sales_with_nothing_banked_is_a_total_mismatch():
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0)],
        [_sale("2026-01-05", 1_000.0), _sale("2026-02-05", 1_000.0)],
    )
    # One month at 1.0, one at 0.0 -- the median of two values is their mean, 0.5.
    assert result["reconciliation_ratio"] == pytest.approx(0.5)
    assert result["mismatched_month_count"] == 1
    assert result["max_month_gap_pct"] == pytest.approx(100.0)
    assert result["reconciled"] is False


def test_banked_with_no_sales_uses_the_declared_unmatched_ratio():
    """A month with no sales has no divisor, so it records a full mismatch rather than
    infinity -- a number the median can work with and a memo can quote."""
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0), _tx("2026-02-10", 5_000.0)],
        [_sale("2026-01-05", 1_000.0)],
    )
    assert result["months_compared"] == 2
    assert result["mismatched_month_count"] == 1
    assert result["reconciled"] is False


def test_a_month_with_neither_is_not_a_disagreement():
    """Both records empty for a month means nothing to reconcile, not a failure."""
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0), _tx("2026-03-10", 975.0)],
        [_sale("2026-01-05", 1_000.0), _sale("2026-03-05", 1_000.0)],
    )
    assert result["months_compared"] == 2, "February appears in neither record"
    assert result["reconciled"] is True


# --- one-off inflows ------------------------------------------------------------------


def test_a_large_one_off_does_not_count_as_banked_revenue():
    """An asset sale has no counterpart in a sales export, so a merchant must not be
    refused for selling a van."""
    routine = [_tx(f"2026-0{m}-10", 975.0) for m in range(1, 7)]
    sales = [_sale(f"2026-0{m}-05", 1_000.0) for m in range(1, 7)]

    clean = reconcile_sales(routine, sales)
    with_van = reconcile_sales(routine + [_tx("2026-03-20", 90_000.0, description="equipment sale")], sales)

    assert clean["reconciled"] is True
    assert with_van["reconciled"] is True, "the van must not cause a refusal"
    assert with_van["banked_total"] == clean["banked_total"], "the van is not banked revenue"


def test_the_median_survives_a_minority_of_distorted_months():
    """The reason the decision uses a median and not the period totals.

    Excluding one-off inflows distorts only the months containing them. Measured on period
    totals that distortion hit one honest merchant for 18.2% against an 8% tolerance.
    """
    months = [f"2026-{m:02d}" for m in range(1, 13)]
    transactions = [_tx(f"{m}-10", 975.0) for m in months]
    sales = [_sale(f"{m}-05", 1_000.0) for m in months]
    # Two months also received a large one-off, which is excluded from the banked side.
    transactions += [_tx("2026-03-20", 80_000.0), _tx("2026-07-20", 80_000.0)]

    result = reconcile_sales(transactions, sales)
    assert result["reconciliation_ratio"] == pytest.approx(1.0), "the median ignores the two"
    assert result["reconciled"] is True


# --- shape and refusals ---------------------------------------------------------------


def test_the_tolerance_applied_is_reported():
    """Returned so a decline memo can cite the bar it was judged against and still ground."""
    result = reconcile_sales([_tx("2026-01-10", 975.0)], [_sale("2026-01-05", 1_000.0)])
    assert result["reconciliation_tolerance_pct"] == policy.RECONCILIATION_TOLERANCE_PCT


def test_empty_inputs_are_refused():
    with pytest.raises(ValueError, match="at least one transaction"):
        reconcile_sales([], [_sale("2026-01-05", 1.0)])
    with pytest.raises(ValueError, match="at least one sale"):
        reconcile_sales([_tx("2026-01-10", 1.0)], [])


def test_outflows_are_not_banked_revenue():
    result = reconcile_sales(
        [_tx("2026-01-10", 975.0), _tx("2026-01-15", -500.0, description="rent")],
        [_sale("2026-01-05", 1_000.0)],
    )
    assert result["banked_total"] == 975.0


# --- against ground truth -------------------------------------------------------------


@pytest.mark.parametrize(
    "merchant_id", ["m02_healthy_mid", "m04_healthy_oneoff", "m14_declining_distress",
                    "m21_recon_overstated", "m22_recon_understated"]
)
def test_matches_ground_truth(merchants, merchant_id):
    data = merchants[merchant_id]
    result = reconcile_sales(data["inputs"]["transactions"], data["inputs"]["sales"])
    truth = data["truth"]
    assert result["sales_total"] == pytest.approx(truth["true_total_sales"], abs=0.05)
    assert result["reconciliation_ratio"] == pytest.approx(truth["true_reconciliation_ratio"], abs=0.02)
    assert result["reconciled"] is truth["true_reconciles"]
