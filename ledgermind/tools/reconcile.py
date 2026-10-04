"""Does the sales export agree with the bank statement? (FR-034)

The sales records and the bank records are two independent views of the same trade. A
merchant whose sales export claims revenue that never reached the bank is not a riskier
version of the same business -- they are a business whose numbers we cannot verify, which is
why FR-035 declines them rather than scoring them.

**The decision figure is the median of the monthly ratios, not the ratio of the totals.**
Large one-off inflows are excluded from the banked side, because an asset sale has no
counterpart in a sales export and a merchant should not be refused for selling a van. But
that exclusion uses the rule from FR-006, which is documented as producing false positives
on erratic merchants -- and measured against period totals it cost one honest merchant an
18.2% deviation against an 8% tolerance. Excluding a few months' inflows distorts those
months; a median ignores a minority of distorted months. Meanwhile a sales export that
systematically overstates reality overstates it in *every* month, moving the median by the
full factor. Robust to the noise present, sensitive to the misstatement being sought.

That is a general lesson rather than a local fix: `large_one_off_count` was documented as
inaccurate on erratic merchants and that was acceptable while it carried zero risk points.
The moment it fed a refusal, its known inaccuracy became a wrong decision. A measurement's
tolerable error depends on what consumes it.

Pure function, stdlib only, no I/O (Article IV).
"""

from __future__ import annotations

import statistics

from ledgermind import policy
from ledgermind.tools.flags import one_off_threshold


def _month(iso_date: str) -> str:
    return iso_date[:7]


def _month_ratio(banked: float, expected: float) -> float:
    """One month's banked-to-expected ratio.

    Three cases, all defined: a month with sales and nothing banked is a ratio of zero; a
    month with neither is a ratio of one, there being nothing to disagree about; and a month
    that banked money against no sales has no divisor and is recorded as the declared
    unmatched ratio, so the median has a finite number to work with.
    """
    if expected > 0:
        return banked / expected
    return 1.0 if banked == 0 else policy.RECONCILIATION_UNMATCHED_MONTH_RATIO


def reconcile_sales(transactions: list[dict], sales: list[dict]) -> dict:
    """Compare what the sales records imply should have been banked against what was."""
    if not transactions:
        raise ValueError("reconcile_sales requires at least one transaction")
    if not sales:
        raise ValueError("reconcile_sales requires at least one sale")

    net_of_fee = 1 - policy.EXPECTED_PROCESSOR_FEE_PCT / 100

    inflows = [row["amount"] for row in transactions if row["amount"] > 0]
    threshold = one_off_threshold(inflows)

    banked_by_month: dict[str, float] = {}
    for row in transactions:
        # Excluded here as well as counted in `detect_cashflow_flags`, using that module's
        # own threshold function rather than a second copy of the rule.
        if row["amount"] > 0 and row["amount"] <= threshold:
            key = _month(row["date"])
            banked_by_month[key] = banked_by_month.get(key, 0.0) + row["amount"]

    sales_by_month: dict[str, float] = {}
    for sale in sales:
        key = _month(sale["date"])
        sales_by_month[key] = sales_by_month.get(key, 0.0) + sale["amount"]

    # The union, never the overlap. Trimming to months both records cover would let a
    # merchant whose sales stopped being banked look reconciled by having those months
    # quietly dropped.
    months = sorted(set(banked_by_month) | set(sales_by_month))

    month_ratios = [
        _month_ratio(banked_by_month.get(month, 0.0), sales_by_month.get(month, 0.0) * net_of_fee)
        for month in months
    ]

    sales_total = sum(sales_by_month.values())
    expected_banked_total = sales_total * net_of_fee
    banked_total = sum(banked_by_month.values())

    ratio = statistics.median(month_ratios) if month_ratios else 1.0
    mismatched = sum(
        1
        for month_ratio in month_ratios
        if abs(month_ratio - 1.0) * 100 > policy.RECONCILIATION_MONTH_TOLERANCE_PCT
    )
    max_gap = max((abs(month_ratio - 1.0) * 100 for month_ratio in month_ratios), default=0.0)

    return {
        "sales_total": round(sales_total, 2),
        "expected_banked_total": round(expected_banked_total, 2),
        "banked_total": round(banked_total, 2),
        "reconciliation_ratio": round(ratio, 6),
        # The same figure as a percentage. Returned because prose reaches for "banked 74%
        # of what sales imply" rather than "a ratio of 0.74" -- and 0.74 is two decimal
        # places, which is not a rung on the grounding ladder, so the readable phrasing had
        # no groundable figure behind it until this existed. Same reason the scores return
        # their scale (FR-009).
        "reconciliation_ratio_pct": round(ratio * 100, 4),
        "period_ratio": round(banked_total / expected_banked_total, 6) if expected_banked_total else 0.0,
        "months_compared": len(months),
        "mismatched_month_count": mismatched,
        "max_month_gap_pct": round(max_gap, 4),
        "reconciliation_tolerance_pct": policy.RECONCILIATION_TOLERANCE_PCT,
        "reconciled": abs(ratio - 1.0) * 100 <= policy.RECONCILIATION_TOLERANCE_PCT,
    }
