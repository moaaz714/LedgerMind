---

description: "Task list for the grounded underwriting pipeline"
---

# Tasks: Grounded Underwriting Pipeline

**Input**: Design documents from `specs/001-grounded-underwriting-pipeline/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [data-model.md](./data-model.md), [constitution v1.0.1](../../.specify/memory/constitution.md)

**Tests**: Test tasks ARE included. Constitution Article IV requires every tool to have passing
tests against known-truth fixtures *before* it is wired into the agent loop, and spec SC-005 is a
test. Tests are not optional here.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths are included in every task

## Path Conventions

Single project. Package at `ledgermind/` and tests at `tests/`, both at repository root, per
[plan.md](./plan.md) "Project Structure".

## Phase ↔ Day mapping

The build plan's days and this file's phases are the same decomposition:

| Day | Phases | Completes |
|---|---|---|
| **Day 1 — Foundation** | Phase 1 (Setup) + Phase 2 (Foundational) | **No user story.** Pure prerequisite: generator, policy, tools, tests. |
| **Day 2 — Agent** | Phase 3 (US1) + Phase 4 (US2) | US1, US2 |
| **Day 3 — Proof & polish** | Phase 5 (US3) + Phase 6 (US4) + Phase 7 | US3, US4 |

Day 1 completing no user story is expected, not a gap. The spec slices vertically by value
delivered, so each story is independently testable; the day plan slices horizontally by dependency
order, because that is how the thing gets built. Phase 2 is where those two axes meet.

## Known deviation from plan.md

Three modules exist that plan.md's tree does not name. Each is recorded here rather than
smuggled in; reconcile with `/speckit-converge`.

- **`ledgermind/data/load.py`** (T008) — the plan covers generating merchant data but never
  names the module that reads and validates it, while FR-001 and FR-002 require exactly that.
- **`ledgermind/tools/trend.py`** (T009/T010) — both the revenue and volatility tools need the
  trend fit, and sharing it is what stops them disagreeing about where the line sits.
  Deliberately *not* shared with the generator, which keeps its own implementation so that
  agreement between them is evidence rather than tautology.
- **`ledgermind/agent/ledger.py`** (T021) — the plan put the fact ledger inside `loop.py`. Split
  out so it can be tested without importing the loop, and so the loop stays the ~150 lines it is
  meant to be.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton that imports cleanly and runs an empty test suite.

- [x] T001 Create the package skeleton with `__init__.py` in `ledgermind/`, `ledgermind/data/`, `ledgermind/tools/`, `ledgermind/llm/`, `ledgermind/agent/`, `ledgermind/guardrail/`, `ledgermind/eval/`, `ledgermind/app/`, and an empty `tests/` directory, matching plan.md exactly
- [x] T002 Create `requirements.txt` pinning the versions verified on Python 3.14.2 — `streamlit==1.64.0`, `pandas==3.0.6`, `numpy==2.5.3`, `pyarrow==25.0.1`, `altair==6.3.0`, plus `pytest` — and install it into `.venv`. Ollama is a local service, not a pip dependency
- [x] T003 Configure pytest in `pyproject.toml` (`[tool.pytest.ini_options]`, testpaths = `tests`) and add `tests/test_smoke.py` asserting every `ledgermind` subpackage imports

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Everything the agent will later depend on, verified against known truth before any
model is involved. **This phase is Day 1 and blocks all four user stories.**

**Ordering is not arbitrary** — `policy.py` precedes the tools so no bound can be written as an
inline literal, and `gen.py` precedes the tools so their tests assert against generated known truth
rather than fixtures invented to match the implementation.

- [x] T004 Implement `ledgermind/policy.py` holding every bound and threshold as named constants — advance cap basis, minimum viable advance, repayment-percentage band per tier, duration bounds, minimum months of history, volatility-to-stability-score bands, risk-tier thresholds — plus `POLICY_VERSION`. No numeric literal governing a decision may live anywhere else (Article IV)
- [x] T005 Implement `ledgermind/data/gen.py` generating one merchant as `transactions.csv`, `sales.csv`, and `truth.json` under `data/merchants/<merchant_id>/`, for profiles `"healthy"`, `"volatile"`, `"declining"`, `"thin_file"`, seeded from an explicit `seed` argument. Transaction fields are exactly `date` (ISO `YYYY-MM-DD`), `amount` (signed float, positive is inflow), `description`, `balance`; sales fields are exactly `date`, `amount` (always positive), per data-model.md
- [x] T006 Make generation idempotent in `ledgermind/data/gen.py` — generate a merchant only when its directory is absent or incomplete (missing any of the three files), never overwrite existing data on a normal run, and require an explicit `--force` flag that names which merchants it will overwrite before doing so (FR-031, FR-032). Add `data/merchants/` to `.gitignore`: it is a generated artifact, reproducible from its seed. Add `tests/test_gen_idempotent.py` asserting a second generate call leaves existing files untouched and that a directory missing `truth.json` is regenerated in full
- [x] T007 Write `truth.json` from `ledgermind/data/gen.py` with exactly the fields pinned in data-model.md — `merchant_id`, `profile`, `seed`, `true_monthly_revenue`, `true_avg_monthly_revenue`, `true_revenue_cv`, `true_overdraft_count`, `true_bounced_payment_count`, `true_large_one_off_count`, `true_months_covered` — and add `tests/test_gen_reproducible.py` asserting the same seed reproduces byte-identical CSVs and truth (FR-028)
- [x] T008 Implement `ledgermind/data/load.py` reading `transactions.csv` and `sales.csv` into plain dicts and validating shape before use; on a missing column, unparseable date, or empty record set it raises naming the specific offending column or row, and never coerces or drops a record (FR-002). Add `tests/test_load_validation.py` covering each rejection case
- [x] T009 [P] Implement `compute_revenue_metrics` in `ledgermind/tools/revenue.py` returning exactly `monthly_revenue` (`list[tuple[str, float]]`, ascending by month), `avg_monthly_revenue`, `total_revenue`, `mom_growth_pct`, `months_covered`, `period_start`, `period_end`, `trend` (`"rising"` | `"flat"` | `"declining"`). Stdlib only. Add `tests/test_revenue.py` asserting against a hand-computed 6-month fixture and against a generated merchant's `truth.json`
- [x] T010 [P] Implement `compute_volatility` in `ledgermind/tools/volatility.py` taking `monthly_revenue` and returning exactly `revenue_cv`, `revenue_stdev`, `stability_score` (int 0–100, derived from `policy.py` bands), `insufficient_history` (bool, true when months < the policy minimum). Add `tests/test_volatility.py` covering the thin-file case and the zero/near-zero revenue month case — `revenue_cv` must stay finite and defined, never a division by zero or infinity
- [x] T011 [P] Implement `detect_cashflow_flags` in `ledgermind/tools/flags.py` returning exactly `overdraft_count`, `bounced_payment_count`, `large_one_off_count`, `flag_count`, `min_balance`, `large_one_offs` (`list[dict]` of `{date, amount, description}`), `declining_balance` (bool). An empty result is a count of zero, never an absent fact. Add `tests/test_flags.py` asserting against generated merchants' `truth.json`
- [x] T012 Implement `score_risk` in `ledgermind/tools/scoring.py` taking revenue metrics, volatility, and flags and returning exactly `risk_score` (int 0–100 from `policy.py` weights), `risk_tier` (`"A"` | `"B"` | `"C"` | `"D"`), `declined` (bool), `decline_reason` (`str | None`, set only when declined), `drivers` (`list[str]`, non-numeric reasons). Add `tests/test_scoring.py` asserting the same inputs always yield the same tier (FR-007)
- [x] T013 Implement `compute_offer` in `ledgermind/tools/offer.py` returning exactly `advance_amount`, `repayment_pct`, `expected_duration_months`, `total_repayable`, `advance_cap_applied` (bool, true when the cap rather than the formula set the amount), `policy_version`. Add `tests/test_offer.py` asserting every term falls within `policy.py` bounds, and that a merchant whose advance computes below the minimum viable amount is declined explicitly rather than offered zero
- [x] T014 Implement `ledgermind/tools/registry.py` mapping each tool name to its callable and JSON schema, as the single source consumed by both the agent prompt and the dispatcher. Depends on T009–T013 all passing — Article IV forbids wiring a tool in before its tests are green
- [x] T015 Add `tests/test_import_boundaries.py` walking the imports of `ledgermind/tools/`, `ledgermind/agent/`, `ledgermind/guardrail/`, and `ledgermind/app/` and failing if any of them reach ground truth, and failing if any of them import `pandas` (Article III, plus the stdlib-only rule from plan.md)
- [x] T016 Generate the 20-merchant evaluation set across the four profiles via `ledgermind/data/gen.py`, and add `tests/test_tools_vs_truth.py` asserting every tool output agrees with each merchant's `truth.json` within stated tolerance (SC-005)

**Checkpoint — end of Day 1**: full test suite green, every financial figure in the system computed
by a tool and verified against known truth, with no model involved yet.

### Amendment A — sales reconciliation (FR-034, FR-035, SC-009)

Raised after Day 1 closed: the sales export was a required, validated input that no tool read.
These tasks add `reconcile_sales` and revisit T005, T007, T012, T014 and T016, which are already
checked off. They **gate T022**: the loop must be built against the final tool set, so the registry
the dispatcher consumes is settled before any model drives it. Article IV's order holds inside the
amendment — policy first, generator second, tool and its tests third, wiring last.

- [x] T042 [P] Add to `ledgermind/policy.py` the expected payment-processor fee, the period-level reconciliation tolerance, and the per-month tolerance, as named constants with a comment on why each value. Extend `tests/test_policy.py` with a static check that they are positive and that the per-month tolerance is not tighter than the period tolerance. The generator's own `PROCESSOR_FEE_PCT` must equal the policy's expected rate for a reconciling merchant, and a test asserts it, so the two cannot drift apart silently (FR-010, Article IV)
- [x] T043 Extend `ledgermind/data/gen.py` with a `sales_scale` field on `MerchantSpec` (default 1.0; sales amounts are generated as that multiple of the reconciling value) and add `m21_recon_overstated` and `m22_recon_understated`, healthy profile, fixed seeds, scales clearly outside the tolerance in each direction. Write `true_total_sales`, `true_reconciliation_ratio` and `true_reconciles` to `truth.json` per data-model.md, computed from the generator's own arithmetic and **not** by importing the tool (Article III). Add a test asserting merchants m01–m20 regenerate byte-identical to before, so adding fixtures provably disturbs nothing existing (FR-028)
- [x] T044 Implement `reconcile_sales` in `ledgermind/tools/reconcile.py` returning exactly the fields in data-model.md, over the union of months, excluding large one-offs from the banked side by the same rule as `flags.py` (share the threshold function rather than copy it). Stdlib only, pure. Add `tests/test_reconcile.py` asserting against a hand-computed fixture, a month with sales and no deposits, a month with deposits and no sales, a one-off inflow that must not count, an overshoot, and against every merchant's `truth.json`
- [x] T045 Amend `score_risk` in `ledgermind/tools/scoring.py` to take `reconciliation` and decline when `reconciled` is false, history checked first, adding no risk points (FR-035). The decline reason names the ratio and tolerance. Update `tests/test_scoring.py`: the existing weight and tier assertions must pass unchanged, which is the proof the weights were not disturbed, and add cases for a reconciliation decline and for history taking precedence
- [x] T046 Register `reconcile_sales` in `ledgermind/tools/registry.py` with namespace `reconcile`, parameters `transactions` and `sales` as labels, and add `reconciliation` to `score_risk`'s parameters. Correct the stale comment that says the model passes `sales` by name, which becomes true here. Update `tests/test_import_boundaries.py` if it enumerates tools. Depends on T044 and T045 passing
- [x] T047 Extend `tests/test_tools_vs_truth.py` to the 22-merchant set and add the SC-009 assertion: m21 and m22 are declined for reconciliation and every other merchant is not. The loader already returns `sales` from `load_merchant`, so no loader change is expected; confirm that

**Checkpoint — Amendment A**: full suite green over 22 merchants, SC-009 holding, every existing
merchant's outcome unchanged.

---

## Phase 3: User Story 1 — Produce a verified offer for one merchant (Priority: P1)

**Goal**: An analyst gets a memo and offer for one merchant where every figure is tool-computed and
verified.

**Independent test**: Run one complete merchant through the pipeline and confirm every numeral in
the memo resolves to a recorded fact, and every structured offer field is identical to the tool's
return value rather than an approximation of it.

- [x] T017 [US1] Define the `Provider` protocol in `ledgermind/llm/base.py` — a chat call taking messages plus tool schemas and returning either a tool call or prose. No provider-specific request or response type may cross this boundary (plan.md)
- [ ] T018 [US1] Implement `ledgermind/llm/ollama.py` as a plain-HTTP client against `http://127.0.0.1:11434` for model `qwen2.5`, satisfying the `Provider` protocol. Add `tests/test_ollama_provider.py` with the transport stubbed, so the suite does not require a running server
- [x] T019 [P] [US1] Define memo and offer structures in `ledgermind/agent/schema.py`. The offer carries the tool's return values; there is no field into which model-emitted numbers are parsed (Article I, FR-016)
- [ ] T020 [P] [US1] Write the system and tool-selection prompts in `ledgermind/agent/prompts.py`, stating that the model selects analyses and writes prose only, and that it will never be given raw transaction rows (FR-015)
- [x] T021 [US1] Implement the fact ledger in `ledgermind/agent/loop.py` — `FactLedgerEntry` with `key` (namespaced `<tool>.<field>`), `value`, `tool`, `call_index`. Scalars register directly; **sequences register per element** so `monthly_revenue` becomes `revenue.monthly_revenue.2026-01` and each `large_one_offs` amount registers as `flags.large_one_offs.0.amount`. Add `tests/test_fact_ledger.py` asserting a single month's revenue figure is resolvable, since without per-element registration a legitimate memo quoting one month would fail grounding
- [ ] T022 [US1] **(Blocked on Amendment A, T042–T047.)** Implement the hand-rolled agent loop in `ledgermind/agent/loop.py` — drive the provider, parse tool calls, dispatch through `registry.py`, append results to the transcript, repeat until the model produces prose. The dispatcher registers each tool return to the ledger **before** appending it to the transcript, so the model can never see a value that is not already recorded. Only the dispatcher writes to the ledger (FR-012). The model's context carries merchant identity, period covered, and tool results only — never raw rows (FR-015)
- [x] T023 [US1] Implement `ledgermind/guardrail/grounding.py` — extract every numeral from prose including cardinals spelled as words, and resolve each against the ledger using only the declared ladder: nearest 1, 10, 100, 1000, or one decimal place (FR-018, FR-019). Resolution is existence-based: a value matching two facts still grounds, and provenance lists all matches. Add `tests/test_grounding.py` asserting `47,812` / `47,800` / `48,000` all resolve to a recorded `47812.34` while `52,000` and `50,000` do not
- [x] T024 [P] [US1] Implement `ledgermind/guardrail/policy.py` verifying offer terms against `ledgermind/policy.py` bounds, returning the specific bound breached. A breach is surfaced as a defect in the offer logic, not as prose to regenerate (FR-022)
- [x] T025 [US1] Implement `check_output` in `ledgermind/guardrail/check.py` composing the grounding and policy checks into a `VerificationResult` with `passed`, `unresolved_numerals`, `policy_breaches`, `incomplete`, `attempt`, `used_fallback`. It sits between the loop and every consumer, so no code path reaches a display without passing through it (Article II)
- [ ] T026 [US1] Add `tests/test_us1_end_to_end.py` running one generated merchant through loop and guardrail, asserting a memo and offer are produced, every memo numeral resolves, and each structured offer field equals the tool's return value exactly

**Checkpoint**: US1 is independently demonstrable — one merchant in, verified memo and offer out.

---

## Phase 4: User Story 2 — Reject and recover when the narrative fails verification (Priority: P2)

**Goal**: An unverifiable memo never reaches the analyst, and repeated failure degrades visibly
rather than silently.

**Independent test**: Feed `check_output` a hand-written memo containing a fabricated figure and
confirm rejection — **with no model involved** — then exhaust the retry allowance and confirm the
labelled fallback appears.

- [x] T027 [US2] Add the completeness check to `ledgermind/guardrail/check.py` — a memo citing none of the decision's figures is rejected as incomplete, and a displayed memo must reference at least the advance amount, the repayment percentage, and one revenue metric (FR-021). Grounding alone rewards vagueness, so this closes the degenerate case where saying nothing scores perfectly
- [ ] T028 [US2] Add bounded regeneration to `ledgermind/agent/loop.py` — a declared maximum attempt count from `ledgermind/policy.py`, with the unresolved numerals fed back to the model as the reason for rejection (FR-023)
- [x] T029 [US2] Implement the deterministic fallback memo in `ledgermind/guardrail/check.py`, templated from recorded facts alone and setting `used_fallback` (FR-024). It must carry an explicit notice that narrative verification failed; unverified prose is never displayed and the fallback is never substituted silently (FR-025)
- [x] T030 [US2] Add `tests/test_us2_rejection.py` — a hand-written memo containing a figure absent from the ledger is rejected; a memo whose numeral matches two different facts still passes; retries exhausted yields the fallback with `used_fallback` set. All without invoking a model, so the guardrail is provable independently of model behaviour

**Checkpoint**: the grounding guarantee is demonstrable on its own, independent of what the model does.

---

## Phase 5: User Story 3 — Measure grounding and policy compliance (Priority: P3)

**Goal**: The central claim becomes a measured number rather than an assertion.

**Independent test**: Run the evaluation across all 22 merchants and confirm the report carries a
figure for each of the five measures, each computed against recorded ground truth.

- [ ] T031 [US3] Implement monotonicity in `ledgermind/eval/metrics.py` — for merchant pairs with equal revenue and differing volatility, assert the less stable merchant's advance is never larger (SC-001). Generate the paired fixtures via `ledgermind/data/gen.py`. This measure leads because it can fail while every other one passes, which makes it the one that tests the lending logic rather than the plumbing
- [ ] T032 [US3] Add the remaining four measures to `ledgermind/eval/metrics.py` — grounding faithfulness as the proportion of displayed numerals resolving to a recorded fact (SC-002), policy-breach count (SC-003), run-to-run consistency over three runs of the same merchant asserting identical structured offer fields (SC-004), and tool-versus-truth agreement (SC-005)
- [ ] T033 [US3] Implement `ledgermind/eval/harness.py` running the pipeline across all 22 merchants, collecting the five measures, and writing a report. Ground truth is read here and in `tests/` only (Article III)
- [ ] T034 [US3] Add a CLI entry point for the harness and `tests/test_eval_harness.py` asserting the report contains every defined measure

**Checkpoint**: the claim is now a number anyone can re-run.

---

## Phase 6: User Story 4 — Inspect a run in the interface (Priority: P4)

**Goal**: Grounding becomes visible rather than merely claimed.

**Independent test**: Run one merchant through the interface and confirm the analyst can see which
analyses ran, what each returned, and which analysis produced any given figure in the memo.

- [ ] T035 [US4] Implement `ledgermind/app/streamlit_app.py` with a merchant picker, a run trigger, and display of the memo and structured offer. Presentation only: the app must not compute, derive, or re-round any financial value (FR-030). This is the one module where `pandas` is permitted
- [ ] T036 [US4] Stream tool calls into the interface as they happen — each analysis shown when it runs, with what it returned
- [ ] T037 [US4] Add per-figure provenance display — selecting any figure in the memo names the analysis that produced it, listing all matching facts where resolution was ambiguous (FR-029)
- [ ] T038 [US4] Surface the fallback notice in the interface whenever `used_fallback` is set, and render the eval report (SC-007)

**Checkpoint**: the full demo path works end to end.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T039 Write `README.md` covering setup, how to generate merchants, how to run the pipeline, and how to run the evaluation — absorbing the `quickstart.md` content deliberately deferred in plan.md
- [ ] T040 Run `/speckit-converge` and reconcile this file against what was actually built, appending any remaining work as new tasks
- [ ] T041 Run the constitution's demonstration gate — full test suite green and the eval harness reporting grounding faithfulness and policy compliance over all 22 merchants

---

## Dependencies

```text
Phase 1 (T001-T003)
   └─► Phase 2 (T004-T016)          ← Day 1; blocks every story
         │   T004 policy ──► T012, T013        (no inline literals)
         │   T005,T007 gen ──► T009-T013       (tests assert vs known truth)
         │   T008 load ──► T009, T011          (tools need parsed records)
         │   T009,T010,T011 [P] ──► T012 ──► T013 ──► T014 registry
         │
         │   Amendment A (T042-T047): T042 ──► T043 ──► T044 ──► T045 ──► T046 ──► T047
         │                            └─ gates T022; revisits T005, T007, T012, T014, T016
         │
         ├─► Phase 3 US1 (T017-T026)   ← Day 2
         │     T017 ──► T018; T021 ──► T022; T023,T024 ──► T025 ──► T026
         │
         ├─► Phase 4 US2 (T027-T030)   ← Day 2, needs T025
         ├─► Phase 5 US3 (T031-T034)   ← Day 3, needs T025
         └─► Phase 6 US4 (T035-T038)   ← Day 3, needs T025
                                        └─► Phase 7 (T039-T041)
```

US2, US3, and US4 all depend only on `check_output` (T025) existing, so after Day 2 they are
genuinely independent of one another.

## Parallel Example: Phase 2

```text
# Once T004-T008 are done, these three are independent — different files,
# and their input shapes are pinned in data-model.md:
T009  compute_revenue_metrics  + tests   ledgermind/tools/revenue.py
T010  compute_volatility       + tests   ledgermind/tools/volatility.py
T011  detect_cashflow_flags    + tests   ledgermind/tools/flags.py
```

## Implementation Strategy

### Day 1 — foundation, no story completed

Phase 1 then Phase 2, in order. The checkpoint is the full suite green with every figure verified
against known truth and no model involved. Stop here even if there is time left: Article IV forbids
wiring a tool into the loop before its tests pass, and Day 2 is far easier to debug on a foundation
already known to be correct.

### Day 2 — MVP

Phase 3 (US1) gives the first demonstrable product. Phase 4 (US2) gives the thing that makes it
trustworthy, and is the better demo of the two — it is provable without the model.

### Day 3 — proof, then polish

Phase 5 (US3) before Phase 6 (US4). Evidence outranks interface: a measured grounding number
persuades a reviewer who never watches it run, while a nice interface proves nothing on its own.
Phase 6 is the first thing to cut if time runs short, and cutting it costs polish rather than the
central claim.

### Working rhythm

One task per sitting, each followed by an explanation and a stop. Commit per task or per coherent
group, on the single `001-grounded-underwriting-pipeline` branch, merging to `main` at each day's
checkpoint.

## Notes

- `[P]` marks tasks that touch different files with no incomplete dependency
- Tool and tests are **one** task, never two — Article IV ties them together
- Every bound lives in `ledgermind/policy.py`; an inline literal governing a decision is a defect
- Scope discipline: work may be dropped from the end of Phase 6 onward, never from the grounding or
  verification core (constitution, Development Workflow)
