"""Underwrite one merchant from the command line.

    python -m ledgermind.decide m02_healthy_mid

The smallest thing that shows the whole pipeline: the analyses as they run, the offer the
tools computed, the memo the model wrote, and the provenance of every figure in it.

Exists because the README has to be able to tell someone how to run this, and the Streamlit
interface is the first thing the plan says to cut. A reader who cannot run it has to take
the evaluation on trust.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ledgermind.agent import loop
from ledgermind.data import gen, load
from ledgermind.llm.ollama import OllamaProvider


def _observer(verbose: bool):
    def report(event: str, payload: dict) -> None:
        if event == "registered" and verbose:
            print(f"  {payload['tool']:<24} -> {len(payload['keys'])} facts recorded", flush=True)
        elif event == "tool_error":
            print(f"  ! {payload['tool']}: {payload['error'][:88]}", flush=True)
        elif event == "verification":
            state = "verified" if payload["passed"] else "rejected"
            print(f"  memo attempt {payload['attempt']}: {state}", flush=True)
            if not payload["passed"]:
                print(f"      {payload['reason'][:96]}", flush=True)
        elif event == "fallback":
            print("  falling back to the templated memo", flush=True)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Underwrite one merchant.")
    parser.add_argument("merchant_id", nargs="?", help="e.g. m02_healthy_mid")
    parser.add_argument("--root", type=Path, default=gen.DATA_ROOT)
    parser.add_argument("--model", default=None)
    parser.add_argument("--quiet", action="store_true", help="hide the analysis trace")
    parser.add_argument("--list", action="store_true", help="list the generated merchants")
    args = parser.parse_args(argv)

    if args.list or not args.merchant_id:
        print("merchants:")
        for spec in gen.MERCHANT_SPECS:
            ready = "" if gen.is_complete(args.root / spec.merchant_id) else "   (not generated)"
            print(f"  {spec.merchant_id:24} {spec.profile}{ready}")
        if not args.merchant_id:
            print("\ngenerate them with:  python -m ledgermind.data.gen")
        return 0

    merchant_dir = args.root / args.merchant_id
    if not gen.is_complete(merchant_dir):
        print(f"{args.merchant_id} is not generated. Run: python -m ledgermind.data.gen")
        return 1

    provider = OllamaProvider(model=args.model) if args.model else OllamaProvider()
    merchant = load.load_merchant(merchant_dir)
    print(f"{args.merchant_id}   {len(merchant['transactions'])} transactions, "
          f"{len(merchant['sales'])} sales   via {provider.name}\n")

    try:
        decision = loop.run(merchant, provider, observer=_observer(not args.quiet))
    except loop.LoopError as exc:
        print(f"\nno decision reached: {exc}")
        return 1

    offer, risk = decision.offer, decision.risk
    print()
    if decision.declined:
        print(f"DECLINED   risk score {risk['risk_score']} of {risk['risk_score_max']}")
        print(f"  reason   {offer['decline_reason']}")
    else:
        print(f"OFFER      tier {risk['risk_tier']}, risk score {risk['risk_score']} "
              f"of {risk['risk_score_max']}")
        print(f"  advance              {offer['advance_amount']:>12,.2f}")
        print(f"  repayment            {offer['repayment_pct']:>11.1f}% of monthly revenue")
        print(f"  expected duration    {offer['expected_duration_months']:>12} months")
        print(f"  total repayable      {offer['total_repayable']:>12,.2f}")
    print(f"  policy version       {offer['policy_version']:>12}")

    print(f"\nMEMO  ({'templated fallback' if decision.used_fallback else 'model-written'}, "
          f"verified on attempt {decision.verification.attempt})")
    print(f"  {decision.memo}")

    if decision.provenance:
        print("\nPROVENANCE  every figure in the memo, and the analysis behind it")
        for entry in decision.provenance:
            print(f"  {entry.numeral:>14}  <-  {', '.join(sorted(set(entry.fact_keys)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
