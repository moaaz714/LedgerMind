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

# Both the risk score and the stability score run 0 to this. The tools return it as a
# named fact so a memo can legitimately write "72 out of 100": a bare denominator with no
# recorded value behind it would otherwise be rejected as fabricated. Same principle as
# FR-009 -- a figure the prose will naturally use must exist as a fact.
SCORE_SCALE_MAX = 100

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

# Trend thresholds, named once so the risk bands and the human-readable label cannot
# disagree about where "flat" ends.
TREND_STEEP_DECLINE_PCT = -8.0
TREND_DECLINING_PCT = -2.0
TREND_RISING_PCT = 2.0

# (month-over-month growth percent upper bound, risk_points)
TREND_BANDS = (
    (TREND_STEEP_DECLINE_PCT, 25),
    (TREND_DECLINING_PCT, 18),
    (TREND_RISING_PCT, 10),
    (inf, 0),
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

# An inflow is a large one-off when it exceeds Q3 + this multiple of the interquartile
# range of the merchant's own inflows -- the standard outlier rule, with a wider multiple
# than the usual 1.5 because the concept is a *large* one-off, not a mild outlier.
#
# Two earlier drafts failed, and both failed the same way: they normalised by level while
# ignoring spread. A fraction of average monthly revenue depended on deposit cadence, and a
# multiple of the median inflow depended on how erratic the merchant's deposits were -- it
# reported up to fifteen one-offs for merchants that had none. Q3 + k*IQR adapts to each
# merchant's own distribution, so an erratic merchant needs a bigger outlier to qualify.
#
# Known limit: for a highly erratic merchant no amount-based rule separates "one-off event"
# from "exceptional trading week". This flag carries zero risk points precisely because of
# that -- it informs the memo, it does not price the offer. See the spec's Assumptions.
LARGE_ONE_OFF_IQR_MULTIPLE = 3.0

# A balance strictly below this is overdrawn. Defined on the balance itself rather than on
# description keywords, so detection stays deterministic.
#
# `overdraft_count` counts EPISODES -- crossings from at-or-above this threshold to below
# it -- not rows with a negative balance. A merchant who goes overdrawn and stays there
# would otherwise score one per transaction: in testing, merchants asked for zero and two
# overdrafts reported 96 and 137. "Three overdrafts" means three occasions, which is also
# what an analyst reading the memo will understand it to mean.
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

# A rung applies only where the recorded value is at least this many times its unit.
#
# Without the condition the ladder, read literally, permits a number to be rounded into a
# different number: nearest-10 of an overdraft count of 3 is zero, and nearest-10 of a
# repayment rate of 13 is ten. So "no overdrafts" resolved against a count of three, and
# "repaid at 10%" against a rate of thirteen -- material misstatements that passed. Ten
# bounds the rounding error at 5% of the figure.
GROUNDING_MIN_MAGNITUDE_MULTIPLE = 10

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


def trend_label(mom_growth_pct: float) -> str:
    """Human-readable trend direction, sharing the bands' own boundaries."""
    if mom_growth_pct <= TREND_DECLINING_PCT:
        return "declining"
    if mom_growth_pct <= TREND_RISING_PCT:
        return "flat"
    return "rising"
