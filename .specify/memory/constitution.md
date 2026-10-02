<!--
SYNC IMPACT REPORT — amendment review scratch, remove before committing
Version change: (none) → 1.0.0  [initial ratification]
Principles defined:
  - I.   Deterministic Financial Computation (NON-NEGOTIABLE)   [new]
  - II.  No Unverified Output                                   [new]
  - III. Ground Truth Is the Measuring Instrument               [new]
  - IV.  Pure, Independently Testable Tools                     [new]
Template slot PRINCIPLE_5 intentionally dropped: four falsifiable articles cover the
  project's non-negotiables; a fifth would restate them or add generic filler.
Sections added:
  - Additional Constraints (filled SECTION_2)
  - Development Workflow & Quality Gates (filled SECTION_3)
  - Governance
Removed sections: none.
Deferred TODOs: none. All placeholder tokens replaced.

AMENDMENT 1.0.0 → 1.0.1 (2026-10-02) — PATCH, wording clarification only.
  Article I previously forbade the model to "restate" a financial figure. That was too broad:
  the memo necessarily quotes figures, and Article II's grounding check exists precisely to
  verify those quotations. If the model could never utter a number, no grounding check would
  be needed. Amended to forbid ORIGINATION while permitting QUOTATION of ledger values.
  No principle added, removed, or redefined — hence PATCH, not MINOR.
  Surfaced by writing spec 001 (see specs/001-grounded-underwriting-pipeline/spec.md FR-016
  to FR-020, and that spec's requirements checklist, "Noted for amendment").
-->

# LedgerMind Constitution

## Core Principles

### I. Deterministic Financial Computation (NON-NEGOTIABLE)

- The LLM MUST NOT compute, estimate, or derive any financial figure.
- Every number that reaches a user MUST originate as the return value of a deterministic
  Python tool.
- The model MAY quote a figure already recorded in the fact ledger — the memo would be useless
  otherwise — but MUST NOT introduce a figure absent from it. The governing distinction is
  **origination versus quotation**: the model may repeat a value a tool produced, never produce
  one of its own. Every quotation is verified under Article II.
- The structured decision fields — advance amount, repayment percentage, expected duration,
  and risk tier — MUST be rendered directly from tool return values. They MUST NOT be parsed
  out of model output, even when the model's value appears correct.
- The model's authority is limited to two things: selecting which tools to call and with what
  arguments, and writing natural-language prose.

**Rationale:** the output is meant to be trustworthy enough to lend real money against. A
figure the model produced cannot be audited back to an input; a figure a tool produced can.
Removing the model from the numeric path eliminates the entire failure class rather than
attempting to detect it after the fact.

### II. No Unverified Output

- Generated prose MUST NOT reach a user before passing both the grounding check and the policy
  check.
- **Grounding:** every numeral appearing in generated prose MUST resolve to a value present in
  the fact ledger — the record of tool outputs accumulated during that run — within an
  explicitly declared rounding tolerance. An unresolvable numeral is a rejection.
- **Policy:** offer terms MUST fall within the declared policy bounds. A violation is treated
  as a defect in the offer logic, not as prose to regenerate.
- Failure handling MUST be bounded: a fixed maximum number of regeneration attempts, after
  which the system falls back to a deterministic memo templated from the fact ledger alone.
- The system MUST NOT fail open — unverified prose is never shown. The system MUST NOT fail
  silently — when the fallback is used, the user is told that narrative verification failed.

**Rationale:** a guardrail that can be bypassed under retry pressure, or that hides its own
activation, provides the appearance of safety rather than safety. Bounded failure with a
visible, honest degradation is the only version of this that survives contact with production.

### III. Ground Truth Is the Measuring Instrument

- Synthetic merchants MUST be generated together with their ground truth, recorded as an
  artifact separate from the agent-visible inputs.
- Ground truth MUST be readable only by tests and the eval harness. No code path reachable by
  the agent may read it.
- Generation MUST be reproducible from an explicit seed.
- Any claim about grounding faithfulness, policy compliance, decision sanity, or run-to-run
  consistency MUST be supported by a number the eval harness produced.

**Rationale:** "the system does not invent figures" is unfalsifiable without a known-correct
answer to compare against. Synthetic data is not a convenience here — it is the only reason
the central claim of this project can be tested rather than asserted.

### IV. Pure, Independently Testable Tools

- Each analysis tool MUST be a pure function: inputs to return value, with no file or network
  I/O, no LLM calls, and no mutable module state.
- Policy constants MUST live in a single module. An inline numeric literal in scoring or offer
  logic is a defect.
- Each tool MUST have tests verifying it against known-truth fixtures, and those tests MUST
  pass before the tool is wired into the agent loop.

**Rationale:** the agent loop is only debuggable if the layer beneath it is already known to be
correct. When an agent run produces a wrong decision, the question must be answerable without
re-litigating the arithmetic.

## Additional Constraints

- **Language:** Python.
- **LLM:** local via Ollama, model Qwen 2.5 7B, chosen to fit entirely within 6 GB VRAM. It MUST
  sit behind a provider-agnostic interface; no provider-specific request or response type may
  leak past that boundary. Declared fallback if 7B tool-calling proves too weak: Groq's free
  tier (Llama 3.3 70B).
- **Agent harness:** hand-rolled. No agent framework (no LangChain, ADK, or AgentKit).
- **Guardrails:** hand-rolled validators. No guardrails library — grounding and policy are
  bespoke business logic defined by ground truth only this project holds.
- **Scoring:** rule-based. No trained or statistical model; no labeled data required.
- **UI:** Streamlit, strictly a presentation layer over the pipeline. The UI MUST NOT compute,
  round, or reformat a financial value in a way that changes it.
- **Data:** synthetic only. No real merchant financial data enters this repository.

Rationale for the hand-rolled choices: the grounding path is the load-bearing claim of this
project, so it MUST be fully owned and inspectable. A framework that hides the tool-dispatch
step also hides the point at which the fact ledger is populated.

## Development Workflow & Quality Gates

- Development is spec-driven via Spec Kit. The constitution, spec, plan, and tasks are committed
  artifacts and are reviewed before the code they govern is written.
- Deterministic tools and their tests precede agent code. The agent is built against a verified
  foundation, never alongside an unverified one.
- Each unit of work is explained — its inputs, outputs, place in the data path, and the
  alternative rejected — before the next unit begins.
- **Scope discipline:** functionality may be dropped from the end of the plan. It MUST NEVER be
  dropped from the grounding or verification core. A demo without the policy-RAG stretch goal is
  on-spec; a demo without the grounding check is not this project.
- **Gate before any demonstration:** the full tool test suite passes, and the eval harness
  reports grounding faithfulness and policy compliance over the synthetic merchant set.

## Governance

- This constitution supersedes convention, habit, and ad-hoc instruction. `CLAUDE.md` is runtime
  development guidance and is subordinate to this document; where they conflict, the constitution
  wins and `CLAUDE.md` is corrected.
- Amendments MUST be recorded in this file with a version bump following semantic versioning:
  MAJOR for removing or redefining a principle, MINOR for adding one or materially expanding
  guidance, PATCH for clarifications and wording.
- Any change touching the fact ledger, the grounding check, the policy check, or offer
  computation MUST state how it complies with Articles I and II. "It passes the tests" is not
  sufficient where the tests themselves are being changed.
- Added complexity MUST be justified by naming the article it serves. Complexity that serves no
  article is removed.

**Version**: 1.0.1 | **Ratified**: 2026-10-02 | **Last Amended**: 2026-10-02
