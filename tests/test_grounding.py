"""Numeral extraction and grounding (T023, FR-017 to FR-020)."""

import pytest

from ledgermind.agent.ledger import FactLedger
from ledgermind.guardrail.grounding import check_grounding, extract_numerals


def _values(prose):
    return [numeral.value for numeral in extract_numerals(prose)]


# --- extraction -----------------------------------------------------------------------


def test_extracts_plain_and_formatted_amounts():
    assert _values("revenue of 47812 rose") == [47812.0]
    assert _values("revenue of 47,812 rose") == [47812.0]
    assert _values("revenue of 47,812.34 rose") == [47812.34]
    assert _values("revenue of $47,812 rose") == [47812.0]


def test_extracts_percentages():
    assert _values("growing at 2.0% a month") == [2.0]
    assert _values("repaid at 14% of sales") == [14.0]


def test_extracts_spelled_cardinals():
    """FR-019: a miscount must not evade the check by being spelled out."""
    assert _values("there were three overdrafts") == [3.0]
    assert _values("Four payments were returned") == [4.0]
    assert _values("eighteen months of history") == [18.0]


def test_spelled_cardinals_only_match_whole_words():
    assert _values("the tenant paid") == []
    assert _values("a oneness of purpose") == []


def test_iso_dates_are_not_three_separate_claims():
    """2025-04-07 would otherwise yield 2025, 4 and 7, none of which is a figure."""
    assert _values("from 2025-04-07 to 2026-09-30") == []
    assert _values("the period 2025-04 onwards") == []


def test_bare_years_are_not_figures():
    """"the 18 months to September 2026" makes one numeric claim, not two."""
    assert _values("over the period to September 2026") == []
    assert _values("eighteen months to September 2026") == [18.0]


def test_a_decorated_or_separated_year_like_number_is_a_figure():
    """The exemption is narrow: only bare integers with no formatting."""
    assert _values("an advance of $2026") == [2026.0]
    assert _values("an advance of 2,026") == [2026.0]
    assert _values("a margin of 2026.00") == [2026.0]


def test_zero_is_extracted():
    assert _values("0 overdrafts") == [0.0]
    assert _values("zero returned payments") == [0.0]


def test_prose_with_no_figures_yields_none():
    assert _values("Revenue appears stable and the account is healthy.") == []


def test_extraction_keeps_the_original_text_for_the_rejection_message():
    numerals = extract_numerals("an advance of $47,812.34")
    assert numerals[0].text == "$47,812.34"
    assert numerals[0].value == 47812.34


# --- grounding against a ledger -------------------------------------------------------


@pytest.fixture
def ledger():
    led = FactLedger()
    led.register(
        "revenue",
        "compute_revenue_metrics",
        {
            "avg_monthly_revenue": 47_812.34,
            "months_covered": 18,
            "mom_growth_pct": 1.9886,
            "monthly_revenue": [("2026-01", 31_400.0), ("2026-02", 44_200.0)],
        },
    )
    led.register("flags", "detect_cashflow_flags", {"overdraft_count": 3, "bounced_payment_count": 0})
    led.register(
        "offer",
        "compute_offer",
        {"advance_amount": 77_000.0, "repayment_pct": 13.0, "expected_duration_months": 12},
    )
    return led


def test_a_faithful_memo_passes(ledger):
    memo = (
        "Revenue has averaged roughly $47,800 a month over eighteen months, growing 2.0% "
        "a month. We observed three overdrafts and zero returned payments. On that basis "
        "we can advance $77,000, repaid at 13% of monthly revenue over 12 months."
    )
    result = check_grounding(memo, ledger)
    assert result.passed, result.unresolved


def test_a_fabricated_figure_is_caught(ledger):
    """The one thing the system exists to prevent."""
    memo = "Revenue has averaged nearly $52,000 a month, so we can advance $77,000."
    result = check_grounding(memo, ledger)
    assert not result.passed
    assert [n.text for n in result.unresolved] == ["$52,000"]


def test_a_single_months_revenue_resolves(ledger):
    """Legitimate, and only possible because sequences register per element."""
    assert check_grounding("January was weakest at 31,400.", ledger).passed


def test_a_miscount_spelled_as_a_word_is_caught(ledger):
    """There were three overdrafts; claiming seven must not pass."""
    result = check_grounding("There were seven overdrafts in the period.", ledger)
    assert not result.passed
    assert [n.text for n in result.unresolved] == ["seven"]


def test_every_rung_of_the_ladder_is_accepted(ledger):
    for written in ("47,812", "47,810", "47,800", "48,000"):
        assert check_grounding(f"revenue of {written}", ledger).passed, written


def test_a_value_between_rungs_is_refused(ledger):
    for written in ("47,500", "50,000", "47,900"):
        assert not check_grounding(f"revenue of {written}", ledger).passed, written


def test_provenance_names_the_analysis_behind_each_figure(ledger):
    result = check_grounding("we can advance $77,000", ledger)
    assert result.cited_keys() == {"offer.advance_amount"}


def test_unresolved_numerals_are_reported_in_full(ledger):
    result = check_grounding("averaged 52,000 and we advance 99,999", ledger)
    assert len(result.unresolved) == 2


# --- cardinals above twenty: the silent-pass hole -------------------------------------


def test_cardinals_above_twenty_are_extracted():
    """An unextracted numeral is worse than a rejected one.

    Stopping the cardinal list at twenty left "a risk score of ninety" unextracted, so it
    was never checked and displayed unverified. A rejection costs one regeneration; a
    silent pass costs the project's whole claim.
    """
    assert _values("a score of thirty") == [30.0]
    assert _values("ninety payments") == [90.0]
    assert _values("forty-two months") == [42.0]
    assert _values("one hundred") == [1.0, 100.0]


def test_compounds_resolve_before_their_parts():
    """"twenty-one" is 21, not 20 and 1."""
    assert _values("twenty-one overdrafts") == [21.0]
    assert _values("ninety nine percent") == [99.0]


def test_a_lie_written_in_words_is_caught(ledger):
    """The ledger holds overdraft_count = 3. Any other spelled count must fail."""
    for written in ("thirty", "ninety", "forty-five"):
        result = check_grounding(f"there were {written} overdrafts", ledger)
        assert not result.passed, written


def test_hyphenated_non_cardinals_are_still_exempt():
    """The compound support must not undo the one-off fix."""
    assert _values("three large one-off inflows") == [3.0]
    assert _values("a one-off adjustment") == []


# --- digits inside an identifier (found by the first real model run) -------------------


def test_a_merchant_id_is_not_a_figure():
    """The model opened its memo with "The business m02_healthy_mid has shown...".

    The "02" was scanned as a numeric claim and passed only because that merchant's growth
    rate happened to be 2.0. On m07, with no fact equal to 7, an honest memo naming the
    merchant would have been rejected.
    """
    assert _values("The business m02_healthy_mid has shown steady growth") == []
    assert _values("merchant m21_recon_overstated was declined") == []


def test_a_figure_beside_an_identifier_is_still_read():
    assert _values("m02_healthy_mid banked 57,100") == [57100.0]


def test_a_trailing_letter_does_not_exempt_a_figure():
    """Only the preceding character is checked. Excluding on a trailing letter too would
    make "12m" invisible, and an unscanned numeral is a silent pass -- the direction that
    costs the project its claim."""
    assert _values("a 12m facility") == [12.0]


def test_currency_and_percent_markers_still_work():
    assert _values("$57,100 and 13%") == [57100.0, 13.0]
