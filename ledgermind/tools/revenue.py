"""Revenue metrics from a bank statement (FR-004).

Pure function, stdlib only, no I/O (Article IV). Field names are the fact ledger's keys --
see data-model.md -- so renaming one changes the verification contract.
"""

from __future__ import annotations

import statistics

from ledgermind import policy
from ledgermind.tools import trend


def _month_key(iso_date: str) -> str:
    return iso_date[:7]


def _months_between(first: str, last: str) -> list[str]:
    """Every month from `first` to `last` inclusive, as YYYY-MM.

    Months with no inflows are included with zero revenue rather than skipped. A gap is a
    real signal -- a month the business banked nothing -- and dropping it would quietly
    shorten the history, raise the average, and understate the variation.
    """
    year, month = int(first[:4]), int(first[5:7])
    end_year, end_month = int(last[:4]), int(last[5:7])
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def compute_revenue_metrics(transactions: list[dict]) -> dict:
    """Monthly revenue and the measures derived from it.

    Revenue is the sum of *positive* amounts: cash that arrived. Outflows are the expense
    side and belong to the cashflow analysis, not to revenue.
    """
    if not transactions:
        raise ValueError("compute_revenue_metrics requires at least one transaction")

    inflow_by_month: dict[str, float] = {}
    for row in transactions:
        if row["amount"] > 0:
            key = _month_key(row["date"])
            inflow_by_month[key] = round(inflow_by_month.get(key, 0.0) + row["amount"], 2)

    dates = [row["date"] for row in transactions]
    months = _months_between(_month_key(min(dates)), _month_key(max(dates)))
    monthly_revenue = [(month, inflow_by_month.get(month, 0.0)) for month in months]
    series = [amount for _, amount in monthly_revenue]

    growth = trend.log_linear_growth_pct(series)
    return {
        "monthly_revenue": monthly_revenue,
        "avg_monthly_revenue": round(statistics.fmean(series), 2),
        "total_revenue": round(sum(series), 2),
        "mom_growth_pct": round(growth, 4),
        "months_covered": len(monthly_revenue),
        "period_start": min(dates),
        "period_end": max(dates),
        "trend": policy.trend_label(growth),
    }
