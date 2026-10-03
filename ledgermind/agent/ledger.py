"""The fact ledger: every value any analysis returned during one run.

This is the structure the entire grounding guarantee rests on. Two rules make it work, and
both are about *ordering and access* rather than about the data itself:

1. **Only the dispatcher writes.** The model and the guardrail read; neither adds (FR-012).
   `register` is called from exactly one place in the loop.
2. **Written before the transcript.** A tool return is registered *before* its result is
   appended to the model's context, so the model cannot see a value that is not already
   recorded. Anything it quotes is therefore in the ledger by construction -- which is what
   makes Article I structural rather than a prompt instruction.

Sequences register per element. Without that, a memo citing one month's revenue would fail
grounding despite the figure being entirely legitimate: the series is a single field holding
eighteen numbers, and `revenue.monthly_revenue` as one entry matches none of them.

Not in plan.md's tree, which put the ledger inside `loop.py`. Split out so it can be tested
without importing the loop, and so the loop stays the ~150 lines it is meant to be.
Recorded as a deviation in tasks.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ledgermind import policy


@dataclass(frozen=True)
class FactLedgerEntry:
    """One value an analysis produced."""

    key: str
    value: Any
    tool: str
    call_index: int

    @property
    def is_numeric(self) -> bool:
        """Booleans are excluded: `isinstance(True, int)` holds in Python, and a flag is
        not a figure a memo can quote."""
        return isinstance(self.value, (int, float)) and not isinstance(self.value, bool)


def _flatten(prefix: str, value: Any) -> Iterable[tuple[str, Any]]:
    """Expand a tool's return value into individual (key, value) facts.

    The shapes that occur, and the key each produces:

      scalar                     -> prefix
      dict                       -> prefix.field
      list of (str, number)      -> prefix.<str>        (the monthly revenue series)
      list of dicts              -> prefix.<index>.field
      list of scalars            -> prefix.<index>
    """
    if isinstance(value, dict):
        for field_name, inner in value.items():
            yield from _flatten(f"{prefix}.{field_name}", inner)
        return

    if isinstance(value, (list, tuple)):
        for index, element in enumerate(value):
            # A (month, amount) pair keys on the month, so `revenue.monthly_revenue.2026-01`
            # is stable regardless of how many months the file happens to cover.
            if (
                isinstance(element, (list, tuple))
                and len(element) == 2
                and isinstance(element[0], str)
                and isinstance(element[1], (int, float))
                and not isinstance(element[1], bool)
            ):
                yield f"{prefix}.{element[0]}", element[1]
            else:
                yield from _flatten(f"{prefix}.{index}", element)
        return

    yield prefix, value


def rounding_candidates(value: float) -> set[float]:
    """Every value a memo may legitimately write for this fact (FR-018).

    The declared ladder and nothing else: nearest 1, 10, 100, 1000, or one decimal place.
    Rounding is applied to the *recorded* value and the numeral must equal one of the
    results -- so legality is a membership test, not a tolerance.

    The absolute value is included because prose describes an overdraft as "1,000
    overdrawn" as readily as "-1,000", and both refer to the same recorded fact.
    """
    candidates: set[float] = set()
    for magnitude in (value, abs(value)):
        for unit in policy.GROUNDING_ROUNDING_LADDER:
            digits = -len(str(unit)) + 1  # 1 -> 0, 10 -> -1, 100 -> -2, 1000 -> -3
            candidates.add(float(round(magnitude, digits)))
        candidates.add(float(round(magnitude, policy.GROUNDING_DECIMAL_PLACES)))
        candidates.add(float(magnitude))
    return candidates


class FactLedger:
    """Append-only record of one run's analysis results."""

    def __init__(self) -> None:
        self._entries: list[FactLedgerEntry] = []
        self._calls = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def call_count(self) -> int:
        return self._calls

    def register(self, namespace: str, tool_name: str, result: dict) -> tuple[FactLedgerEntry, ...]:
        """Record everything one tool call returned.

        Called only by the dispatcher, and only before the result reaches the model.
        Returns the entries added, so the caller can log what became quotable.
        """
        self._calls += 1
        added = [
            FactLedgerEntry(key=key, value=value, tool=tool_name, call_index=self._calls)
            for key, value in _flatten(namespace, result)
        ]
        self._entries.extend(added)
        return tuple(added)

    @property
    def entries(self) -> tuple[FactLedgerEntry, ...]:
        return tuple(self._entries)

    def numeric_entries(self) -> tuple[FactLedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.is_numeric)

    def get(self, key: str) -> FactLedgerEntry | None:
        """The most recent entry for a key.

        Most recent rather than first: a tool called twice should be read as having
        superseded its earlier result, while `call_index` keeps both auditable.
        """
        for entry in reversed(self._entries):
            if entry.key == key:
                return entry
        return None

    def resolve(self, numeral: float) -> tuple[str, ...]:
        """Keys of every fact this numeral could legitimately be (FR-018).

        Existence-based: a numeral matching two facts is grounded, and both are reported.
        The claim being verified is that the figure came from an analysis, not which one --
        so this cannot catch a figure quoted in the wrong sentence, only one invented
        outright. That limit is recorded in data-model.md.
        """
        return tuple(
            entry.key
            for entry in self._entries
            if entry.is_numeric and float(numeral) in rounding_candidates(float(entry.value))
        )

    def snapshot(self) -> dict[str, Any]:
        """The ledger as a plain dict, for the interface and the evaluation report."""
        return {entry.key: entry.value for entry in self._entries}
