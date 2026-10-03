"""Revenue metrics (FR-004).

Two kinds of check, and the difference matters.

Hand-computed fixtures verify the *arithmetic*: small inputs whose answers were worked out
on paper, where neither the tool nor the generator gets a vote. Ground-truth comparison
verifies *recovery*: that the tool pulls the same figures out of a real CSV that the
generator put into it. Ground truth cannot catch a wrong formula, because both sides would
use it; a fixture cannot catch a month-grouping bug on realistic data. Both are needed.
"""

import pytest

from ledgermind.tools.revenue import compute_revenue_metrics


def _tx(date, amount, balance=0.0, description="x"):
    return {"date": date, "amount": amount, "description": description, "balance": balance}


# --- hand-computed arithmetic ---------------------------------------------------------


def test_revenue_sums_only_inflows():
    """Outflows belong to the cashflow analysis; revenue is cash that arrived."""
    metrics = compute_revenue_metrics(
        [
            _tx("2026-01-05", 10_000.0),
            _tx("2026-01-11", 5_000.0),
            _tx("2026-01-20", -3_000.0),
            _tx("2026-01-28", -500.0),
        ]
    )
    assert metrics["monthly_revenue"] == [("2026-01", 15_000.0)]
    assert metrics["total_revenue"] == 15_000.0
    assert metrics["avg_monthly_revenue"] == 15_000.0


def test_flat_series_has_no_growth_and_a_known_average():
    months = ["2026-01", "2026-02", "2026-03", "2026-04"]
    metrics = compute_revenue_metrics([_tx(f"{m}-10", 20_000.0) for m in months])
    assert metrics["avg_monthly_revenue"] == 20_000.0
    assert metrics["total_revenue"] == 80_000.0
    assert metrics["months_covered"] == 4
    assert metrics["mom_growth_pct"] == pytest.approx(0.0, abs=1e-9)
    assert metrics["trend"] == "flat"


def test_geometric_series_recovers_its_exact_growth_rate():
    """10,000 growing at exactly 5% a month must report exactly 5%."""
    amounts = [10_000.0 * 1.05**i for i in range(6)]
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    metrics = compute_revenue_metrics([_tx(f"{m}-10", a) for m, a in zip(months, amounts)])
    assert metrics["mom_growth_pct"] == pytest.approx(5.0, abs=1e-4)
    assert metrics["trend"] == "rising"


def test_declining_series_is_labelled_declining():
    amounts = [10_000.0 * 0.94**i for i in range(6)]
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    metrics = compute_revenue_metrics([_tx(f"{m}-10", a) for m, a in zip(months, amounts)])
    assert metrics["mom_growth_pct"] == pytest.approx(-6.0, abs=1e-4)
    assert metrics["trend"] == "declining"


def test_month_with_no_inflows_is_kept_as_zero():
    """A month the business banked nothing is a signal, not a gap to skip.

    Dropping it would shorten the history, raise the average, and understate variation --
    three wrong answers from one omission.
    """
    metrics = compute_revenue_metrics(
        [
            _tx("2026-01-10", 20_000.0),
            _tx("2026-02-10", -5_000.0),
            _tx("2026-03-10", 20_000.0),
        ]
    )
    assert metrics["monthly_revenue"] == [
        ("2026-01", 20_000.0),
        ("2026-02", 0.0),
        ("2026-03", 20_000.0),
    ]
    assert metrics["months_covered"] == 3
    assert metrics["avg_monthly_revenue"] == pytest.approx(13_333.33, abs=0.01)


def test_year_boundary_is_handled():
    metrics = compute_revenue_metrics(
        [_tx("2025-11-10", 1000.0), _tx("2025-12-10", 1000.0), _tx("2026-01-10", 1000.0)]
    )
    assert [m for m, _ in metrics["monthly_revenue"]] == ["2025-11", "2025-12", "2026-01"]


def test_period_bounds_are_the_actual_first_and_last_dates():
    metrics = compute_revenue_metrics(
        [_tx("2026-01-03", 100.0), _tx("2026-01-17", 100.0), _tx("2026-02-28", -50.0)]
    )
    assert metrics["period_start"] == "2026-01-03"
    assert metrics["period_end"] == "2026-02-28"


def test_empty_input_is_refused():
    with pytest.raises(ValueError, match="at least one transaction"):
        compute_revenue_metrics([])


# --- recovery from a real CSV, against ground truth -----------------------------------


@pytest.mark.parametrize(
    "merchant_id",
    ["m02_healthy_mid", "m03_healthy_large", "m09_volatile_badmonth", "m14_declining_distress"],
)
def test_matches_ground_truth(merchants, merchant_id):
    data = merchants[merchant_id]
    metrics = compute_revenue_metrics(data["inputs"]["transactions"])
    truth = data["truth"]

    assert metrics["months_covered"] == truth["true_months_covered"]
    assert metrics["avg_monthly_revenue"] == pytest.approx(truth["true_avg_monthly_revenue"], abs=0.02)
    assert metrics["total_revenue"] == pytest.approx(truth["true_total_revenue"], abs=0.05)
    assert metrics["mom_growth_pct"] == pytest.approx(truth["true_mom_growth_pct"], abs=0.01)

    for (tool_month, tool_amount), (true_month, true_amount) in zip(
        metrics["monthly_revenue"], truth["true_monthly_revenue"]
    ):
        assert tool_month == true_month
        assert tool_amount == pytest.approx(true_amount, abs=0.02)
