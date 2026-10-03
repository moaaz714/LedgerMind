"""Static verification of the policy table.

These tests need no merchant, no tool and no model -- the policy table alone is enough to
check its own internal consistency. That is possible because of one identity: the advance
and the monthly payment are both multiples of monthly revenue, so revenue cancels and a
tier's duration is fully determined by its three numbers.

The failure these guard against is a plausible future edit. Change tier A's repayment
percentage from 13% to 11% and the implied duration goes from 11.6 to 13.8 months, past
the policy maximum -- with nothing in the pipeline objecting, because every merchant would
still be processed happily. MAX_DURATION_MONTHS is a guard, and this is where it fires.
"""

import pytest

from ledgermind import policy

TIERS = ("A", "B", "C", "D")


# --- risk score composition -----------------------------------------------------------


def test_risk_weights_sum_to_one_hundred():
    total = (
        policy.RISK_WEIGHT_VOLATILITY
        + policy.RISK_WEIGHT_TREND
        + policy.RISK_WEIGHT_FLAGS
        + policy.RISK_WEIGHT_HISTORY
    )
    assert total == 100


def test_flag_points_cannot_exceed_their_weight():
    """The per-flag maxima must sum to exactly the flag weight.

    Less and the flag component can never reach its share; more and a heavily flagged
    merchant could push the total risk score above 100.
    """
    worst_case = (
        policy.OVERDRAFT_RISK_POINTS_MAX
        + policy.BOUNCED_PAYMENT_RISK_POINTS_MAX
        + policy.DECLINING_BALANCE_RISK_POINTS
    )
    assert worst_case == policy.RISK_WEIGHT_FLAGS


def test_worst_case_risk_score_is_one_hundred():
    worst = (
        policy.VOLATILITY_BANDS[-1][1][1]
        + policy.TREND_BANDS[0][1]
        + policy.RISK_WEIGHT_FLAGS
        + policy.HISTORY_BANDS[0][1]
    )
    assert worst == 100


# --- band tables ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bands",
    [policy.VOLATILITY_BANDS, policy.TREND_BANDS, policy.HISTORY_BANDS, policy.TIER_BANDS],
    ids=["volatility", "trend", "history", "tier"],
)
def test_band_bounds_ascend_and_end_with_a_catch_all(bands):
    bounds = [upper for upper, _ in bands]
    assert bounds == sorted(bounds), "band bounds must ascend for lookup to be correct"
    assert bounds[-1] == float("inf"), "band table must end with a catch-all bound"


def test_volatility_bands_move_in_opposite_directions():
    """More variation must mean a lower stability score and more risk points."""
    stability = [payload[0] for _, payload in policy.VOLATILITY_BANDS]
    risk = [payload[1] for _, payload in policy.VOLATILITY_BANDS]
    assert stability == sorted(stability, reverse=True)
    assert risk == sorted(risk)
    assert all(0 <= s <= 100 for s in stability)


def test_trend_risk_decreases_as_growth_improves():
    points = [p for _, p in policy.TREND_BANDS]
    assert points == sorted(points, reverse=True)


def test_history_risk_decreases_as_history_lengthens():
    points = [p for _, p in policy.HISTORY_BANDS]
    assert points == sorted(points, reverse=True)


# --- tier table -----------------------------------------------------------------------


def test_tier_bands_cover_a_to_d_then_decline():
    assert [tier for _, tier in policy.TIER_BANDS] == ["A", "B", "C", "D", None]


def test_decline_threshold_is_one_past_the_last_tier():
    last_tier_bound = policy.TIER_BANDS[-2][0]
    assert policy.DECLINE_RISK_SCORE == last_tier_bound + 1


def test_tier_for_score_at_every_boundary():
    for upper, expected in policy.TIER_BANDS[:-1]:
        assert policy.tier_for_score(upper) == expected
        assert policy.tier_for_score(upper + 1) != expected
    assert policy.tier_for_score(policy.DECLINE_RISK_SCORE) is None
    assert policy.tier_for_score(100) is None


def test_every_tier_has_terms():
    assert set(policy.TIER_TERMS) == set(TIERS)


# --- offer terms ----------------------------------------------------------------------


def test_advance_multiple_strictly_decreases_across_tiers():
    """This is what makes SC-001 structural rather than hoped for.

    More volatility raises the risk score, a higher score can only worsen the tier, and a
    worse tier can only shrink the advance. Monotonicity holds by construction, so the
    only way to break it is to edit this ordering -- which is exactly what fails here.
    """
    multiples = [policy.TIER_TERMS[t].advance_multiple for t in TIERS]
    assert all(a > b for a, b in zip(multiples, multiples[1:])), multiples


def test_repayment_percentage_does_not_decrease_across_tiers():
    """Riskier merchants repay a larger share, shortening the exposure window."""
    pcts = [policy.TIER_TERMS[t].repayment_pct for t in TIERS]
    assert pcts == sorted(pcts)


def test_factor_rate_strictly_increases_across_tiers():
    """Riskier merchants are priced higher."""
    factors = [policy.TIER_TERMS[t].factor_rate for t in TIERS]
    assert all(a < b for a, b in zip(factors, factors[1:])), factors


@pytest.mark.parametrize("tier", TIERS)
def test_implied_duration_is_within_policy_bounds(tier):
    """The guard. Verified from the table alone, no merchant required."""
    duration = policy.TIER_TERMS[tier].implied_duration_months
    assert policy.MIN_DURATION_MONTHS <= duration <= policy.MAX_DURATION_MONTHS, (
        f"tier {tier} implies {duration:.2f} months, outside "
        f"{policy.MIN_DURATION_MONTHS}-{policy.MAX_DURATION_MONTHS}"
    )


def test_implied_duration_shortens_as_risk_rises():
    durations = [policy.TIER_TERMS[t].implied_duration_months for t in TIERS]
    assert all(a > b for a, b in zip(durations, durations[1:])), durations


def test_implied_duration_matches_an_independent_calculation():
    """Guards the identity itself, not just the numbers it produces.

    Computed here the long way -- from an actual revenue figure -- so a mistake in the
    `implied_duration_months` property cannot hide behind the property being used on both
    sides of the assertion.
    """
    revenue = 47_812.34
    for tier in TIERS:
        terms = policy.TIER_TERMS[tier]
        advance = terms.advance_multiple * revenue
        owed = advance * terms.factor_rate
        monthly_payment = terms.repayment_pct * revenue
        assert owed / monthly_payment == pytest.approx(terms.implied_duration_months)


# --- offer bounds ---------------------------------------------------------------------


def test_minimum_viable_advance_is_below_the_cap():
    assert policy.MIN_VIABLE_ADVANCE < policy.ADVANCE_CAP_ABSOLUTE


def test_cap_is_reachable_within_the_expected_revenue_range():
    """A cap that can never bind leaves `advance_cap_applied` permanently false.

    The spec's assumed revenue range tops out around 120,000 a month; the best tier's
    multiple applied to that must exceed the cap, or the branch is dead code and T013's
    test of it would be vacuous.
    """
    top_of_range = 120_000.0
    best_multiple = max(policy.TIER_TERMS[t].advance_multiple for t in TIERS)
    assert best_multiple * top_of_range > policy.ADVANCE_CAP_ABSOLUTE


def test_grounding_ladder_is_ascending_powers_of_ten():
    assert policy.GROUNDING_ROUNDING_LADDER == (1, 10, 100, 1000)


def test_regeneration_attempts_are_bounded():
    assert 1 <= policy.MAX_REGENERATION_ATTEMPTS <= 5
