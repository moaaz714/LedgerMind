"""Cashflow flag detection (FR-006)."""

import pytest

from ledgermind.tools.flags import detect_cashflow_flags


def _tx(day, amount, balance, description="x"):
    return {
        "date": f"2026-01-{day:02d}",
        "amount": float(amount),
        "description": description,
        "balance": float(balance),
    }


# A 20-row statement whose answers were walked through by hand. The balance column is the
# running total, two crossings go below zero, one payment is returned, and one inflow is an
# order of magnitude above the rest.
HAND_WALKED = [
    _tx(1, +3_000, 3_000),
    _tx(2, -4_000, -1_000, "rent"),            # crossing 1
    _tx(3, +3_000, 2_000),
    _tx(4, -500, 1_500),
    _tx(5, +3_000, 4_500),
    _tx(6, -5_000, -500, "payroll"),           # crossing 2
    _tx(7, +3_000, 2_500),
    _tx(8, -200, 2_300, "returned direct debit - insufficient funds"),
    _tx(9, +3_000, 5_300),
    _tx(10, -500, 4_800),
    _tx(11, +3_000, 7_800),
    _tx(12, -500, 7_300),
    _tx(13, +3_000, 10_300),
    _tx(14, -500, 9_800),
    _tx(15, +3_000, 12_800),
    _tx(16, -500, 12_300),
    _tx(17, +3_000, 15_300),
    _tx(18, +50_000, 65_300, "equipment sale"),
    _tx(19, +3_000, 68_300),
    _tx(20, -500, 67_800),
]


def test_hand_walked_statement():
    flags = detect_cashflow_flags(HAND_WALKED)
    assert flags["overdraft_count"] == 2
    assert flags["bounced_payment_count"] == 1
    assert flags["large_one_off_count"] == 1
    assert flags["large_one_offs"][0]["amount"] == 50_000.0
    assert flags["large_one_offs"][0]["description"] == "equipment sale"
    assert flags["min_balance"] == -1_000.0
    assert flags["declining_balance"] is False
    assert flags["flag_count"] == 4  # 2 overdrafts + 1 bounced + 1 one-off + 0


def test_staying_overdrawn_is_one_episode_not_many():
    """The bug this definition exists to prevent.

    Counting rows with a negative balance, this statement would report six overdrafts. It
    is one occasion: the account went under once and stayed there.
    """
    rows = [_tx(1, +1_000, 1_000)] + [_tx(day, -500, -500 * (day - 1)) for day in range(2, 8)]
    flags = detect_cashflow_flags(rows)
    assert flags["overdraft_count"] == 1


def test_recovering_and_dipping_again_is_two_episodes():
    rows = [
        _tx(1, +1_000, 1_000),
        _tx(2, -1_500, -500),
        _tx(3, +2_000, 1_500),
        _tx(4, -2_000, -500),
    ]
    assert detect_cashflow_flags(rows)["overdraft_count"] == 2


def test_no_flags_is_zero_not_absent():
    """An empty result must be a recorded count of zero (spec edge case).

    A missing fact cannot be grounded, so a memo saying "no overdrafts" would have nothing
    to resolve against.
    """
    rows = [_tx(day, +1_000, 1_000 * day) for day in range(1, 13)]
    flags = detect_cashflow_flags(rows)
    assert flags["overdraft_count"] == 0
    assert flags["bounced_payment_count"] == 0
    assert flags["large_one_off_count"] == 0
    assert flags["flag_count"] == 0
    assert flags["large_one_offs"] == []


def test_every_bounced_keyword_is_detected():
    from ledgermind import policy

    for keyword in policy.BOUNCED_PAYMENT_KEYWORDS:
        rows = [_tx(1, +1_000, 1_000), _tx(2, -100, 900, f"payment {keyword} by bank")]
        assert detect_cashflow_flags(rows)["bounced_payment_count"] == 1, keyword


def test_keyword_match_is_case_insensitive():
    rows = [_tx(1, +1_000, 1_000), _tx(2, -100, 900, "DD RETURNED - Insufficient Funds")]
    assert detect_cashflow_flags(rows)["bounced_payment_count"] == 1


def test_declining_balance_uses_thirds_not_endpoints():
    """Compares the first third's average against the last third's.

    Endpoint comparison would let one large payment on the final day decide it.
    """
    rows = [_tx(day, -500, 12_000 - 900 * day) for day in range(1, 13)]
    assert detect_cashflow_flags(rows)["declining_balance"] is True

    steady = [_tx(day, +100, 10_000) for day in range(1, 13)]
    assert detect_cashflow_flags(steady)["declining_balance"] is False


def test_too_few_inflows_reports_no_one_offs_rather_than_guessing():
    """With fewer than four inflows there is no distribution to call an outlier against."""
    rows = [_tx(1, +1_000, 1_000), _tx(2, +90_000, 91_000), _tx(3, -500, 90_500)]
    assert detect_cashflow_flags(rows)["large_one_off_count"] == 0


def test_empty_input_is_refused():
    with pytest.raises(ValueError, match="at least one transaction"):
        detect_cashflow_flags([])


# --- against ground truth -------------------------------------------------------------


@pytest.mark.parametrize(
    "merchant_id",
    ["m02_healthy_mid", "m08_volatile_high", "m11_volatile_bounced", "m14_declining_distress"],
)
def test_matches_ground_truth(merchants, merchant_id):
    data = merchants[merchant_id]
    flags = detect_cashflow_flags(data["inputs"]["transactions"])
    truth = data["truth"]
    assert flags["overdraft_count"] == truth["true_overdraft_count"]
    assert flags["bounced_payment_count"] == truth["true_bounced_payment_count"]
    assert flags["large_one_off_count"] == truth["true_large_one_off_count"]
