"""Numeral extraction and resolution against the fact ledger (FR-017 to FR-020).

The load-bearing component. Every figure in a displayed memo passes through here, and a
figure that cannot be resolved means the memo is rejected.

**An unextracted numeral is worse than a rejected one.** A rejection is visible and
triggers a retry; a numeral the scanner misses is displayed to the analyst without ever
being verified. So extraction errs toward catching too much: a false rejection costs one
regeneration, a false pass costs the project's entire claim. That asymmetry is why spelled
cardinals run to one hundred rather than stopping at twenty -- "a risk score of ninety" on
a merchant scoring thirty would otherwise sail straight through.

Three exemptions are applied, each narrow and each stated here rather than discovered later:

1. **ISO dates** are stripped before scanning. "2025-04-07" would otherwise yield 2025, 4
   and 7 as separate claims needing facts.
2. **Bare years** (1900-2100, no separators, no decimal, no currency or percent sign) are
   not figures. "the 18 months to September 2026" makes one numeric claim, not two. A
   financial figure of exactly 2026 written without a thousands separator is vanishingly
   unlikely, and would simply need writing as "2,026".
3. **Hyphenated non-cardinals.** "one-off" is not the number one. Hyphens are excluded from
   the word boundary, while genuine compounds ("twenty-one") are matched as a unit first.

Words of quantity that are not cardinals -- "no", "none", "several" -- are deliberately not
treated as numerals. Including "no" would not catch the error it appears to: resolution is
existence-based, so "no overdrafts" on a merchant with four would resolve against any other
zero-valued fact. The gap is real and recorded in data-model.md; pretending to close it
would be worse than naming it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ledgermind.agent.ledger import FactLedger

ISO_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")

# Currency or nothing, digits with optional thousands separators, optional decimals,
# optional trailing percent. The match keeps the original text so a rejection message can
# quote the memo verbatim.
NUMERAL = re.compile(
    r"""
    (?P<currency>[$£€]\s?)?          # optional currency marker
    (?P<number>
        \d{1,3}(?:,\d{3})+(?:\.\d+)? # 1,234 or 1,234.56
        | \d+\.\d+                   # 1234.56
        | \d+                        # 1234
    )
    \s?(?P<percent>%)?
    """,
    re.VERBOSE,
)

SMALL_CARDINALS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
TENS_CARDINALS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SINGLE_CARDINALS = {**SMALL_CARDINALS, **TENS_CARDINALS, "hundred": 100}

# Hyphens excluded on both sides, so "one-off" is not the number one.
_EDGE_BEFORE = r"(?<![\w-])"
_EDGE_AFTER = r"(?![\w-])"

# Compounds are matched first and their spans masked, so "twenty-one" yields 21 rather than
# 20 and 1. A hyphen or a space may join them.
COMPOUND_PATTERN = re.compile(
    _EDGE_BEFORE
    + r"(" + "|".join(TENS_CARDINALS) + r")"
    + r"[-\s]"
    + r"(" + "|".join(SMALL_CARDINALS) + r")"
    + _EDGE_AFTER,
    re.IGNORECASE,
)
SINGLE_PATTERN = re.compile(
    _EDGE_BEFORE
    + r"(" + "|".join(sorted(SINGLE_CARDINALS, key=len, reverse=True)) + r")"
    + _EDGE_AFTER,
    re.IGNORECASE,
)

YEAR_RANGE = range(1900, 2101)


@dataclass(frozen=True)
class Numeral:
    """One numeric claim found in prose."""

    text: str
    value: float


def _spelled_numerals(text: str) -> list[Numeral]:
    """Cardinals written as words, compounds resolved before their parts."""
    found: list[Numeral] = []
    claimed: list[tuple[int, int]] = []

    for match in COMPOUND_PATTERN.finditer(text):
        tens = TENS_CARDINALS[match.group(1).lower()]
        unit = SMALL_CARDINALS[match.group(2).lower()]
        found.append(Numeral(text=match.group(0), value=float(tens + unit)))
        claimed.append(match.span())

    for match in SINGLE_PATTERN.finditer(text):
        start, end = match.span()
        if any(start < claimed_end and claimed_start < end for claimed_start, claimed_end in claimed):
            continue  # already counted as part of a compound
        word = match.group(1)
        found.append(Numeral(text=word, value=float(SINGLE_CARDINALS[word.lower()])))

    return found


def extract_numerals(prose: str) -> tuple[Numeral, ...]:
    """Every numeric claim in the text."""
    without_dates = ISO_DATE.sub(" ", prose)

    found: list[Numeral] = []
    for match in NUMERAL.finditer(without_dates):
        raw = match.group("number")
        value = float(raw.replace(",", ""))

        is_bare_integer = "," not in raw and "." not in raw
        decorated = bool(match.group("currency") or match.group("percent"))
        if is_bare_integer and not decorated and int(value) in YEAR_RANGE:
            continue  # a calendar year, not a figure

        found.append(Numeral(text=match.group(0).strip(), value=value))

    found.extend(_spelled_numerals(without_dates))
    return tuple(found)


@dataclass(frozen=True)
class GroundingResult:
    """Which numerals resolved, and to what."""

    resolved: tuple[tuple[Numeral, tuple[str, ...]], ...]
    unresolved: tuple[Numeral, ...]

    @property
    def passed(self) -> bool:
        return not self.unresolved

    def cited_keys(self) -> frozenset[str]:
        """Every fact key the memo quoted. Used by the completeness check."""
        return frozenset(key for _, keys in self.resolved for key in keys)


def check_grounding(prose: str, ledger: FactLedger) -> GroundingResult:
    """Resolve every numeral in the prose against the recorded facts.

    A numeral with no match means the model produced a figure no analysis did, which is the
    one thing the system exists to prevent.
    """
    resolved: list[tuple[Numeral, tuple[str, ...]]] = []
    unresolved: list[Numeral] = []

    for numeral in extract_numerals(prose):
        keys = ledger.resolve(numeral.value)
        if keys:
            resolved.append((numeral, keys))
        else:
            unresolved.append(numeral)

    return GroundingResult(resolved=tuple(resolved), unresolved=tuple(unresolved))
