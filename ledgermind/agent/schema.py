"""The shapes a run produces.

Note what is absent: there is no field anywhere here into which a model-emitted number is
parsed. `Decision.offer` holds `compute_offer`'s return value verbatim, and the interface
renders it directly. That is Article I made structural -- the model cannot alter an offer
field because no code path exists that would let it (FR-016).

The model contributes exactly one thing to this structure: `Decision.memo`, a string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of checking one candidate memo against the recorded facts.

    `passed` is the only thing the loop acts on; the rest exists so a failure can be fed
    back to the model as a specific reason rather than a bare rejection.
    """

    passed: bool
    unresolved_numerals: tuple[str, ...] = ()
    policy_breaches: tuple[str, ...] = ()
    incomplete: bool = False
    missing_citations: tuple[str, ...] = ()
    attempt: int = 1
    used_fallback: bool = False

    # The figures the model got wrong, carried through to the fallback result.
    # `unresolved_numerals` on a fallback describes the fallback, which passes by
    # construction -- so without this the interface could report that verification failed
    # and not what failed, which is the one thing a reader actually wants to know.
    rejected_numerals: tuple[str, ...] = ()

    def reason(self) -> str:
        """A single line explaining the failure, for the regeneration prompt."""
        parts = []
        if self.unresolved_numerals:
            parts.append(
                "these figures appear in your memo but were not produced by any analysis: "
                + ", ".join(self.unresolved_numerals)
            )
        if self.missing_citations:
            parts.append("the memo must also cite: " + ", ".join(self.missing_citations))
        if self.policy_breaches:
            parts.append("policy breaches: " + "; ".join(self.policy_breaches))
        return " | ".join(parts) if parts else "verification passed"


@dataclass(frozen=True)
class Provenance:
    """Which analyses produced a figure the memo quotes (FR-029).

    `fact_keys` is a list because resolution is existence-based: when two recorded facts
    hold the same value, the numeral is grounded and every match is reported rather than
    one being guessed at.
    """

    numeral: str
    value: float
    fact_keys: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    """Everything one run produces.

    The analysis results are kept whole rather than flattened, so the interface and the
    evaluation read the same values the tools returned.
    """

    merchant_id: str
    revenue_metrics: dict[str, Any]
    volatility: dict[str, Any]
    flags: dict[str, Any]
    risk: dict[str, Any]
    offer: dict[str, Any]
    memo: str
    verification: VerificationResult
    provenance: tuple[Provenance, ...] = ()
    tool_calls: tuple[str, ...] = ()
    provider_name: str = ""

    # Every recorded fact, keyed as the provenance refers to them (FR-013: the record must
    # remain inspectable after the run). Without it `Provenance` gives a consumer the key
    # `revenue.mom_growth_pct` and no way to reach the 1.9886 behind it -- so an interface
    # could not show "you wrote 2.0%, the recorded value is 1.9886", which is the whole
    # point of displaying provenance at all.
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def declined(self) -> bool:
        return bool(self.offer.get("declined"))

    @property
    def used_fallback(self) -> bool:
        """True when the memo is the templated fallback rather than generated prose.

        Drives the mandatory visible notice. The system must never substitute the fallback
        without telling the analyst (FR-025).
        """
        return self.verification.used_fallback

    def recorded_value(self, fact_key: str) -> Any:
        """The value behind a provenance key, at full precision.

        What the memo quotes is rounded to a renderable precision; this is what was
        actually computed.
        """
        return self.facts.get(fact_key)
