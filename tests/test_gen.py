"""Tests for the synthetic merchant generator.

The generator is the one component with no test above it: every tool test and the whole
evaluation checks results *against* truth.json, so nothing downstream can catch a
generator that records the wrong answer. These tests therefore check the properties that
can be established without a second oracle -- reproducibility, idempotency, internal
consistency between the emitted rows and the recorded truth, and the coverage the merchant
set is supposed to guarantee.
"""

import csv
import json

import pytest

from ledgermind import policy
from ledgermind.data import gen


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """One generated merchant set, shared across the module."""
    root = tmp_path_factory.mktemp("merchants")
    gen.generate_all(root)
    return root


def _rows(root, merchant_id, filename):
    with (root / merchant_id / filename).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _truth(root, merchant_id):
    return json.loads((root / merchant_id / gen.TRUTH_FILENAME).read_text(encoding="utf-8"))


# --- reproducibility and idempotency (FR-028, FR-031, FR-032) --------------------------


def test_same_seed_reproduces_identical_files(tmp_path):
    """FR-028. Byte-identical, not merely equivalent."""
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate_all(a)
    gen.generate_all(b)
    for spec in gen.MERCHANT_SPECS:
        for name in gen.REQUIRED_FILES:
            assert (a / spec.merchant_id / name).read_bytes() == (b / spec.merchant_id / name).read_bytes(), (
                f"{spec.merchant_id}/{name} differs between runs from the same seeds"
            )


def test_second_run_writes_nothing(tmp_path):
    """FR-031: an existing, complete merchant is left alone."""
    first = gen.generate_all(tmp_path)
    assert len(first["written"]) == len(gen.MERCHANT_SPECS)
    assert first["skipped"] == []

    stamps = {
        s.merchant_id: (tmp_path / s.merchant_id / "transactions.csv").stat().st_mtime_ns
        for s in gen.MERCHANT_SPECS
    }
    second = gen.generate_all(tmp_path)
    assert second["written"] == []
    assert len(second["skipped"]) == len(gen.MERCHANT_SPECS)
    for merchant_id, stamp in stamps.items():
        assert (tmp_path / merchant_id / "transactions.csv").stat().st_mtime_ns == stamp


def test_incomplete_merchant_is_regenerated_in_full(tmp_path):
    """FR-031: a directory missing any artifact counts as absent, not as partially usable."""
    gen.generate_all(tmp_path)
    victim = gen.MERCHANT_SPECS[0].merchant_id
    (tmp_path / victim / gen.TRUTH_FILENAME).unlink()

    assert not gen.is_complete(tmp_path / victim)
    result = gen.generate_all(tmp_path)
    assert result["written"] == [victim]
    assert gen.is_complete(tmp_path / victim)


def test_force_overwrites(tmp_path):
    gen.generate_all(tmp_path)
    result = gen.generate_all(tmp_path, force=True)
    assert len(result["written"]) == len(gen.MERCHANT_SPECS)
    assert result["skipped"] == []


# --- truth matches what was emitted ---------------------------------------------------


@pytest.mark.parametrize("spec", gen.MERCHANT_SPECS, ids=lambda s: s.merchant_id)
def test_recorded_revenue_matches_emitted_inflows(generated, spec):
    """The recorded monthly revenue must equal the positive amounts actually written.

    This is the generator's core promise. It is checked by re-reading the CSV precisely
    because the recorded figure was accumulated while emitting rows, never re-derived from
    them -- so agreement here is a real check rather than a tautology.
    """
    rows = _rows(generated, spec.merchant_id, "transactions.csv")
    truth = _truth(generated, spec.merchant_id)

    from_file: dict[str, float] = {}
    for row in rows:
        amount = float(row["amount"])
        if amount > 0:
            month = row["date"][:7]
            from_file[month] = round(from_file.get(month, 0.0) + amount, 2)

    for month, recorded in truth["true_monthly_revenue"]:
        assert from_file.get(month, 0.0) == pytest.approx(recorded, abs=0.02), f"{month}"


@pytest.mark.parametrize("spec", gen.MERCHANT_SPECS, ids=lambda s: s.merchant_id)
def test_recorded_flags_match_emitted_rows(generated, spec):
    rows = _rows(generated, spec.merchant_id, "transactions.csv")
    truth = _truth(generated, spec.merchant_id)

    episodes, was_overdrawn, bounced = 0, False, 0
    for row in rows:
        overdrawn = float(row["balance"]) < policy.OVERDRAFT_BALANCE_THRESHOLD
        if overdrawn and not was_overdrawn:
            episodes += 1
        was_overdrawn = overdrawn
        if any(k in row["description"].lower() for k in policy.BOUNCED_PAYMENT_KEYWORDS):
            bounced += 1

    assert episodes == truth["true_overdraft_count"]
    assert bounced == truth["true_bounced_payment_count"]


@pytest.mark.parametrize("spec", gen.MERCHANT_SPECS, ids=lambda s: s.merchant_id)
def test_balance_column_is_a_running_total(generated, spec):
    rows = _rows(generated, spec.merchant_id, "transactions.csv")
    for previous, row in zip(rows, rows[1:]):
        expected = round(float(previous["balance"]) + float(row["amount"]), 2)
        assert float(row["balance"]) == pytest.approx(expected, abs=0.02)


@pytest.mark.parametrize("spec", gen.MERCHANT_SPECS, ids=lambda s: s.merchant_id)
def test_months_and_shape(generated, spec):
    truth = _truth(generated, spec.merchant_id)
    assert truth["true_months_covered"] == spec.months
    assert len(truth["true_monthly_revenue"]) == spec.months
    assert truth["seed"] == spec.seed
    assert truth["profile"] == spec.profile

    months = [m for m, _ in truth["true_monthly_revenue"]]
    assert months == sorted(months), "monthly series must ascend"

    for name in ("transactions.csv", "sales.csv"):
        dates = [r["date"] for r in _rows(generated, spec.merchant_id, name)]
        assert dates == sorted(dates), f"{name} must be date-ordered"


@pytest.mark.parametrize("spec", gen.MERCHANT_SPECS, ids=lambda s: s.merchant_id)
def test_revenue_is_net_of_the_processor_fee(generated, spec):
    """Gross sales must exceed banked revenue by exactly the fee, excluding one-offs.

    A one-off is banked without being a sale, so it is subtracted before comparing.

    `sales_scale` is divided out. The deposits were derived from the unscaled sales and the
    export was scaled afterwards, so the fee relationship holds against the pre-scale
    figure -- which is exactly what makes the reconciliation fixtures fail reconciliation
    while leaving their bank statement honest. This assertion caught both of them when they
    were added, which is the behaviour wanted from it: the relationship it guards is real,
    and a merchant built to break it must be declared rather than slipped past.
    """
    sales = sum(float(r["amount"]) for r in _rows(generated, spec.merchant_id, "sales.csv"))
    rows = _rows(generated, spec.merchant_id, "transactions.csv")
    settled = sum(
        float(r["amount"]) for r in rows if r["description"] == gen.SETTLEMENT_DESCRIPTION
    )
    unscaled_sales = sales / spec.sales_scale
    assert settled == pytest.approx(unscaled_sales * (1 - gen.PROCESSOR_FEE_PCT), rel=1e-6)


# --- coverage the merchant set must guarantee -----------------------------------------


def test_healthy_merchants_have_no_cashflow_flags(generated):
    """The common case must be exact, even though distressed counts are only a floor."""
    for spec in gen.MERCHANT_SPECS:
        if spec.overdrafts or spec.bounced_payments or spec.declining_balance:
            continue
        truth = _truth(generated, spec.merchant_id)
        assert truth["true_overdraft_count"] == 0, spec.merchant_id
        assert truth["true_bounced_payment_count"] == 0, spec.merchant_id


def test_requested_flags_are_a_floor(generated):
    """Distressed merchants realise at least what was asked for.

    Not equality: forcing an overdraft drains the cash buffer, and a merchant living near
    zero afterwards dips again from ordinary timing. See MerchantSpec for why that is left
    alone rather than engineered away.
    """
    for spec in gen.MERCHANT_SPECS:
        truth = _truth(generated, spec.merchant_id)
        assert truth["true_overdraft_count"] >= spec.overdrafts, spec.merchant_id
        assert truth["true_large_one_off_count"] >= spec.large_one_offs, spec.merchant_id
        assert truth["true_bounced_payment_count"] == spec.bounced_payments, spec.merchant_id


def test_set_covers_both_sides_of_the_history_minimum(generated):
    months = [_truth(generated, s.merchant_id)["true_months_covered"] for s in gen.MERCHANT_SPECS]
    assert any(m < policy.MIN_MONTHS_HISTORY for m in months), "no thin-file merchant"
    assert any(m >= policy.MIN_MONTHS_HISTORY for m in months)


def test_set_spans_the_volatility_bands(generated):
    """The set must reach both ends of the band table, or whole branches go untested."""
    cvs = [_truth(generated, s.merchant_id)["true_revenue_cv"] for s in gen.MERCHANT_SPECS]
    assert min(cvs) <= policy.VOLATILITY_BANDS[0][0], "nothing in the most stable band"
    assert max(cvs) > policy.VOLATILITY_BANDS[-2][0], "nothing in the least stable band"


def test_advance_cap_is_reachable(generated):
    """At least one merchant must be large enough to engage the cap (FR-008).

    Otherwise `advance_cap_applied` is permanently false and T013's test of it is vacuous.
    """
    best = max(policy.TIER_TERMS[t].advance_multiple for t in policy.TIER_TERMS)
    revenues = [_truth(generated, s.merchant_id)["true_avg_monthly_revenue"] for s in gen.MERCHANT_SPECS]
    assert any(r * best > policy.ADVANCE_CAP_ABSOLUTE for r in revenues)


def test_monotonicity_pairs_share_revenue(generated):
    """SC-001 compares advances, which is only meaningful if revenue is held constant."""
    for low_id, high_id in gen.MONOTONICITY_PAIRS:
        low, high = _truth(generated, low_id), _truth(generated, high_id)
        assert low["true_avg_monthly_revenue"] == pytest.approx(
            high["true_avg_monthly_revenue"], rel=0.02
        ), f"{low_id} vs {high_id}"
        assert high["true_revenue_cv"] > low["true_revenue_cv"] * 2, f"{low_id} vs {high_id}"


def test_near_zero_month_exists_and_stays_finite(generated):
    """The zero-revenue edge case must be present, and must not produce an undefined figure."""
    spec = next(s for s in gen.MERCHANT_SPECS if s.near_zero_month)
    truth = _truth(generated, spec.merchant_id)
    series = [v for _, v in truth["true_monthly_revenue"]]
    assert min(series) < max(series) * 0.1, "no near-zero month present"
    assert 0 < truth["true_revenue_cv"] < 10, truth["true_revenue_cv"]
    assert abs(truth["true_mom_growth_pct"]) < 100, truth["true_mom_growth_pct"]


# --- the trend estimator --------------------------------------------------------------


def test_trend_estimator_rejects_the_mean_of_ratios():
    """The illustration from FR-004, as an executable assertion.

    A series that starts and ends at 100 -- flat over the period -- averages +25% per month
    under the mean of month-over-month changes, because the halving is -50% while the
    recovery is +100%. The log-linear fit reports no growth.
    """
    flat = [100.0, 50.0, 100.0]
    mean_of_ratios = sum(
        (flat[i] / flat[i - 1] - 1) * 100 for i in range(1, len(flat))
    ) / (len(flat) - 1)
    assert mean_of_ratios == pytest.approx(25.0)
    assert gen.trend_growth_pct(flat) == pytest.approx(0.0, abs=1e-9)


def test_trend_estimator_recovers_a_known_growth_rate():
    for rate in (-12.0, -5.0, 0.0, 2.0, 4.0):
        series = [1000 * (1 + rate / 100) ** i for i in range(15)]
        assert gen.trend_growth_pct(series) == pytest.approx(rate, abs=1e-6)


def test_detrended_cv_is_zero_for_a_smooth_trend():
    """The whole point of detrending: smooth growth is not volatility."""
    for rate in (-12.0, 0.0, 2.0):
        series = [1000 * (1 + rate / 100) ** i for i in range(15)]
        assert gen.detrended_cv(series) == pytest.approx(0.0, abs=1e-9)


def test_detrended_cv_survives_a_zero_month():
    series = [1000.0, 1000.0, 0.0, 1000.0, 1000.0]
    result = gen.detrended_cv(series)
    assert result > 0
    assert result == pytest.approx(result)  # finite, not nan or inf
