"""Cashflow risk indicators from a bank statement (FR-006).

Pure function, stdlib only, no I/O (Article IV).

Overdrafts are counted as **episodes** -- crossings from a non-negative balance to a
negative one -- not as rows with a negative balance. Counting rows, a merchant who goes
overdrawn and stays there scores one per transaction: in testing, merchants with two and
zero overdrafts reported 137 and 96. "Three overdrafts" means three occasions, which is
what an analyst reading the memo will take it to mean.
"""

from __future__ import annotations

import statistics

from ledgermind import policy


def _one_off_threshold(inflows: list[float]) -> float:
    """Q3 + k*IQR over the merchant's own inflows (policy.LARGE_ONE_OFF_IQR_MULTIPLE).

    Relative to the merchant's own spread, not to their revenue level. Two earlier
    definitions failed the same way -- a fraction of monthly revenue depended on deposit
    cadence, and a multiple of the median depended on how erratic the deposits were.
    """
    if len(inflows) < 4:
        return float("inf")
    q1, _, q3 = statistics.quantiles(inflows, n=4)
    return q3 + policy.LARGE_ONE_OFF_IQR_MULTIPLE * (q3 - q1)


def _is_declining(balances: list[float]) -> bool:
    """True when the final third averages below a policy fraction of the first third.

    Thirds rather than first-versus-last balance: a single large payment landing on the
    last day would otherwise decide it.
    """
    third = len(balances) // 3
    if third == 0:
        return False
    opening = statistics.fmean(balances[:third])
    closing = statistics.fmean(balances[-third:])
    if opening <= 0:
        return False
    return closing < opening * policy.DECLINING_BALANCE_FRACTION


def detect_cashflow_flags(transactions: list[dict]) -> dict:
    """Overdrafts, bounced payments, balance trend and large one-off inflows."""
    if not transactions:
        raise ValueError("detect_cashflow_flags requires at least one transaction")

    inflows = [row["amount"] for row in transactions if row["amount"] > 0]
    threshold = _one_off_threshold(inflows)

    overdraft_count = 0
    bounced_payment_count = 0
    large_one_offs: list[dict] = []
    was_overdrawn = False

    for row in transactions:
        overdrawn = row["balance"] < policy.OVERDRAFT_BALANCE_THRESHOLD
        if overdrawn and not was_overdrawn:
            overdraft_count += 1
        was_overdrawn = overdrawn

        lowered = row["description"].lower()
        if any(keyword in lowered for keyword in policy.BOUNCED_PAYMENT_KEYWORDS):
            bounced_payment_count += 1

        if row["amount"] > threshold:
            large_one_offs.append(
                {
                    "date": row["date"],
                    "amount": row["amount"],
                    "description": row["description"],
                }
            )

    declining_balance = _is_declining([row["balance"] for row in transactions])

    return {
        "overdraft_count": overdraft_count,
        "bounced_payment_count": bounced_payment_count,
        "large_one_off_count": len(large_one_offs),
        # Every kind, with the boolean counting as one. Exists so a memo sentence like
        # "four cashflow flags" has a fact to resolve against (FR-009, FR-019).
        "flag_count": (
            overdraft_count
            + bounced_payment_count
            + len(large_one_offs)
            + (1 if declining_balance else 0)
        ),
        "min_balance": round(min(row["balance"] for row in transactions), 2),
        "large_one_offs": large_one_offs,
        "declining_balance": declining_balance,
    }
