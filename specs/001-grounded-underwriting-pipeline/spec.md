# Feature Specification: Grounded Underwriting Pipeline

**Feature Branch**: `001-grounded-underwriting-pipeline` *(spec directory; no separate git branch — no branch hook is installed)*

**Created**: 2026-10-02

**Status**: Draft

**Input**: User description: "Ingest a small business's bank transactions and sales records, run deterministic analyses, and produce a structured revenue-based-financing offer plus a written underwriting memo. Every figure in the memo is verified against analysis-computed values before it is shown; unverifiable narrative is rejected and regenerated, bounded, with a labeled deterministic fallback."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Produce a verified offer for one merchant (Priority: P1)

An underwriting analyst selects a merchant whose bank transactions and sales records are on file. The system analyses the merchant's revenue, stability, and cashflow health, decides a risk tier, computes offer terms, and presents a written memo explaining the decision alongside the structured offer. Every figure the analyst reads has been computed by an analysis and verified before display.

**Why this priority**: This is the product. An analyst who can obtain one defensible offer has something of value even if nothing else in this specification is built.

**Independent Test**: Select a single merchant with a complete history, run the pipeline, and confirm the analyst receives a memo and an offer in which every numeral is traceable to a recorded analysis result.

**Acceptance Scenarios**:

1. **Given** a merchant with 18 months of bank transactions and matching sales records, **When** the analyst requests an underwriting decision, **Then** the system presents a risk tier, an advance amount, a repayment percentage, an expected duration, and a written memo.
2. **Given** that memo, **When** every numeral in it is checked against the recorded analysis results, **Then** all of them resolve to a recorded value at a permitted rounding precision.
3. **Given** the presented offer, **When** each structured field is compared to the corresponding analysis result, **Then** the values are identical — not approximations of them.
4. **Given** a merchant whose cashflow contains overdrafts, **When** the system analyses it, **Then** the memo references the overdraft count and that count matches the recorded analysis result.
5. **Given** a merchant whose bank inflows do not reconcile with their sales records beyond the declared tolerance, **When** the system analyses it, **Then** the merchant is declined, and the decline memo cites the reconciliation ratio and the tolerance it was judged against, both of which match recorded analysis results.

---

### User Story 2 - Reject and recover when the narrative fails verification (Priority: P2)

A written memo containing a figure that no analysis produced never reaches the analyst. The system detects the unverifiable figure, discards the narrative, and requests a replacement. If replacements keep failing, the analyst is shown a plainly-worded summary built only from recorded analysis results, labelled as such, rather than unverified prose or an error.

**Why this priority**: This is the behaviour that makes the output trustworthy enough to lend against. It is also the one component that can be fully demonstrated without the narrative generator being involved at all.

**Independent Test**: Supply the verification step with a hand-written memo containing a figure absent from the recorded analysis results, and confirm it is rejected; then exhaust the retry allowance and confirm the labelled fallback appears.

**Acceptance Scenarios**:

1. **Given** a memo stating an average monthly revenue that no analysis produced at any permitted precision, **When** verification runs, **Then** the memo is rejected and is not displayed.
2. **Given** a rejected memo, **When** the retry allowance has not been exhausted, **Then** a replacement narrative is requested.
3. **Given** the retry allowance is exhausted, **When** the system must present a result, **Then it** presents a summary composed only of recorded analysis results, accompanied by an explicit notice that narrative verification failed.
4. **Given** an offer whose terms fall outside the declared policy bounds, **When** verification runs, **Then** the condition is surfaced as a defect in the offer logic and the offer is not displayed.
5. **Given** any failure in the verification path, **When** the system responds, **Then** no unverified narrative is displayed and no fallback is substituted without the analyst being told.

---

### User Story 3 - Measure grounding and policy compliance across the merchant set (Priority: P3)

An evaluator runs the pipeline across the full set of merchants whose correct answers are known in advance, and receives a report stating what proportion of displayed figures were traceable, whether any offer breached policy, whether the decisions behave sensibly as inputs vary, and whether repeated runs on the same merchant agree.

**Why this priority**: It converts the central claim from an assertion into a measurement. Valuable only once US1 exists, but it is what makes US1 credible to anyone who did not watch it run.

**Independent Test**: Run the evaluation across the merchant set and confirm the report contains a figure for each defined measure, each computed against the merchants' recorded correct answers.

**Acceptance Scenarios**:

1. **Given** the full merchant set, **When** the evaluation runs, **Then** the report states the proportion of displayed numerals that resolved to a recorded analysis result.
2. **Given** two merchants with identical revenue but different revenue stability, **When** both are underwritten, **Then** the less stable merchant's advance is not larger than the more stable one's.
3. **Given** one merchant underwritten three times, **When** the three structured offers are compared, **Then** they are identical.
4. **Given** each merchant's recorded correct answers, **When** each analysis result is compared against them, **Then** every analysis agrees within the stated tolerance.
5. **Given** a stated seed, **When** the merchant set is generated a second time from it, **Then** every merchant's records and recorded correct answers are identical to the first generation.

---

### User Story 4 - Inspect a run in the interface (Priority: P4)

An analyst watches the decision being made: which analyses ran, in what order, what each returned, and which analysis each figure in the memo came from.

**Why this priority**: It makes the grounding visible rather than merely claimed, which is what persuades a sceptical reviewer. It is also the only story whose absence costs nothing but polish, so it is the first candidate to drop if time runs short.

**Independent Test**: Run one merchant through the interface and confirm the analyst can see the sequence of analyses, each result, and the originating analysis for any figure in the memo.

**Acceptance Scenarios**:

1. **Given** a run in progress, **When** an analysis completes, **Then** the interface shows that it ran and what it returned.
2. **Given** a completed memo, **When** the analyst selects any figure in it, **Then** the interface names the analysis that produced that value.
3. **Given** a displayed financial figure, **When** it is compared with the recorded analysis result, **Then** the interface has neither recomputed nor re-rounded it.

### Edge Cases

- **Thin file**: a merchant with fewer months of history than the minimum required to assess stability. The system must state that the file is too thin rather than compute a stability figure from insufficient data.
- **Zero or near-zero revenue months**: growth and volatility measures must remain defined, and must not produce an undefined or infinite result.
- **Advance rounds to zero**: a merchant whose policy-capped advance computes below the minimum viable advance must be declined explicitly rather than offered zero.
- **Malformed or empty input**: missing columns, unparseable dates, or an empty record set must be rejected with a message naming the specific problem, never silently coerced or dropped.
- **No flags found**: an empty cashflow-flag result is a valid outcome and must be represented as a recorded fact (a count of zero), not as an absent one.
- **Narrative contains no numerals**: verification passes trivially, so a separate completeness requirement applies — a memo that cites none of the decision's figures is incomplete and is rejected.
- **Numeral in a non-financial context**: every numeral is in scope regardless of context, so any figure the narrative introduces must correspond to a recorded fact.
- **Numeral spelled as a word**: cardinal numbers written as words are treated as numerals, so a miscount cannot evade verification by being spelled out.
- **Ambiguous resolution**: when two different recorded facts hold the same value, a matching numeral is considered resolved — the claim being verified is that the figure came from an analysis, not which one. Provenance display lists every matching fact.
- **Retries exhausted**: covered by US2; the labelled deterministic fallback is the defined outcome.
- **Partially generated merchant**: a stored merchant missing any of its three artifacts is incomplete, and is regenerated in full rather than used or silently half-loaded (FR-031).
- **Sales and bank periods differ**: a month that has sales but no bank inflows, or inflows but no sales, is compared like any other month and shows as a full mismatch. Months are taken from the union of both periods, never silently trimmed to their overlap, because a merchant whose sales stop being banked must not look reconciled by having those months dropped.
- **Large one-off inflow in the bank record**: an exceptional inflow (an asset sale, say) is not a sale and has no counterpart in the sales export. It is excluded from the banked side of the reconciliation, using the same large-one-off rule the cashflow flags apply, so a genuine merchant is not declined for having sold a van.
- **Deposits that overshoot sales**: reconciliation is two-sided. Bank inflows materially above what the sales records imply are as unexplained as inflows materially below, and both fail it.

## Requirements *(mandatory)*

### Functional Requirements

**Input and validation**

- **FR-001**: System MUST accept, for a single merchant, a set of bank transaction records and a set of sales records covering the same period.
- **FR-002**: System MUST validate the shape of both inputs before any analysis runs, and MUST reject invalid input with a message naming the specific offending column, row, or value. It MUST NOT silently coerce values or drop records.
- **FR-003**: System MUST record the period covered — the first and last transaction dates and the number of whole months spanned — as named facts available for later reference.

**Analyses**

- **FR-004**: System MUST compute revenue metrics from transaction records, returning at minimum: a monthly revenue series, average monthly revenue, month-over-month growth rate, trend direction, and the number of months covered. The growth rate MUST be the average geometric growth per month, obtained by least-squares fit on log revenue. *(It MUST NOT be the arithmetic mean of month-over-month percentage changes: a series of 100, 50, 100 — flat over the period, starting and ending at the same level — has changes of −50% and +100%, averaging +25%, because a halving is −50% while the recovery that undoes it is +100%. On generated data that measure reported +709% growth for a flat merchant. Endpoint growth is also excluded — it reads only the first and last month, and gave a flat merchant a −28% trend. Months at or below zero are excluded from the fit but still counted in the mean and the residuals.)*
- **FR-005**: System MUST compute revenue stability from the monthly revenue series, returning at minimum: a coefficient of variation and a stability score. The coefficient of variation MUST be measured **after the trend is removed** — the expected series implied by the measured growth rate is subtracted and the variation of the residuals is reported. *(Measured on the raw series it would conflate trend with unpredictability: a perfectly smooth 2%-per-month growth line has a raw coefficient of variation of 0.106, and a smooth 12%-per-month decline reaches 0.52. A merchant with no month-to-month uncertainty whatsoever would score as volatile, and a declining merchant would be penalised once by the trend measure and again by the volatility measure. Detrending keeps the two measures independent, which is what their separate risk weights claim.)*
- **FR-006**: System MUST detect cashflow risk indicators from transaction records, returning at minimum: an overdraft count, a bounced-payment count, whether the balance trend is declining, and a count of large one-off movements together with the identified items. The overdraft count MUST count **episodes** — crossings from a non-negative balance to a negative one — not records with a negative balance. *(Counting records, a merchant who goes overdrawn and stays there scores one per transaction: in testing, merchants with two and zero overdrafts reported 137 and 96. "Three overdrafts" means three occasions, which is what an analyst reading the memo will understand it to mean.)*
- **FR-007**: System MUST assign a risk tier from the computed metrics using documented rules, and the same inputs MUST always produce the same tier.
- **FR-008**: System MUST compute offer terms — advance amount, repayment percentage, and expected duration — from the metrics and risk tier, with every term falling within the declared policy bounds.
- **FR-009**: Every analysis MUST return counts and time spans as explicitly named facts rather than leaving them implicit, so that any count or duration appearing in the narrative is verifiable.
- **FR-010**: All policy bounds MUST be declared in a single location and referenced from there; a bound MUST NOT be restated anywhere it is applied.

**Recorded facts**

- **FR-011**: System MUST maintain, for each run, a record of every value any analysis returned — each keyed by a stable name and carrying the identity of the analysis call that produced it.
- **FR-012**: Only the component that dispatches analyses MUST be able to add to that record. The narrative generator MUST NOT be able to add to it.
- **FR-013**: The record MUST remain inspectable after the run completes, such that any displayed figure can be traced to the analysis that produced it.

**Narrative generation**

- **FR-014**: System MUST allow the language model to choose which analyses to run and in what order, and MUST allow it to run further analyses in response to earlier results.
- **FR-015**: The language model's available context MUST contain only the merchant's identity, the period covered, and the results of analyses already run. It MUST NOT contain raw transaction or sales records.
- **FR-016**: The language model MUST produce prose only. The structured decision fields — risk tier, advance amount, repayment percentage, expected duration — MUST be taken from analysis results and MUST NOT be parsed from the model's output, even when the model's values appear correct.

**Verification**

- **FR-017**: Before any narrative is displayed, System MUST extract every numeral from it and attempt to resolve each one against the recorded facts for that run.
- **FR-018**: A numeral resolves if it equals a recorded value rounded to any precision on this declared ladder: nearest 1, nearest 10, nearest 100, nearest 1000, or one decimal place. A rung on the ladder applies only where the recorded value is **at least ten times that rung's unit**; coarser rungs do not apply. The recorded value itself always resolves. No tolerance beyond this is permitted. *(Worked example: a recorded average monthly revenue of 47812.34 is matched by "47,812", "47,800", and "48,000"; it is not matched by "52,000" or "50,000".)*

  *(The magnitude condition was added after the ladder, read literally, was found to permit nonsense. Rounding a small value to a coarse rung collapses it: nearest-10 of a recorded overdraft count of 3 is **0**, and nearest-10 of a recorded repayment percentage of 13 is **10** — so "no overdrafts" resolved against a count of three, and "repaid at 10%" against a rate of thirteen. Both are material misstatements that passed. Requiring the value to be at least ten times the rung bounds the rounding error at 5% of the figure, which is what the ladder was intended to express: that a memo may round sensibly, not that it may round a number into a different number.)*
- **FR-019**: All numerals are in scope, including counts and durations, not only currency amounts and percentages. Cardinal numbers written as words MUST also be resolved.
- **FR-020**: A narrative containing any numeral that does not resolve MUST be rejected and MUST NOT be displayed.
- **FR-021**: A narrative that cites none of the decision's figures MUST be rejected as incomplete. For an **approved** merchant, a displayed memo MUST reference at least the advance amount, the repayment percentage, and one revenue metric. For a **declined** merchant — which has neither an advance nor a repayment percentage — it MUST reference the decline reason and at least one metric that drove the decision. *(The original wording required the advance and repayment percentage unconditionally, which made every decline memo permanently unsatisfiable: the check would reject each attempt until the retry allowance ran out and the fallback fired. Surfaced while designing the risk policy.)*
- **FR-022**: Before any offer is displayed, System MUST verify its terms against the declared policy bounds. A breach MUST be surfaced as a defect in the offer logic, not treated as narrative to regenerate.

**Failure handling**

- **FR-023**: Narrative regeneration MUST be bounded by a declared maximum number of attempts.
- **FR-024**: When the attempt allowance is exhausted, System MUST present a summary composed solely of recorded facts, and MUST label it as a fallback shown because narrative verification failed.
- **FR-025**: System MUST NOT display unverified narrative under any condition, and MUST NOT substitute the fallback without informing the analyst.

**Evaluation**

- **FR-026**: System MUST provide an evaluation that runs the pipeline across the full merchant set and reports: the proportion of displayed numerals that resolved, any policy breaches, the sensibility of decisions as inputs vary, agreement between repeated runs, and agreement between each analysis and the merchant's recorded correct answers.
- **FR-027**: Each merchant's recorded correct answers MUST be stored separately from the records the analyst and the system see, and MUST NOT be readable by any component in the decision path.
- **FR-028**: Merchant generation MUST be reproducible from a stated seed.

**Presentation**

- **FR-029**: The interface MUST display the memo, the structured offer, and the provenance of each displayed figure.
- **FR-030**: The interface MUST NOT compute, derive, or re-round any financial value; it displays what the analyses produced.

**Data persistence**

- **FR-031**: Generated merchant data MUST persist on disk at a stable location and MUST be generated only when absent. A run MUST NOT regenerate or overwrite merchant data that already exists. A merchant whose stored data is incomplete — missing any of its records or its recorded correct answers — MUST be treated as absent and regenerated in full. *(This is not only convenience: SC-004 measures whether three runs of the same merchant agree, which is meaningless if the merchant's history changes between runs. Stable inputs are a precondition for the measurement.)*
- **FR-032**: Overwriting existing merchant data MUST require an explicit, deliberate action, and MUST state which merchants will be overwritten before doing so.

**Declined merchants**

- **FR-033**: A declined merchant MUST receive a written memo explaining the refusal, held to the same grounding standard as an approved one. A decision to refuse credit has to be defensible to a credit committee in the same way a decision to extend it does.

**Sales reconciliation**

- **FR-034**: System MUST compare the sales records against the bank transaction records, returning at minimum: total sales, the banked total expected from those sales after the declared payment-processor fee, the banked total actually received (excluding large one-off inflows), the ratio of actual to expected, the number of months compared, the number of months whose own ratio falls outside the declared per-month tolerance, the largest single-month deviation, whether the period reconciles, and the tolerance applied. The comparison MUST cover every month in the union of the two periods. The expected processor fee and every tolerance MUST be declared in the policy module (FR-010) and MUST NOT be restated in the analysis. *(Without this the sales export is a required, validated input that nothing reads: FR-001 asks for two files and the system would use one, and the independent view of revenue named in the Sales Record entity would never be consulted.)*
- **FR-035**: A merchant whose period does not reconcile MUST be declined, with a decline reason that names the reconciliation ratio and the tolerance. The history check precedes it: a file too short to assess is declined for that reason first. Reconciliation MUST NOT contribute to the risk score, so the declared score weights are unchanged. *(Revenue that cannot be verified is not a riskier version of the same merchant, it is an unknown one, and assigning it risk points would price a number nobody has confirmed. Declining also keeps the 40/25/25/10 weights, and the explanation for them, intact.)*

### Key Entities

- **Merchant**: a small business being underwritten. Carries an identity, a period of history, and a profile (healthy, volatile, declining, or thin-file).
- **Transaction**: one movement in the merchant's bank record — date, amount, direction, description, and resulting balance.
- **Sales Record**: one sale in the merchant's sales export — date and amount; the independent view of revenue against which bank inflows can be compared.
- **Ground Truth**: the correct answers for a generated merchant, recorded at generation time. Readable only by tests and the evaluation. Never reachable from the decision path.
- **Revenue Metrics**: the monthly revenue series and the measures derived from it — average, growth, trend, months covered.
- **Sales Reconciliation**: the comparison of banked inflows against the sales export — expected and actual banked totals, their ratio, per-month deviations, and whether the period reconciles within tolerance.
- **Cashflow Flag**: one identified risk indicator — its kind, the records that evidence it, and its severity.
- **Risk Assessment**: the assigned tier and the metric values that drove it.
- **Offer**: advance amount, repayment percentage, expected duration, and the policy bounds that constrained them.
- **Recorded Fact**: one value an analysis returned — its stable name, its value, and the analysis call that produced it.
- **Memo**: the written justification. The only artefact authored by the language model.
- **Verification Result**: the outcome of checking a memo and offer — pass or fail, and for a failure, which numerals did not resolve or which bound was breached.
- **Evaluation Report**: the measured outcomes across the merchant set.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Holding revenue constant and increasing revenue volatility never increases the advance amount, across every paired merchant in the set.
- **SC-002**: 100% of numerals in displayed narrative resolve to a recorded fact, across the full merchant set.
- **SC-003**: Zero displayed offers breach the declared policy bounds, across the full merchant set.
- **SC-004**: The same merchant underwritten three times produces identical structured offer fields on all three runs.
- **SC-005**: Every analysis result agrees with the merchant's recorded correct answer within the stated tolerance, for every merchant in the set.
- **SC-006**: A memo containing a fabricated figure is rejected on 100% of attempts.
- **SC-007**: Every fallback shown is labelled as a fallback; no fallback is ever shown unlabelled, and no unverified narrative is ever shown.
- **SC-008**: An analyst receives a completed memo and offer within 90 seconds of requesting a decision, so a decision can be produced live in front of a reviewer.
- **SC-009**: Every merchant generated with a deliberate sales/bank mismatch is declined for that reason, and no merchant generated with reconciling records is declined for it, across the full merchant set.

SC-001 is stated first deliberately. SC-002 through SC-004 verify that the plumbing works; SC-001 can fail while all of them pass, which makes it the criterion that actually tests whether the lending logic is sane.

## Assumptions

- Merchant data is synthetic. No real business's financial records enter this project.
- A single currency throughout; no foreign-exchange handling.
- Revenue is assessed at monthly granularity.
- Each merchant has between 12 and 18 months of history, except those deliberately generated as thin-file cases.
- One policy regime applies to all merchants; there are no per-merchant underwriting rules or overrides.
- The merchant set for evaluation contains 22 merchants: twenty spanning four profiles (healthy, volatile, declining, and thin-file), plus two healthy-profile reconciliation fixtures whose sales records deliberately disagree with their bank inflows, one overstated and one understated. They are added rather than substituted for existing merchants so that every existing merchant's records, truth and expected outcome are unchanged.
- Retrieval of underwriting policy text to support justifications is an explicit non-goal of this specification.
- One analyst at a time; no concurrent use, no multi-user state, no authentication.
- The decision is advisory. No funds move, and no downstream lending system is integrated.
- Revenue means **net cash received into the bank account**. The sales export lists individual sales; those are batched into bank deposits with a payment-processor fee deducted, so gross sales exceed banked revenue slightly. Net is the right basis because repayment is taken from cash that actually arrives. The expected processor fee is a declared policy rate; reconciliation judges the banked total against sales net of that rate, so a difference of the size the fee explains is not a mismatch.
- Large one-off inflows are detected and reported but are **not** excluded from the revenue basis, so a
  merchant with an exceptional inflow may be sized against revenue that is not fully recurring. A
  deliberate simplification: excluding them would introduce a second revenue figure, and both the memo
  and the grounding check would then have to disambiguate which of the two any given claim refers to.
