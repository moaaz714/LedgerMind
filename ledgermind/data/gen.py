"""Synthetic merchant generation with known ground truth.

Why synthetic data is not a convenience here: ground truth is the measuring instrument
(Article III). "The system does not invent figures" is unfalsifiable without a
known-correct answer to compare against, and real merchant data cannot supply one.

**How circularity is avoided.** This module *constructs* each month's revenue, so it
records the answer it built rather than re-deriving it. `truth.json` says "I decided
2026-01 would hold 44,200 and emitted transactions summing to that." The tools must then
independently recover 44,200 by parsing the CSV. Agreement means something because the two
figures arrive from opposite directions.

What truth.json cannot validate is a *formula*: it cannot check the coefficient-of-variation
calculation, because both sides would use the same one. That is what the hand-computed
fixtures in tests/test_volatility.py are for. Two different checks -- truth validates
recovery from the files, fixtures validate the arithmetic.

**Coverage is by construction, not by luck.** MERCHANT_SPECS is an explicit table rather
than randomised generation, so the set is guaranteed to exercise every tier, the decline
path, the advance cap, insufficient history, a near-zero revenue month, large one-offs,
overdrafts, bounced payments and declining balances -- plus three monotonicity pairs that
differ only in volatility (SC-001).

**Known limit.** For the most erratic merchants (residual variation above about 0.4) the
realised large-one-off count exceeds the requested one, because no amount-based rule
separates "a one-off event" from "an exceptional trading week" when weeks genuinely vary
several-fold. Three of twenty are affected. The flag carries zero risk points precisely
because of this: it informs the memo, it does not price the offer.

Revenue means net cash banked. Sales are individual; deposits batch them with a
payment-processor fee deducted, so gross sales slightly exceed banked revenue. Net is the
right basis because repayment is taken from cash that actually arrives.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ledgermind import policy
from ledgermind.data import load

# --------------------------------------------------------------------------------------
# Generation constants.
#
# These shape synthetic data; they do not govern an underwriting decision, so Article IV
# does not place them in policy.py. The line is whether changing the number changes an
# offer: a deposit batching interval does not, an advance multiple does.
# --------------------------------------------------------------------------------------

PROCESSOR_FEE_PCT = 0.025

# Weekly settlement windows, aligned to the calendar rather than rolling.
# Rolling 3-day batches over uniformly random sale dates made deposit size depend on how
# many sales a window happened to catch: on a merchant whose monthly revenue varied by 6%,
# deposits ranged from 2,340 to 64,912. No outlier rule can separate a one-off event from a
# busy window while routine windows themselves vary 28x.
DEPOSIT_WINDOW_DAYS = 7
SALES_PER_MONTH_RANGE = (18, 44)

# 1.5 months of revenue on hand. Half a month left healthy merchants dipping overdrawn
# purely from timing -- expenses landing before deposits cleared -- which produced
# overdrafts nobody asked for and made requested counts untestable.
OPENING_BALANCE_FRACTION = 1.5

EXPENSE_RATIO_HEALTHY = 0.82
EXPENSE_RATIO_DECLINING = 1.04
EXPENSES_PER_MONTH = 6
EXPENSE_LABELS = ("rent", "payroll", "supplier payment", "utilities", "software", "logistics")

# An injected one-off is most of a month's revenue arriving at once -- an asset sale, say.
# Sized against average monthly revenue rather than against deposit size, so it stays an
# unambiguous outlier however the batching came out.
ONE_OFF_INJECT_REVENUE_FRACTION = 0.8

# How far below the threshold a forced overdraft lands. Small, so the next deposit clears
# it and the episode stays a single crossing.
OVERDRAFT_UNDERSHOOT = 150.0

NEAR_ZERO_REVENUE_FRACTION = 0.02
MONTH_FLOOR_FRACTION = 0.02
BOUNCED_PAYMENT_REVENUE_FRACTION = 0.04
PERIOD_END = date(2026, 9, 30)

# One definition, declared in load.py so the interface can find merchants without
# importing this module.
DATA_ROOT = load.DEFAULT_ROOT
TRUTH_FILENAME = "truth.json"
REQUIRED_FILES = ("transactions.csv", "sales.csv", TRUTH_FILENAME)

SETTLEMENT_DESCRIPTION = "card settlement"
ONE_OFF_DESCRIPTION = "equipment sale"
BOUNCED_DESCRIPTION = "returned direct debit - insufficient funds"


@dataclass(frozen=True)
class MerchantSpec:
    """The construction parameters for one merchant.

    `detrended_cv` targets the residual variation after the trend is removed (FR-005).
    Clamping a month at the floor, forcing a near-zero month, or injecting a one-off all
    pull the realised value away from the target -- so truth.json always records what was
    realised, never what was requested.

    `overdrafts` is a FLOOR, not an exact target: the number of episodes deliberately
    forced. Forcing one drains the cash buffer, and a merchant living near zero afterwards
    dips again from ordinary timing -- so the distressed profiles realise more than were
    asked for (m13 requests 2 and realises 14). That is left alone rather than engineered
    away, because repeated overdrafts are exactly what a cash-burning business looks like,
    and the alternative mechanisms were worse: restoring the balance after each dip would
    require injecting an inflow that is not revenue, and the scoring impact is bounded
    anyway by policy.OVERDRAFT_RISK_POINTS_MAX. What matters is verified instead: healthy
    merchants realise zero, distressed ones realise some, and truth records the total.
    """

    merchant_id: str
    profile: str
    months: int
    base_monthly_revenue: float
    detrended_cv: float
    trend_pct: float
    overdrafts: int = 0
    bounced_payments: int = 0
    declining_balance: bool = False
    large_one_offs: int = 0
    near_zero_month: bool = False

    # Multiplier on the sales amounts only. At 1.0 the sales export and the bank statement
    # agree once the processor fee is allowed for, which is what every honest merchant
    # looks like. Away from 1.0 the two records contradict each other by that factor, which
    # is what reconciliation exists to catch (FR-034).
    #
    # Scales the SALES side rather than the banked side on purpose: the bank statement is
    # the harder record to falsify, so an overstated sales export is the realistic shape of
    # the problem -- a merchant claiming revenue that never arrived.
    sales_scale: float = 1.0
    seed: int = 0


# Twenty merchants. The three `pair` groups share a base revenue and trend and differ only
# in volatility, which is what SC-001 measures.
MERCHANT_SPECS: tuple[MerchantSpec, ...] = (
    # --- healthy: low residual variation, flat to rising ---
    MerchantSpec("m01_healthy_small", "healthy", 15, 22_000, 0.08, 3.0, seed=101),
    MerchantSpec("m02_healthy_mid", "healthy", 18, 48_000, 0.07, 2.0, seed=102),
    MerchantSpec("m03_healthy_large", "healthy", 16, 118_000, 0.06, 4.0, seed=103),
    MerchantSpec("m04_healthy_oneoff", "healthy", 14, 55_000, 0.15, 1.0, large_one_offs=1, seed=104),
    MerchantSpec("m05_healthy_flat", "healthy", 12, 35_000, 0.14, 0.0, seed=105),
    MerchantSpec("m06_healthy_pairlow", "healthy", 15, 60_000, 0.06, 2.0, seed=106),
    # --- volatile: high residual variation ---
    MerchantSpec("m07_volatile_pairhigh", "volatile", 18, 48_000, 0.40, 2.0, seed=107),
    MerchantSpec(
        "m08_volatile_high", "volatile", 14, 30_000, 0.55, 0.0,
        overdrafts=2, bounced_payments=1, seed=108,
    ),
    MerchantSpec(
        "m09_volatile_badmonth", "volatile", 15, 40_000, 0.45, 0.0,
        overdrafts=1, near_zero_month=True, seed=109,
    ),
    MerchantSpec("m10_volatile_pairhigh2", "volatile", 15, 60_000, 0.42, 2.0, seed=110),
    MerchantSpec(
        "m11_volatile_bounced", "volatile", 13, 38_000, 0.30, -1.0,
        bounced_payments=2, seed=111,
    ),
    # --- declining: negative trend, eroding balance ---
    MerchantSpec(
        "m12_declining_mild", "declining", 16, 52_000, 0.16, -3.0,
        declining_balance=True, seed=112,
    ),
    MerchantSpec(
        "m13_declining_weak", "declining", 15, 44_000, 0.45, -5.0,
        overdrafts=2, declining_balance=True, seed=113,
    ),
    MerchantSpec(
        "m14_declining_distress", "declining", 14, 26_000, 0.58, -12.0,
        overdrafts=4, bounced_payments=2, declining_balance=True, seed=114,
    ),
    MerchantSpec("m15_declining_pairlow", "declining", 15, 50_000, 0.12, -4.0, seed=115),
    MerchantSpec("m16_declining_pairhigh", "declining", 15, 50_000, 0.40, -4.0, seed=116),
    # --- thin file: two below the policy minimum, two short but scorable ---
    MerchantSpec("m17_thin_4mo", "thin_file", 4, 40_000, 0.15, 1.0, seed=117),
    MerchantSpec("m18_thin_5mo", "thin_file", 5, 60_000, 0.08, 2.0, seed=118),
    MerchantSpec("m19_thin_7mo", "thin_file", 7, 35_000, 0.15, 1.0, seed=119),
    MerchantSpec("m20_thin_10mo", "thin_file", 10, 45_000, 0.16, 2.0, seed=120),
    # --- reconciliation fixtures: healthy in every other respect, so the decline can only
    # --- come from the sales records contradicting the bank statement (SC-009) ---
    MerchantSpec(
        "m21_recon_overstated", "healthy", 15, 45_000, 0.10, 1.0,
        sales_scale=1.35, seed=121,
    ),
    MerchantSpec(
        "m22_recon_understated", "healthy", 15, 45_000, 0.10, 1.0,
        sales_scale=0.70, seed=122,
    ),
)

MONOTONICITY_PAIRS = (
    ("m02_healthy_mid", "m07_volatile_pairhigh"),
    ("m06_healthy_pairlow", "m10_volatile_pairhigh2"),
    ("m15_declining_pairlow", "m16_declining_pairhigh"),
)


# --------------------------------------------------------------------------------------
# Trend and variation
#
# These two functions state what the generator built. The tools must reach the same numbers
# from a parsed CSV, so the *definitions* are shared with the tools while the
# implementations stay separate -- a shared implementation would make the comparison
# vacuous.
# --------------------------------------------------------------------------------------


def trend_growth_pct(series: list[float]) -> float:
    """Average geometric growth per month, by least squares on log revenue (FR-004).

    Not the mean of month-over-month percentage changes. That measure is unusable: a series
    of 100, 50, 100 is flat over the period, yet its changes of -50% and +100% average
    +25%, because a halving is -50% while the recovery that undoes it is +100%. Averaging
    ratios overstates growth systematically, and one near-zero month makes it explode -- on
    generated data it reported +709% growth for a flat merchant and a coefficient of
    variation of 1e11.

    Endpoint growth, (last/first)**(1/(n-1)), is also unusable: it reads only two months, so
    a noisy first or last month dominates. It gave a flat merchant a -28% trend.

    Months at or below zero are excluded from the fit, since they carry no logarithm. They
    are still counted in the mean and the residuals computed against this line.
    """
    points = [(i, math.log(v)) for i, v in enumerate(series) if v > 0]
    if len(points) < 2:
        return 0.0
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return (math.exp(slope) - 1) * 100


def detrended_cv(series: list[float]) -> float:
    """Residual variation after removing the fitted trend (FR-005).

    The fitted line is anchored at the mean detrended level, so the residuals sum to
    approximately zero and the result measures unpredictability rather than drift.
    """
    if len(series) < 2:
        return 0.0
    mean = statistics.fmean(series)
    if mean == 0:
        return 0.0
    growth = 1 + trend_growth_pct(series) / 100
    anchor = statistics.fmean(v / growth**i for i, v in enumerate(series))
    residuals = [v - anchor * growth**i for i, v in enumerate(series)]
    return statistics.stdev(residuals) / mean


def _monthly_net_revenue(spec: MerchantSpec) -> list[float]:
    """Net banked revenue per month, aiming at `detrended_cv`.

    Centring the residuals to sum to zero is what makes the scale solvable in closed form:
    with mean(residuals) == 0 the series mean equals the trend mean however the residuals
    are scaled, so the scale factor follows directly rather than needing a search.
    """
    rng = random.Random(spec.seed)
    trend = [spec.base_monthly_revenue * (1 + spec.trend_pct / 100) ** i for i in range(spec.months)]
    trend_mean = statistics.fmean(trend)

    if spec.months < 2 or spec.detrended_cv == 0:
        return list(trend)

    raw = [rng.gauss(0.0, 1.0) * t for t in trend]
    shift = statistics.fmean(raw)
    residuals = [r - shift for r in raw]
    spread = statistics.stdev(residuals)
    scale = (spec.detrended_cv * trend_mean / spread) if spread else 0.0

    floor = spec.base_monthly_revenue * MONTH_FLOOR_FRACTION
    series = [max(t + r * scale, floor) for t, r in zip(trend, residuals)]

    if spec.near_zero_month:
        # A month the business all but stopped trading. Deliberately pushes the realised
        # variation above the target; truth records the realised figure.
        series[spec.months // 2] = spec.base_monthly_revenue * NEAR_ZERO_REVENUE_FRACTION

    return series


# --------------------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------------------


def _days_in_month(start: date) -> int:
    return ((start.replace(day=28) + timedelta(days=4)).replace(day=1) - start).days


def _month_starts(months: int, end: date) -> list[date]:
    """First day of each month in the window ending with `end`'s month, ascending."""
    starts: list[date] = []
    y, m = end.year, end.month
    for _ in range(months):
        starts.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(starts))


def _spread(rng: random.Random, months: list[date], count: int) -> set[date]:
    """Pick `count` distinct months, spread across the period rather than clustered."""
    if count <= 0:
        return set()
    usable = months[1:] if len(months) > 1 else months
    return set(rng.sample(usable, min(count, len(usable))))


def _sales_for_month(rng: random.Random, start: date, net_revenue: float) -> list[dict]:
    """Individual sales whose net-of-fee total equals `net_revenue` exactly."""
    gross_total = net_revenue / (1 - PROCESSOR_FEE_PCT)
    count = rng.randint(*SALES_PER_MONTH_RANGE)
    weights = [rng.uniform(0.4, 1.6) for _ in range(count)]
    total_weight = sum(weights)
    span = _days_in_month(start)

    sales = []
    allocated = 0.0
    for i, w in enumerate(weights):
        amount = (
            round(gross_total - allocated, 2)
            if i == count - 1
            else round(gross_total * w / total_weight, 2)
        )
        allocated += amount
        sales.append(
            {
                "date": (start + timedelta(days=rng.randrange(span))).isoformat(),
                "amount": amount,
            }
        )
    sales.sort(key=lambda s: s["date"])
    return sales


def _batch_into_deposits(sales: list[dict], start: date) -> list[tuple[str, float]]:
    """Group a month's sales into weekly settlement deposits, net of the processor fee.

    Fixed calendar windows, so each deposit covers a comparable share of the month and
    routine deposit size stays stable. See DEPOSIT_WINDOW_DAYS for why rolling windows were
    abandoned.
    """
    span = _days_in_month(start)
    last_window = (span - 1) // DEPOSIT_WINDOW_DAYS
    buckets: dict[int, float] = {}
    for sale in sales:
        day = date.fromisoformat(sale["date"]).day
        window = min((day - 1) // DEPOSIT_WINDOW_DAYS, last_window)
        buckets[window] = buckets.get(window, 0.0) + sale["amount"]

    deposits: list[tuple[str, float]] = []
    for window in sorted(buckets):
        settle_day = min((window + 1) * DEPOSIT_WINDOW_DAYS, span)
        deposits.append(
            (
                (start + timedelta(days=settle_day - 1)).isoformat(),
                round(buckets[window] * (1 - PROCESSOR_FEE_PCT), 2),
            )
        )
    return deposits


def _one_off_threshold(inflows: list[float]) -> float:
    """Q3 + k*IQR over the merchant's own inflows (policy.LARGE_ONE_OFF_IQR_MULTIPLE)."""
    if len(inflows) < 4:
        return max(inflows) + 1 if inflows else float("inf")
    q1, _, q3 = statistics.quantiles(inflows, n=4)
    return q3 + policy.LARGE_ONE_OFF_IQR_MULTIPLE * (q3 - q1)


def _build_transactions(
    spec: MerchantSpec, months: list[date], revenue: list[float]
) -> tuple[list[dict], list[dict], dict]:
    """Emit the bank statement and sales export, tracking what was built.

    Returns (transactions, sales, realised) where `realised` holds the per-month inflow
    totals and flag counts the generator actually produced -- accumulated while emitting
    rows, not recovered by re-reading them.
    """
    rng = random.Random(spec.seed + 1)
    expense_ratio = EXPENSE_RATIO_DECLINING if spec.declining_balance else EXPENSE_RATIO_HEALTHY

    bounced_months = _spread(rng, months, spec.bounced_payments)
    one_off_months = _spread(rng, months, spec.large_one_offs)
    overdraft_months = {d.strftime("%Y-%m") for d in _spread(rng, months, spec.overdrafts)}

    all_sales: list[dict] = []
    inflows: list[tuple[str, float, str]] = []
    outflows: list[tuple[str, float, str]] = []

    for start, net in zip(months, revenue):
        sales = _sales_for_month(rng, start, net)
        all_sales.extend(sales)
        inflows.extend((d, a, SETTLEMENT_DESCRIPTION) for d, a in _batch_into_deposits(sales, start))

        each = round(net * expense_ratio / EXPENSES_PER_MONTH, 2)
        for i in range(EXPENSES_PER_MONTH):
            day = start + timedelta(days=rng.randrange(1, _days_in_month(start) - 2))
            outflows.append((day.isoformat(), -each, EXPENSE_LABELS[i % len(EXPENSE_LABELS)]))

        if start in bounced_months:
            day = start + timedelta(days=rng.randrange(5, 25))
            outflows.append(
                (day.isoformat(), -round(net * BOUNCED_PAYMENT_REVENUE_FRACTION, 2), BOUNCED_DESCRIPTION)
            )

    one_off_amount = round(statistics.fmean(revenue) * ONE_OFF_INJECT_REVENUE_FRACTION, 2)
    for start in sorted(one_off_months):
        day = start + timedelta(days=rng.randrange(5, 25))
        inflows.append((day.isoformat(), one_off_amount, ONE_OFF_DESCRIPTION))

    rows = [{"date": d, "amount": a, "description": desc} for d, a, desc in inflows + outflows]
    rows.sort(key=lambda r: (r["date"], -r["amount"]))

    one_off_threshold = _one_off_threshold([a for _, a, _ in inflows])

    balance = spec.base_monthly_revenue * OPENING_BALANCE_FRACTION
    forced: set[str] = set()
    transactions: list[dict] = []
    inflow_by_month: dict[str, float] = {}
    overdraft_episodes = 0
    bounced_count = 0
    one_off_count = 0
    was_overdrawn = False

    for row in rows:
        month_key = row["date"][:7]

        # Force the requested overdrafts: resize the first outflow of a chosen month so the
        # balance lands just below the threshold. One crossing, cleared by the next deposit.
        if (
            month_key in overdraft_months
            and month_key not in forced
            and row["amount"] < 0
            and balance >= policy.OVERDRAFT_BALANCE_THRESHOLD
        ):
            row = dict(row)
            row["amount"] = -round(balance - policy.OVERDRAFT_BALANCE_THRESHOLD + OVERDRAFT_UNDERSHOOT, 2)
            row["description"] = "payroll"
            forced.add(month_key)

        balance = round(balance + row["amount"], 2)

        overdrawn = balance < policy.OVERDRAFT_BALANCE_THRESHOLD
        if overdrawn and not was_overdrawn:
            overdraft_episodes += 1
        was_overdrawn = overdrawn

        lowered = row["description"].lower()
        if any(keyword in lowered for keyword in policy.BOUNCED_PAYMENT_KEYWORDS):
            bounced_count += 1

        if row["amount"] > 0:
            inflow_by_month[month_key] = round(inflow_by_month.get(month_key, 0.0) + row["amount"], 2)
            if row["amount"] > one_off_threshold:
                one_off_count += 1

        transactions.append({**row, "balance": balance})

    # Scale the sales export AFTER the deposits were derived from it, so the bank statement
    # stays the honest record and only the sales file contradicts it. Applied here rather
    # than inside _sales_for_month because scaling before batching would scale the deposits
    # too, and the two records would still agree -- the merchant would just be bigger.
    #
    # Everything else about the merchant is therefore identical to a scale-1.0 build:
    # revenue, volatility and flags all read the bank statement, which is untouched. That
    # isolates reconciliation as the only variable the fixture changes.
    if spec.sales_scale != 1.0:
        all_sales = [
            {**sale, "amount": round(sale["amount"] * spec.sales_scale, 2)} for sale in all_sales
        ]

    all_sales.sort(key=lambda s: s["date"])
    monthly = [
        (start.strftime("%Y-%m"), inflow_by_month.get(start.strftime("%Y-%m"), 0.0)) for start in months
    ]
    realised = {
        "monthly_revenue": monthly,
        "overdraft_count": overdraft_episodes,
        "bounced_payment_count": bounced_count,
        "large_one_off_count": one_off_count,
        "sales_total": round(sum(sale["amount"] for sale in all_sales), 2),
    }
    return transactions, all_sales, realised


# --------------------------------------------------------------------------------------
# Writing a merchant
# --------------------------------------------------------------------------------------


def build_merchant(spec: MerchantSpec) -> tuple[list[dict], list[dict], dict]:
    """Produce one merchant's rows and its ground truth, without touching the filesystem."""
    months = _month_starts(spec.months, PERIOD_END)
    revenue = _monthly_net_revenue(spec)
    transactions, sales, realised = _build_transactions(spec, months, revenue)

    series = [amount for _, amount in realised["monthly_revenue"]]
    truth = {
        "merchant_id": spec.merchant_id,
        "profile": spec.profile,
        "seed": spec.seed,
        "true_monthly_revenue": realised["monthly_revenue"],
        "true_avg_monthly_revenue": round(statistics.fmean(series), 2),
        "true_total_revenue": round(sum(series), 2),
        "true_mom_growth_pct": round(trend_growth_pct(series), 4),
        "true_revenue_cv": round(detrended_cv(series), 6),
        "true_overdraft_count": realised["overdraft_count"],
        "true_bounced_payment_count": realised["bounced_payment_count"],
        "true_large_one_off_count": realised["large_one_off_count"],
        "true_months_covered": spec.months,
        # Reconciliation truth, stated from the construction rather than measured from the
        # files. The generator built the deposits to match the sales exactly, then scaled
        # the sales by `sales_scale` -- so banked over expected is 1 / sales_scale in every
        # month, and therefore so is the median. Computing it this way keeps truth
        # independent of the tool, which has to recover the same figure from the CSVs.
        "true_total_sales": realised["sales_total"],
        "true_reconciliation_ratio": round(1.0 / spec.sales_scale, 6),
        "true_reconciles": abs(1.0 / spec.sales_scale - 1.0) * 100
        <= policy.RECONCILIATION_TOLERANCE_PCT,
    }
    return transactions, sales, truth


def is_complete(merchant_dir: Path) -> bool:
    """True when every required artifact is present.

    A directory missing any one of them counts as absent and is regenerated in full
    (FR-031): half-loading a merchant is worse than rebuilding it.
    """
    return all((merchant_dir / name).exists() for name in REQUIRED_FILES)


def write_merchant(spec: MerchantSpec, root: Path = DATA_ROOT) -> Path:
    """Write one merchant's three artifacts, overwriting whatever is there."""
    out = root / spec.merchant_id
    out.mkdir(parents=True, exist_ok=True)
    transactions, sales, truth = build_merchant(spec)

    with (out / "transactions.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("date", "amount", "description", "balance"))
        writer.writeheader()
        writer.writerows(transactions)

    with (out / "sales.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("date", "amount"))
        writer.writeheader()
        writer.writerows(sales)

    (out / TRUTH_FILENAME).write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    return out


def generate_all(root: Path = DATA_ROOT, force: bool = False) -> dict[str, list[str]]:
    """Generate every merchant that is absent or incomplete.

    Idempotent by default (FR-031): an existing, complete merchant is left untouched, so a
    decision stays re-examinable against the same inputs. SC-004 measures whether three runs
    of one merchant agree, which is meaningless if the merchant's history changes underneath
    it, so stability here is a precondition for the measurement rather than a nicety.
    """
    written, skipped = [], []
    for spec in MERCHANT_SPECS:
        if not force and is_complete(root / spec.merchant_id):
            skipped.append(spec.merchant_id)
            continue
        write_merchant(spec, root)
        written.append(spec.merchant_id)
    return {"written": written, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic merchants with known ground truth.")
    parser.add_argument("--root", type=Path, default=DATA_ROOT, help="output directory")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite merchants that already exist (names them first, then asks)",
    )
    parser.add_argument("--yes", action="store_true", help="skip the --force confirmation prompt")
    args = parser.parse_args(argv)

    if args.force:
        existing = [s.merchant_id for s in MERCHANT_SPECS if is_complete(args.root / s.merchant_id)]
        if existing and not args.yes:
            # FR-032: an explicit, deliberate action that states what it will replace.
            print(f"--force will overwrite {len(existing)} existing merchant(s):")
            for merchant_id in existing:
                print(f"  {merchant_id}")
            if input("proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("aborted; nothing written")
                return 1

    result = generate_all(args.root, force=args.force)
    print(f"written: {len(result['written'])}  skipped (already complete): {len(result['skipped'])}")
    for merchant_id in result["written"]:
        print(f"  + {merchant_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
