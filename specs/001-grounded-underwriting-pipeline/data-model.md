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
| `mom_growth_pct` | `float` | yes | Mean month-over-month change, percent. Also defines the trend line that `compute_volatility` subtracts |
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
| `insufficient_history` | `bool` | no | `True` when months < the policy minimum (thin-file edge case) |

### `detect_cashflow_flags(transactions) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `overdraft_count` | `int` | yes | |
| `bounced_payment_count` | `int` | yes | |
| `large_one_off_count` | `int` | yes | |
| `flag_count` | `int` | yes | Total across all kinds |
| `min_balance` | `float` | yes | Lowest balance observed |
| `large_one_offs` | `list[dict]` | each `amount` | `{date, amount, description}` |
| `declining_balance` | `bool` | no | |

An empty result is a count of zero, never an absent fact (spec edge case).

### `score_risk(revenue_metrics, volatility, flags) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `risk_score` | `int` | yes | 0–100, computed from `policy.py` weights |
| `risk_tier` | `str` | no | `"A"` \| `"B"` \| `"C"` \| `"D"` |
| `declined` | `bool` | no | |
| `decline_reason` | `str \| None` | no | Set only when `declined` |
| `drivers` | `list[str]` | no | Non-numeric reasons, for the memo to draw on |

### `compute_offer(revenue_metrics, risk) -> dict`

| Field | Type | Groundable | Notes |
|---|---|---|---|
| `advance_amount` | `float` | yes | Policy-capped |
| `repayment_pct` | `float` | yes | Within the policy band for the tier |
| `expected_duration_months` | `int` | yes | |
| `total_repayable` | `float` | yes | |
| `advance_cap_applied` | `bool` | no | `True` when the cap, not the formula, set the amount |
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
   numeral resolution.

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
| `true_revenue_cv` | `float` | |
| `true_overdraft_count` | `int` | |
| `true_bounced_payment_count` | `int` | |
| `true_large_one_off_count` | `int` | |
| `true_months_covered` | `int` | |

Importable **only** by `tests/` and `eval/` (Article III). Enforced by a test that walks the imports
of `tools/`, `agent/`, `guardrail/`, and `app/` and fails if any of them reach truth — a structural
guarantee rather than a convention, because a convenience import during debugging is exactly how this
kind of rule dies.

## Verification result

| Field | Type | Notes |
|---|---|---|
| `passed` | `bool` | |
| `unresolved_numerals` | `list[str]` | Numerals found in prose with no ledger match |
| `policy_breaches` | `list[str]` | Bounds violated, if any |
| `incomplete` | `bool` | Memo cited none of the required figures (FR-021) |
| `attempt` | `int` | Which regeneration attempt this was |
| `used_fallback` | `bool` | Drives the mandatory visible notice (FR-024) |
