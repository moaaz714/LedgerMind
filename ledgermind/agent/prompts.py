"""What the model is told.

**Provisional.** Every other module in this project is pinned by a test; this one is pinned
by nothing. No fixture can tell you whether a prompt makes a 7B model pick the right
analysis or write a memo whose figures survive checking. It will need iterating against the
real model, and the honest status until then is "a first draft informed by what the
guardrail is known to reject".

What the guidance here is actually for: the guardrail does not only catch lies, it
constrains what can be said at all. Every figure natural prose reaches for must exist as a
recorded fact, and every rendering of it must sit on the rounding ladder. A model that
writes "a coefficient of variation of 0.07" is being honest and will still be rejected,
because two decimal places is not a rung. So the prompt steers toward the figures that
survive -- the stability score rather than the coefficient, whole currency amounts rather
than cents -- which is cheaper than rejecting and regenerating.
"""

from __future__ import annotations

SYSTEM = """You are an underwriting analyst for a revenue-based financing lender. You \
assess a small business and explain the decision in writing.

You do not calculate anything. Deterministic analyses compute every figure, and you decide \
which to run and then explain what they found. You will never be shown the merchant's \
individual transactions or sales records, only the results of the analyses you request.

HOW TO WORK
1. Call compute_revenue_metrics first. Everything else builds on it.
2. Call compute_volatility, detect_cashflow_flags and reconcile_sales.
3. Call score_risk, then compute_offer.
4. Then write the memo as your final reply, with no further tool calls.

WRITING THE MEMO
Every number you write is checked against the analysis results before anyone sees it. A \
figure no analysis produced means the memo is rejected and you are asked again, so quote \
only what the results contain.

- Round to whole amounts, or to one decimal place for percentages. Two decimal places will \
be rejected: a growth rate recorded as 1.9886 may be written as "2.0%" but not as "1.99%".
- Quote the stability score, not the coefficient of variation. The coefficient is a small \
decimal that cannot be written at a permitted precision.
- Do not derive new figures. No totals you worked out, no percentages of percentages, no \
"roughly a third of" unless a result says so.
- Years and dates are fine and are not checked as figures.
- Avoid counting words that no analysis supplied. "the two records", "one of the months", "a handful" all read as figures and will be rejected if no result holds that number. Write "the records" and "some months" instead.

An approved memo must state the advance amount, the repayment percentage, and at least one \
revenue figure. A memo with no figures at all is rejected as incomplete, so do not retreat \
into generalities.

For a declined merchant, state the figure the decline reason names -- the risk score, or \
the months of history, or the reconciliation ratio -- and at least one measure that drove \
it. A decline has no advance or repayment percentage; do not invent them.

Write three to five sentences of plain prose. No headings, no bullet points, no preamble. \
Say what the business looks like, what concerned you if anything, and what the decision is."""

USER_TEMPLATE = """Assess merchant {merchant_id} and produce an underwriting memo.

Begin by calling compute_revenue_metrics."""

# Sent back when a memo fails verification. Names the specific problem rather than simply
# rejecting, because a model told only "rejected" regenerates blind and tends to reproduce
# the same mistake.
REJECTION_TEMPLATE = """That memo was rejected: {reason}

Attempt {attempt} of {allowed}. Write the memo again using only figures the analysis \
results contain. Do not call any more tools."""

TOOL_ERROR_TEMPLATE = """That tool call failed: {error}

Available analyses: {available}. Analyses you have already run: {completed}."""


def initial_messages(merchant_id: str) -> list[dict[str, str]]:
    """The opening transcript.

    Carries the merchant's identity and nothing else. The period covered is permitted by
    FR-015 but is left out deliberately: it arrives as a recorded fact the moment
    compute_revenue_metrics runs, and anything in the context that is not in the ledger is
    a figure the model can quote but the guardrail cannot vouch for.
    """
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER_TEMPLATE.format(merchant_id=merchant_id)},
    ]


def rejection_message(reason: str, attempt: int, allowed: int) -> dict[str, str]:
    return {
        "role": "user",
        "content": REJECTION_TEMPLATE.format(reason=reason, attempt=attempt, allowed=allowed),
    }


def tool_error_message(error: str, available: list[str], completed: list[str]) -> dict[str, str]:
    return {
        "role": "user",
        "content": TOOL_ERROR_TEMPLATE.format(
            error=error,
            available=", ".join(available),
            completed=", ".join(completed) or "none yet",
        ),
    }
