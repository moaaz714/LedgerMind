# Implementation Plan: Grounded Underwriting Pipeline

**Branch**: `001-grounded-underwriting-pipeline` *(spec directory; no separate git branch)* | **Date**: 2026-10-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-grounded-underwriting-pipeline/spec.md`

## Summary

Deterministic Python tools compute every financial figure for a merchant. A hand-rolled agent loop
lets a local language model choose which tools to run and write the underwriting memo, while the
loop's dispatcher records every tool return into a per-run **fact ledger**. A guardrail sits between
the loop and every consumer: it resolves each numeral in the memo against the ledger and checks the
offer against policy bounds, rejecting and regenerating on failure within a bounded allowance. The
structured offer the user sees is the tool's return value, never parsed from model output.

The architecture follows from one constraint: the model must be structurally unable to originate a
number, not merely instructed not to.

## Technical Context

**Language/Version**: Python 3.14.2 — the only version installed on the development machine.
Streamlit 1.64.0, pandas 3.0.6, numpy 2.5.3, pyarrow 25.0.1 and altair 6.3.0 were verified working
on it empirically on 2026-10-02: all installed from wheels with no source builds, the
`pandas` → `pyarrow` round-trip succeeds, and `streamlit run` boots and serves headlessly. No
fallback interpreter is needed.

**Primary Dependencies**: standard library only for every deterministic tool (`csv`, `datetime`,
`statistics`). Ollama serving Qwen 2.5 7B locally, reached through a provider-agnostic interface.
Streamlit and pandas at the presentation edge only. pytest for tests.

**Storage**: files. No database. Each generated merchant is a directory holding `transactions.csv`,
`sales.csv`, and `truth.json`.

**Testing**: pytest. Tool tests assert against hand-computed fixtures and against each merchant's
`truth.json`.

**Target Platform**: local developer machine (Windows). Offline-capable by design — merchant
financial data never leaves the device.

**Project Type**: single Python package with a Streamlit entry point and a CLI entry point for the
evaluation harness.

**Performance Goals**: a complete decision in under 90 seconds, from spec SC-008, so a decision can
be produced live in front of a reviewer.

**Constraints**: the model must fit within 6 GB of VRAM (RTX 2060). The agent's context must stay
small, which FR-015 — tool outputs only, never raw transaction rows — also serves.

**Scale/Scope**: 22 synthetic merchants (20 across the four profiles plus two sales-reconciliation fixtures), roughly 400 transactions each, 12–18 months of history.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution **v1.0.1**.

| Article | Requirement | Design element that satisfies it |
|---|---|---|
| **I** — Deterministic financial computation | The model must not originate a figure; structured fields come from tools | `tools/` are the only numeric source. `agent/loop.py` builds the model's context from tool returns alone and never passes raw rows (FR-015). The `Offer` the interface renders is `compute_offer`'s return value; nothing parses numbers out of model output. |
| **II** — No unverified output | Nothing displays before grounding and policy checks pass; bounded failure, visible fallback | `guardrail/check.py` sits between the loop and **every** consumer — interface and evaluation alike — so no code path reaches a display without passing through it. Bounded retries and the templated fallback live in the loop. |
| **III** — Ground truth is the measuring instrument | Truth separate from agent-visible data, unreachable from the decision path | `data/gen.py` writes `truth.json`; only `tests/` and `eval/` import it. **Structural rule: no module under `tools/`, `agent/`, `guardrail/`, or `app/` may import truth.** This is enforced by a test that walks those packages' imports, not left to discipline. |
| **IV** — Pure, independently testable tools | Pure functions, centralised policy constants, tests before wiring | `tools/` take plain Python values and return plain Python values — no I/O, no model calls, no module state. `policy.py` is the single home for every bound. Stdlib-only keeps purity trivial to honour. |

**Result: PASS — zero violations.** Complexity Tracking below is therefore empty.

That clean result deserves an honest caveat rather than a victory lap: this constitution was written
for this architecture, in the same session, by the same author. The gate is not adversarial here and
should not be presented as though it were. Its value is different — it makes each design element's
*purpose* explicit, so a later change that quietly breaks one (an interface that re-rounds a figure,
a convenience import of `truth.json` into a tool) has something concrete to fail against. The one
place the gate did real work was in the other direction: writing the spec exposed that Article I's
original wording forbade the memo from quoting figures at all, which produced amendment v1.0.1.

## Project Structure

### Documentation (this feature)

```text
specs/001-grounded-underwriting-pipeline/
├── spec.md              # What the system must do
├── plan.md              # This file
├── data-model.md        # Tool return shapes = the fact ledger's keys
├── checklists/
│   └── requirements.md  # Spec quality gate
└── tasks.md             # Created by /speckit-tasks, at the start of Day 1
```

### Source Code (repository root)

```text
ledgermind/
├── policy.py              # Every policy bound and threshold. Single source (Article IV).
├── data/
│   └── gen.py             # Synthetic merchants → transactions.csv, sales.csv, truth.json
├── tools/
│   ├── revenue.py         # compute_revenue_metrics
│   ├── volatility.py      # compute_volatility
│   ├── flags.py           # detect_cashflow_flags
│   ├── reconcile.py       # reconcile_sales (FR-034)
│   ├── scoring.py         # score_risk
│   ├── offer.py           # compute_offer
│   └── registry.py        # name → (callable, schema). Single source for prompt + dispatcher.
├── llm/
│   ├── base.py            # Provider protocol
│   ├── ollama.py          # Local provider
│   └── groq.py            # Declared fallback provider
├── agent/
│   ├── loop.py            # Hand-rolled loop. The dispatcher owns the fact ledger.
│   ├── prompts.py
│   └── schema.py          # Memo and offer shapes
├── guardrail/
│   ├── grounding.py       # Numeral extraction and ledger resolution
│   ├── policy.py          # Offer bound checks
│   └── check.py           # check_output: the single gate before display
├── eval/
│   ├── harness.py
│   └── metrics.py
└── app/
    └── streamlit_app.py   # Presentation only (FR-030)

tests/
```

**Structure Decision**: one package at the repository root with `tests/` alongside it. The
template's web-application and mobile layouts were deleted as inapplicable — there is no network
service and no client/server split. The package boundaries are drawn to match the constitution
rather than by technical layer: `tools/` is the only place numbers are produced, `guardrail/` is the
only place they are verified, and `truth.json` is reachable from neither. A boundary that maps onto
an article is a boundary a reviewer can check.

## Design Rationale

The decisions below exist nowhere else in the repository — `CLAUDE.md` is deliberately gitignored as
a local working document — so this section is their only committed home.

**Local Ollama rather than a hosted API.** Merchant financial data never leaves the device, which is
the right default for the domain even with synthetic data. It is also free and unlimited, so the
evaluation can run 22 merchants × 3 repetitions as often as needed; a metered API would create
pressure to measure less, exactly where measurement is the point.

**Qwen 2.5 7B rather than 14B.** 7B fits entirely within the available 6 GB of VRAM and runs fast.
14B spills to CPU and system RAM, which makes the iteration loop painful enough to change how the
project gets built. This is a hardware-shaped choice made deliberately, not a quality compromise
accepted silently — and the structured offer never depends on model quality, so the cost of a
smaller model falls on prose quality and tool-selection reasoning, not on correctness.

**A provider-agnostic interface.** Whether a 7B is reliable enough at tool-calling is this project's
main technical risk. `llm/base.py` makes the bet reversible in an afternoon: the declared fallback
is Groq's free tier (Llama 3.3 70B). No provider-specific request or response type may cross that
boundary.

**A hand-rolled agent loop rather than LangChain, ADK, or AgentKit.** The dispatch step is precisely
where the fact ledger is populated, and a framework that abstracts dispatch also hides the seam the
entire grounding guarantee depends on. The loop is roughly 150 lines, so the cost of owning it is
low and the benefit is that Article I is structural rather than aspirational. A framework becomes
the right call at a threshold this project does not reach: multiple cooperating agents, a tool count
large enough that routing needs its own logic, production requirements for retry/observability/
tracing, or a team maintaining the loop rather than one author. Scale would change the answer;
nothing here is an argument that frameworks are wrong.

**Hand-rolled guardrails rather than a guardrails library.** Grounding here is defined against ground
truth only this project holds. No off-the-shelf validator can answer "does this numeral appear in my
fact ledger at a permitted rounding precision", because the ledger is a per-run artifact of this
pipeline.

**Rule-based scoring rather than a trained model.** There are no labeled lending outcomes to train
on. More importantly, a rule-based tier can be explained to a credit committee line by line, which
is the actual product requirement — an unexplainable risk score would fail the use case even if it
were more accurate.

**Sales reconciliation as a decline condition, not a score component (amendment).** The sales
export was a required, validated input that no tool read. `reconcile_sales` closes that, and
`score_risk` takes its result and declines a merchant whose period does not reconcile. It adds no
risk points, for two reasons. Adding a fifth component would force re-weighting the 40/25/25/10
split, which is the one judgement call the whole scoring rests on, and that split has a written
rationale this change would have to rewrite. And unverifiable revenue is not a riskier version of
the same merchant, it is an unknown one, so pricing it would put a number on something nobody has
confirmed. The cost is that the check is binary: a merchant just inside the tolerance is scored as
though fully verified. The tolerance is therefore a policy constant, visible and changeable in one
place.

*Compliance with Articles I and II (constitution, Governance).* Article I: the tool is pure,
computes every figure it returns in code, and the model passes only the labels `"transactions"`
and `"sales"`, never rows, so no figure originates with the model. Article II: every numeric
return is registered by the dispatcher before the transcript sees it, so a memo citing the ratio
or the tolerance resolves against the ledger like any other figure. A reconciliation decline
carries a decline memo held to FR-033's standard. Article III: the generator keeps its own
reconciliation arithmetic rather than importing the tool's, so agreement between them is evidence
and not tautology, as with the trend fit. Article IV: tolerances and the expected processor fee
live in `policy.py`, and the tool's tests pass before `registry.py` wires it in.

**Where the fact ledger is written.** In the dispatcher, immediately after a tool returns, *before*
the result is appended to the model's transcript. That ordering is the whole mechanism: the model
cannot see a value that was not first recorded, so anything it quotes is by construction already in
the ledger. One chokepoint, one invariant.

## Deliberately Not Produced

The plan template offers three further Phase 0/1 artifacts. Their absence is a decision, not an
omission:

- **`research.md`** — skipped. Its purpose is to resolve `NEEDS CLARIFICATION` markers, and the spec
  carries none; every open question was settled before the spec was written. The file would be empty.
- **`contracts/`** — skipped. The system exposes no API, no CLI schema consumed by another system,
  and no wire format. Its only interface contract is the tool return shapes, which `data-model.md`
  covers.
- **`quickstart.md`** — deferred, not skipped. A validation guide should describe commands that
  actually run; written now, before any code exists, it would be fiction. It belongs with the README
  on Day 3, once there is something to validate.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

No violations. No entries.
