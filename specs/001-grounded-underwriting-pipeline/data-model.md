# Data Model: Grounded Underwriting Pipeline

**Date**: 2026-10-02 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

The field names below are not incidental. They become the **fact ledger's keys**, which are what the
grounding check resolves numerals against (FR-011, FR-017). Renaming a field is therefore a change to
the verification contract, not a refactor.

## Input entities

### Transaction

One movement in the merchant's bank record. Read from `transactions.csv`.

| Field | Type | Notes |
|---|---|---|
| `date` | `str` | ISO `YYYY-MM-DD` |
| `amount` | `float` | Signed. Positive is an inflow. |
| `description` | `str` | Free text; drives flag detection (e.g. overdraft fees) |
| `balance` | `float` | Running balance after this movement |

### SalesRecord

One sale from the merchant's sales export. Read from `sales.csv`. The independent view of revenue,
against which bank inflows can be cross-checked.

| Field | Type | Notes |
|---|---|---|
| `date` | `str` | ISO `YYYY-MM-DD` |
| `amount` | `float` | Always positive |

## Tool return shapes

Every tool is a pure function taking plain Python values and returning a plain mapping
(Article IV). Numeric fields are groundable; string and boolean fields are recorded but are not
numeral-match candidates.

### `compute_revenue_metrics(transactions) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `monthly_revenue` | `list[tuple[str, float]]` | each element | `("2026-01", 47812.34)`, ascending by month |
| `avg_monthly_revenue` | `float` | yes | |
| `total_revenue` | `float` | yes | |
| `mom_growth_pct` | `float` | yes | Average geometric growth per month, by least squares on log revenue (FR-004). Also defines the trend line `compute_volatility` subtracts |
| `months_covered` | `int` | yes | Whole months spanned. Exists to satisfy FR-009/FR-019. |
| `period_start` | `str` | no | ISO date |
| `period_end` | `str` | no | ISO date |
| `trend` | `str` | no | `"rising"` \| `"flat"` \| `"declining"` |

### `compute_volatility(monthly_revenue) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `revenue_cv` | `float` | yes | **Detrended**: `revenue_stdev ÷ mean(monthly_revenue)`. See FR-005 |
| `revenue_stdev` | `float` | yes | Standard deviation of the **residuals** after subtracting the trend line |
| `stability_score` | `int` | yes | 0–100, derived from `revenue_cv` via `policy.py` bands |
| `stability_score_max` | `int` | yes | The scale (100). Returned so prose may write "95 out of 100" — a bare denominator with no recorded value behind it is rejected as fabricated |
| `insufficient_history` | `bool` | no | `True` when months < the policy minimum (thin-file edge case) |

### `detect_cashflow_flags(transactions) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `overdraft_count` | `int` | yes | **Episodes** — crossings from non-negative to negative balance, not negative rows (FR-006) |
| `bounced_payment_count` | `int` | yes | |
| `large_one_off_count` | `int` | yes | |
| `flag_count` | `int` | yes | Overdrafts + bounced payments + one-offs, plus 1 if `declining_balance`. Exists so a memo phrase like "four cashflow flags" has a fact to resolve against |
| `min_balance` | `float` | yes | Lowest balance observed |
| `large_one_offs` | `list[dict]` | each `amount` | `{date, amount, description}`. An inflow above `Q3 + 3×IQR` of the merchant's own inflows |
| `declining_balance` | `bool` | no | |

An empty result is a count of zero, never an absent fact (spec edge case).

### `reconcile_sales(transactions, sales) -> dict`

Compares what the sales export says should have been banked against what was banked (FR-034). Per
calendar month over the union of both periods: expected banked = sales × (1 − expected processor
fee); banked = inflows excluding large one-offs (the same rule `detect_cashflow_flags` applies).

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `sales_total` | `float` | yes | Gross, as listed in the sales export |
| `expected_banked_total` | `float` | yes | `sales_total` net of the policy's expected processor fee |
| `banked_total` | `float` | yes | Inflows actually received, large one-offs excluded |
| `reconciliation_ratio` | `float` | yes | `banked_total ÷ expected_banked_total`. 1.0 is a perfect match; the tool never divides by zero because the sales export is validated non-empty and every amount positive |
| `months_compared` | `int` | yes | Months in the union of both periods. Exists to satisfy FR-009/FR-019 |
| `mismatched_month_count` | `int` | yes | Months whose own ratio is outside the per-month tolerance. Evidence, not a decision input |
| `max_month_gap_pct` | `float` | yes | Largest single-month absolute deviation from a ratio of 1.0, as a percentage |
| `reconciliation_tolerance_pct` | `float` | yes | The period-level tolerance that was applied, returned so a decline memo can cite it |
| `reconciled` | `bool` | no | `True` when the period-level ratio is within tolerance, in both directions |

### `score_risk(revenue_metrics, volatility, flags, reconciliation) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `risk_score` | `int` | yes | 0–100, computed from `policy.py` weights |
| `risk_score_max` | `int` | yes | The scale (100), for the same reason as `stability_score_max` |
| `decline_threshold` | `int` | yes | The score at which a merchant is refused. Returned whether or not they were |
| `min_months_history` | `int` | yes | The history floor. `decline_reason` names both of these in prose, so a memo explaining either outcome can cite them |
| `risk_tier` | `str \| None` | no | `"A"` \| `"B"` \| `"C"` \| `"D"`, or **None when declined** — a refused merchant has no tier, and inventing one would put a misleading grade in front of an analyst |
| `declined` | `bool` | no | |
| `decline_reason` | `str \| None` | no | Set only when `declined`. Two causes: history below the minimum, checked first, or a period that does not reconcile (FR-035). A reconciliation decline names `reconciliation_ratio` and `reconciliation_tolerance_pct`, both already recorded as facts by `reconcile_sales` |
| `drivers` | `list[str]` | no | Non-numeric reasons, for the memo to draw on |

`reconciliation` is `reconcile_sales`'s full return. It decides whether to decline and adds **no**
risk points, so the 40/25/25/10 weights are unchanged (FR-035).

### `compute_offer(revenue_metrics, risk) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `advance_amount` | `float` | yes | Policy-capped |
| `repayment_pct` | `float` | yes | Within the policy band for the tier |
| `expected_duration_months` | `int` | yes | |
| `total_repayable` | `float` | yes | |
| `advance_cap_applied` | `bool` | no | `True` when the cap, not the formula, set the amount |
| `declined` | `bool` | no | |
| `decline_reason` | `str \| None` | no | Set only when `declined`. On a decline every numeric field above is **zeroed, not omitted**, so the ledger has facts to register and a decline memo has figures to cite (FR-021) |
| `policy_version` | `str` | no | From `policy.py`, so an offer is traceable to the rules that made it |

## Fact ledger

### FactLedgerEntry

| Field | Type | Notes |
|---|---|---|
| `key` | `str` | Namespaced by tool: `revenue.avg_monthly_revenue` |
| `value` | `float \| int \| str \| bool` | As returned |
| `tool` | `str` | Tool name |
| `call_index` | `int` | Which call in the run produced it, so repeat calls stay distinguishable |

### Registration rules

1. **Only the dispatcher writes.** The model and the guardrail read; neither adds (FR-012).
2. **Written before the transcript.** A tool return is registered *before* its result is appended to
   the model's context. The model therefore cannot see a value that is not already recorded — which
   is what makes Article I structural rather than aspirational.
3. **Scalars register directly** as `<tool>.<field>`.
4. **Sequences register per element**, because a memo may quote any one of them. The monthly series
   becomes `revenue.monthly_revenue.2026-01`, `…2026-02`, and so on; `large_one_offs` registers each
   `amount` as `flags.large_one_offs.0.amount`. Without this, a memo citing a single month's revenue
   would fail grounding despite the figure being entirely legitimate.
5. **Non-numeric values are still registered**, for provenance display, but are not candidates for
   numeral resolution. Booleans are excluded explicitly: `isinstance(True, int)` holds in Python, so
   without that a memo containing "1" would resolve against every false-valued flag.
6. **A figure the prose will naturally use must exist as a fact.** Scale denominators and the
   thresholds a decision was judged against are returned by the tools for this reason (FR-009).
   Otherwise an honest memo writing "72 out of 100" or "below our 6-month minimum" would be
   rejected, because 100 and 6 would appear nowhere in the ledger.

### Resolution

A numeral resolves if it equals any registered numeric value rounded to a precision on the declared
ladder — nearest 1, 10, 100, 1000, or one decimal place (FR-018). Resolution is **existence-based**:
when two entries hold the same value, the numeral is grounded and provenance lists all matches. The
claim being verified is that the figure came from a tool, not which one.

## Ground truth

Written by `data/gen.py` at generation time to `truth.json`, beside the merchant's CSVs.

| Field | Type | Notes |
|---|---|---|
| `merchant_id` | `str` | |
| `profile` | `str` | `"healthy"` \| `"volatile"` \| `"declining"` \| `"thin_file"` |
| `seed` | `int` | Reproduces this merchant exactly (FR-028) |
| `true_monthly_revenue` | `list[tuple[str, float]]` | The series the generator actually produced |
| `true_avg_monthly_revenue` | `float` | |
| `true_total_revenue` | `float` | |
| `true_mom_growth_pct` | `float` | Log-linear fit, matching FR-004 |
| `true_revenue_cv` | `float` | |
| `true_overdraft_count` | `int` | |
| `true_bounced_payment_count` | `int` | |
| `true_large_one_off_count` | `int` | |
| `true_months_covered` | `int` | |
| `true_total_sales` | `float` | Sum of the sales export as generated |
| `true_reconciliation_ratio` | `float` | Realised banked ÷ expected banked, as built. A reconciling merchant sits at 1.0; a fixture sits deliberately away from it |
| `true_reconciles` | `bool` | Whether the generator intended the merchant's records to agree |

Importable **only** by `tests/` and `eval/` (Article III). Enforced by a test that walks the imports
of `tools/`, `agent/`, `guardrail/`, and `app/` and fails if any of them reach truth — a structural
guarantee rather than a convention, because a convenience import during debugging is exactly how this
kind of rule dies.

### Numeral extraction

Extraction errs toward catching too much, because the two failure directions are not
symmetric: a false rejection costs one regeneration, while a numeral the scanner *misses* is
displayed to the analyst having never been checked. Spelled cardinals therefore run to one
hundred including compounds ("ninety-two"), not merely to twenty.

Three exemptions, each narrow: ISO date strings are stripped before scanning; bare integers
in 1900–2100 with no separator, decimal, currency or percent are calendar years rather than
figures; and hyphens are excluded from the cardinal word boundary so "one-off" is not the
number one, while genuine compounds are matched as a unit first.

Not treated as numerals: "no", "none", "several". Including "no" would not catch the error it
appears to — resolution is existence-based, so "no overdrafts" on a merchant with four would
resolve against any other zero-valued fact.

## Verification result

| Field | Type | Notes |
|---|---|---|
| `passed` | `bool` | |
| `unresolved_numerals` | `list[str]` | Numerals found in prose with no ledger match |
| `policy_breaches` | `list[str]` | Bounds violated, if any |
| `incomplete` | `bool` | Memo cited none of the required figures (FR-021) |
| `attempt` | `int` | Which regeneration attempt this was |
| `used_fallback` | `bool` | Drives the mandatory visible notice (FR-024) |
