"""Every bound, threshold and band that governs a decision.

Constitution Article IV: policy constants live in a single module, and an inline numeric
literal in scoring or offer logic is a defect. This file is that module. If a number
changes an outcome -- a tier boundary, an advance multiple, a rejection tolerance -- it
belongs here and nowhere else.

The band-lookup helpers at the bottom are here deliberately rather than in the tools.
A threshold and its interpretation are one thing: `compute_volatility` needs a stability
score from the coefficient of variation and `score_risk` needs risk points from the same
value, so putting the lookup in either one would force the other to restate the bands and
the two could then disagree about what "CV at or below 0.20" means.

Amounts are unitless. The spec assumes a single currency with no FX, and the scale below
assumes average monthly revenue roughly in the 20,000-120,000 range.
"""

from dataclasses import dataclass
from math import inf

POLICY_VERSION = "1.0.0"


# --------------------------------------------------------------------------------------
# Risk score: 0-100, where HIGHER MEANS MORE RISK.
#
# Stated loudly because a sign error here is silent. Flip the direction and riskier
# merchants receive larger advances, every test still passes, and SC-001 (monotonicity)
# is the only thing that would catch it.
# --------------------------------------------------------------------------------------

RISK_WEIGHT_VOLATILITY = 40
RISK_WEIGHT_TREND = 25
RISK_WEIGHT_FLAGS = 25
RISK_WEIGHT_HISTORY = 10

# Volatility carries the most weight because the lender's primary exposure in
# revenue-based financing is duration extension, not default. Repayment is a share of
# revenue, so when revenue swings the repayment period swings with it: the lender is
# eventually paid in full but over a longer window, and the return on that capital falls.
# A merchant with steady revenue is worth more than a larger, erratic one.

# (coefficient of variation upper bound, (stability_score, risk_points))
# One table, two outputs, so the thresholds cannot drift apart.
VOLATILITY_BANDS = (
    (0.10, (95, 0)),
    (0.20, (80, 10)),
    (0.35, (60, 22)),
    (0.50, (35, 32)),
    (inf, (15, 40)),
)

# (month-over-month growth percent upper bound, risk_points)
TREND_BANDS = (
    (-8.0, 25),   # steep decline
    (-2.0, 18),   # declining
    (2.0, 10),    # flat
    (inf, 0),     # rising
)

# (months of history upper bound, risk_points). Below MIN_MONTHS_HISTORY the merchant is
# declined outright rather than scored, so these bands start at that floor -- which means
# 6-8 months is the worst history a scored merchant can have, and must therefore carry the
# full RISK_WEIGHT_HISTORY. An earlier draft gave it 8 of 10, which quietly capped the
# worst possible risk score at 98 and made the weight a number that described nothing.
HISTORY_BANDS = (
    (8, 10),
    (11, 5),
    (inf, 0),
)

# Cashflow flags. The per-flag maxima sum to exactly RISK_WEIGHT_FLAGS; test_policy asserts it.
OVERDRAFT_RISK_POINTS_EACH = 3
OVERDRAFT_RISK_POINTS_MAX = 9
BOUNCED_PAYMENT_RISK_POINTS_EACH = 5
BOUNCED_PAYMENT_RISK_POINTS_MAX = 10
DECLINING_BALANCE_RISK_POINTS = 6


# --------------------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------------------

# (risk score upper bound, tier). None means declined.
TIER_BANDS = (
    (19, "A"),
    (39, "B"),
    (59, "C"),
    (74, "D"),
    (inf, None),
)

DECLINE_RISK_SCORE = 75


@dataclass(frozen=True)
class TierTerms:
    """Offer terms for one tier.

    advance_multiple -- the advance as a multiple of average monthly revenue
    repayment_pct    -- the share of each month's revenue diverted to repayment
    factor_rate      -- total repayable divided by the advance; the lender's entire fee
    """

    advance_multiple: float
    repayment_pct: float
    factor_rate: float

    @property
    def implied_duration_months(self) -> float:
        """Months to settle at steady revenue, from the terms alone.

        The advance and the monthly payment are both multiples of monthly revenue, so
        revenue cancels and duration depends only on these three numbers. That makes the
        whole table statically verifiable: test_policy asserts every tier lands inside
        MIN_DURATION_MONTHS..MAX_DURATION_MONTHS without generating a merchant.
        """
        return self.advance_multiple * self.factor_rate / self.repayment_pct


# Better merchants get more money, cheaper, over longer. Riskier merchants get less,
# priced higher, repaid faster -- the larger repayment share is duration insurance, since
# duration extension is the thing that actually goes wrong.
#
# advance_multiple is strictly decreasing A -> D, which is what makes SC-001 structural:
# more volatility raises the risk score, which can only worsen the tier, which can only
# shrink the advance. The ordering is asserted in test_policy.
TIER_TERMS = {
    "A": TierTerms(advance_multiple=1.35, repayment_pct=0.13, factor_rate=1.12),
    "B": TierTerms(advance_multiple=1.15, repayment_pct=0.14, factor_rate=1.18),
    "C": TierTerms(advance_multiple=0.90, repayment_pct=0.15, factor_rate=1.26),
    "D": TierTerms(advance_multiple=0.60, repayment_pct=0.16, factor_rate=1.35),
}


# --------------------------------------------------------------------------------------
# Offer bounds
# --------------------------------------------------------------------------------------

# Concentration limit: no single merchant exceeds this regardless of revenue. Set so it
# actually engages near the top of the expected revenue range -- a cap that can never bind
# would leave `advance_cap_applied` permanently false and untestable.
ADVANCE_CAP_ABSOLUTE = 150_000.0

# Below this, origination and servicing make the advance uneconomic: decline explicitly
# rather than offer an amount nobody would write.
MIN_VIABLE_ADVANCE = 5_000.0

# Advances round DOWN to this unit. Down, not nearest: never lend more than the formula
# allows, and the conservative direction is the defensible one.
ADVANCE_ROUNDING_UNIT = 100

MIN_MONTHS_HISTORY = 6
MIN_DURATION_MONTHS = 3
MAX_DURATION_MONTHS = 12


# --------------------------------------------------------------------------------------
# Detection thresholds
# --------------------------------------------------------------------------------------

# An inflow above this fraction of average monthly revenue is a large one-off. Reported,
# but not excluded from the revenue basis -- see the spec's Assumptions for why that
# simplification was chosen and what it costs.
LARGE_ONE_OFF_REVENUE_FRACTION = 0.25

# A balance strictly below this is an overdraft. Defined on the balance itself rather than
# on description keywords, so detection stays deterministic.
OVERDRAFT_BALANCE_THRESHOLD = 0.0

BOUNCED_PAYMENT_KEYWORDS = frozenset(
    {"returned", "bounced", "nsf", "insufficient funds", "failed payment"}
)

# Balance is declining when the final third of the period averages below this fraction of
# the first third's average.
DECLINING_BALANCE_FRACTION = 0.75


# --------------------------------------------------------------------------------------
# Verification bounds (FR-018, FR-023)
#
# These govern rejection rather than underwriting, but they are still numbers that decide
# an outcome, so Article IV puts them here too rather than in a second policy module.
# --------------------------------------------------------------------------------------

# A numeral in generated prose grounds if it equals a recorded value rounded to one of
# these units, or to GROUNDING_DECIMAL_PLACES. Nothing beyond this ladder is permitted.
GROUNDING_ROUNDING_LADDER = (1, 10, 100, 1000)
GROUNDING_DECIMAL_PLACES = 1

MAX_REGENERATION_ATTEMPTS = 3


# --------------------------------------------------------------------------------------
# Band lookups
# --------------------------------------------------------------------------------------


def _resolve_band(value, bands):
    """Return the payload of the first band whose upper bound `value` does not exceed.

    Every band table ends with an `inf` bound, so the loop always returns.
    """
    for upper, payload in bands:
        if value <= upper:
            return payload
    raise AssertionError("band table has no catch-all bound")  # pragma: no cover


def volatility_band(revenue_cv: float) -> tuple[int, int]:
    """(stability_score, risk_points) for a coefficient of variation."""
    return _resolve_band(revenue_cv, VOLATILITY_BANDS)


def trend_risk_points(mom_growth_pct: float) -> int:
    """Risk points for a month-over-month growth rate, expressed in percent."""
    return _resolve_band(mom_growth_pct, TREND_BANDS)


def history_risk_points(months_covered: int) -> int:
    """Risk points for length of history, at or above MIN_MONTHS_HISTORY."""
    return _resolve_band(months_covered, HISTORY_BANDS)


def tier_for_score(risk_score: int) -> str | None:
    """Tier for a risk score, or None when the score means declined."""
    return _resolve_band(risk_score, TIER_BANDS)
