"""Run the pipeline over the merchant set and measure it (T033, T034, FR-026).

This is the module that turns the project's claim into a number. Everything else asserts
that grounding works; this says how well, over how many merchants, against answers recorded
before the pipeline ever ran.

Ground truth is read here and in `tests/` only (Article III). That is why the harness lives
outside `tools/`, `agent/`, `guardrail/` and `app/` -- the import-boundary test fails if any
of those reach it.

A merchant whose run raises is recorded as a failure rather than aborting the pass. A single
model that will not stop calling tools should cost one line of the report, not the whole
measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ledgermind.agent import loop
from ledgermind.data import gen, load
from ledgermind.eval import metrics
from ledgermind.llm.base import Provider
from ledgermind.llm.ollama import OllamaProvider

DEFAULT_CONSISTENCY_MERCHANT = "m02_healthy_mid"
DEFAULT_CONSISTENCY_RUNS = 3

# Fields compared across repeated runs for SC-004. Deliberately the structured offer only:
# those come from the tools and must be identical. The memo is the model's prose and may
# legitimately differ between runs.
OFFER_FIELDS = (
    "advance_amount",
    "repayment_pct",
    "expected_duration_months",
    "total_repayable",
    "declined",
)


@dataclass
class Report:
    measures: metrics.Measures
    elapsed: float
    provider: str
    merchant_count: int
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.measures.all_pass and not self.failures

    def render(self) -> str:
        text = metrics.format_report(
            self.measures, self.elapsed, self.provider, self.merchant_count
        )
        if self.failures:
            lines = ["", "RUNS THAT DID NOT REACH A DECISION"]
            lines += [f"  {merchant_id}: {error}" for merchant_id, error in self.failures]
            text += "\n".join(lines)
        return text

    def as_dict(self) -> dict:
        """The report as data, for the interface to render (T038)."""
        m = self.measures
        return {
            "provider": self.provider,
            "merchant_count": self.merchant_count,
            "elapsed_seconds": round(self.elapsed, 1),
            "passed": self.passed,
            "monotonicity_holds": m.monotonicity_holds,
            "monotonicity_pairs": [
                {
                    "steady": p.steady_id,
                    "erratic": p.erratic_id,
                    "steady_advance": p.steady_advance,
                    "erratic_advance": p.erratic_advance,
                    "holds": p.holds,
                }
                for p in m.monotonicity
            ],
            "grounding_faithfulness": round(m.grounding_faithfulness, 6),
            "grounding_resolved": m.grounding_resolved,
            "grounding_total": m.grounding_total,
            "policy_breaches": [f"{mid}: {b}" for mid, b in m.policy_breaches],
            "truth_mismatches": [
                {"merchant": t.merchant_id, "field": t.field, "computed": t.computed,
                 "recorded": t.recorded}
                for t in m.truth_mismatches
            ],
            "reconciliation_errors": m.reconciliation_errors,
            "consistency_holds": m.consistency_holds,
            "attempts": {str(k): v for k, v in sorted(m.attempts.items())},
            "fallbacks": m.fallbacks,
            "declines": [{"merchant": mid, "reason": r} for mid, r in m.declines],
            "failures": [{"merchant": mid, "error": e} for mid, e in self.failures],
        }


def _truth_for(root: Path, merchant_id: str) -> dict:
    path = root / merchant_id / gen.TRUTH_FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run_eval(
    provider: Provider,
    root: Path = gen.DATA_ROOT,
    limit: int | None = None,
    consistency_merchant: str = DEFAULT_CONSISTENCY_MERCHANT,
    consistency_runs: int = DEFAULT_CONSISTENCY_RUNS,
    progress=None,
) -> Report:
    """Underwrite every merchant, then measure the results.

    `limit` exists for development: a full pass costs a few minutes of model time, which is
    too slow to iterate against. Reported figures should come from a full pass, and the
    report states the count so a truncated one cannot be mistaken for it.
    """
    specs = gen.MERCHANT_SPECS[:limit] if limit else gen.MERCHANT_SPECS
    started = time.monotonic()

    decisions: dict[str, loop.Decision] = {}
    truths: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    for index, spec in enumerate(specs, 1):
        merchant_id = spec.merchant_id
        if progress:
            progress(index, len(specs), merchant_id)
        try:
            merchant = load.load_merchant(root / merchant_id)
            decisions[merchant_id] = loop.run(merchant, provider)
            truths[merchant_id] = _truth_for(root, merchant_id)
        except Exception as exc:
            # Recorded, not raised. One uncooperative run should cost a line of the report,
            # not the whole measurement.
            failures.append((merchant_id, f"{type(exc).__name__}: {exc}"))

    # SC-004: the same merchant, several times. Only meaningful if it is in this subset.
    consistency_offers: list[dict] = []
    if consistency_merchant in decisions:
        for _ in range(consistency_runs):
            try:
                merchant = load.load_merchant(root / consistency_merchant)
                decision = loop.run(merchant, provider)
                consistency_offers.append(
                    {field: decision.offer[field] for field in OFFER_FIELDS}
                )
            except Exception as exc:
                failures.append((f"{consistency_merchant} (consistency)", str(exc)))

    measures = metrics.collect(
        decisions,
        truths,
        gen.MONOTONICITY_PAIRS,
        consistency_merchant=consistency_merchant,
        consistency_offers=consistency_offers,
    )
    return Report(
        measures=measures,
        elapsed=time.monotonic() - started,
        provider=provider.name,
        merchant_count=len(decisions),
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate LedgerMind over the merchant set.")
    parser.add_argument("--root", type=Path, default=gen.DATA_ROOT)
    parser.add_argument("--model", default=None, help="ollama model name")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="evaluate only the first N merchants (development only; the report states the count)",
    )
    parser.add_argument("--consistency-runs", type=int, default=DEFAULT_CONSISTENCY_RUNS)
    parser.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    args = parser.parse_args(argv)

    provider = OllamaProvider(model=args.model) if args.model else OllamaProvider()

    def progress(index, total, merchant_id):
        print(f"  [{index:>2}/{total}] {merchant_id}", flush=True)

    print(f"evaluating with {provider.name}\n", flush=True)
    report = run_eval(
        provider,
        root=args.root,
        limit=args.limit,
        consistency_runs=args.consistency_runs,
        progress=progress,
    )
    print()
    print(report.render())

    if args.json:
        args.json.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
