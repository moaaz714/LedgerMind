"""The tool catalogue: name to callable and schema.

One source of truth, read by both the agent's prompt and the dispatcher. If the prompt
advertised tools from one list and the dispatcher resolved them from another, the two would
drift and the model would be offered a tool that cannot be called -- or worse, a tool whose
arguments mean something slightly different than advertised.

This module is the wiring point, so Article IV gates it: it exists only because every tool
below already has passing tests against known truth.

Deliberately not a framework. The dispatcher that consumes this is where the fact ledger is
populated, and that seam is the whole grounding guarantee -- a framework that abstracts
tool dispatch would hide exactly the line of code the project's central claim rests on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ledgermind.tools.flags import detect_cashflow_flags
from ledgermind.tools.offer import compute_offer
from ledgermind.tools.revenue import compute_revenue_metrics
from ledgermind.tools.scoring import score_risk
from ledgermind.tools.volatility import compute_volatility


@dataclass(frozen=True)
class Tool:
    """One callable analysis, with the schema the model is shown.

    `returns` lists the field names the tool produces. They are the fact ledger's keys, so
    this list doubles as the registration contract -- see data-model.md.
    """

    name: str
    description: str
    function: Callable[..., dict]
    parameters: dict[str, Any]
    returns: tuple[str, ...]


# The agent is never handed raw transaction rows (FR-015). It passes `transactions` and
# `sales` by name and the dispatcher substitutes the loaded data, so the model can ask for
# an analysis without ever seeing the records it runs on -- which is what makes Article I
# structural rather than a prompt instruction.
_TRANSACTIONS_PARAM = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "string",
            "description": "Always the literal string 'transactions'; the loaded statement is substituted.",
        }
    },
    "required": ["transactions"],
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="compute_revenue_metrics",
        description=(
            "Monthly banked revenue and the measures derived from it: average, total, "
            "growth rate, trend direction and months covered. Run this first; the other "
            "analyses build on it."
        ),
        function=compute_revenue_metrics,
        parameters=_TRANSACTIONS_PARAM,
        returns=(
            "monthly_revenue",
            "avg_monthly_revenue",
            "total_revenue",
            "mom_growth_pct",
            "months_covered",
            "period_start",
            "period_end",
            "trend",
        ),
    ),
    Tool(
        name="compute_volatility",
        description=(
            "Revenue stability, measured after removing the trend. Returns a coefficient "
            "of variation, a stability score, and whether the history is too short to "
            "assess. Requires the monthly series from compute_revenue_metrics."
        ),
        function=compute_volatility,
        parameters={
            "type": "object",
            "properties": {
                "monthly_revenue": {
                    "type": "string",
                    "description": "Always 'monthly_revenue'; the series from compute_revenue_metrics is substituted.",
                }
            },
            "required": ["monthly_revenue"],
        },
        returns=("revenue_cv", "revenue_stdev", "stability_score", "insufficient_history"),
    ),
    Tool(
        name="detect_cashflow_flags",
        description=(
            "Cashflow risk indicators: overdraft episodes, returned payments, whether the "
            "balance has eroded, large one-off inflows, and the lowest balance seen."
        ),
        function=detect_cashflow_flags,
        parameters=_TRANSACTIONS_PARAM,
        returns=(
            "overdraft_count",
            "bounced_payment_count",
            "large_one_off_count",
            "flag_count",
            "min_balance",
            "large_one_offs",
            "declining_balance",
        ),
    ),
    Tool(
        name="score_risk",
        description=(
            "Assign a risk score and tier from the revenue, volatility and flag results, "
            "or decline. Requires all three earlier analyses."
        ),
        function=score_risk,
        parameters={
            "type": "object",
            "properties": {
                "revenue_metrics": {"type": "string", "description": "Always 'revenue_metrics'."},
                "volatility": {"type": "string", "description": "Always 'volatility'."},
                "flags": {"type": "string", "description": "Always 'flags'."},
            },
            "required": ["revenue_metrics", "volatility", "flags"],
        },
        returns=("risk_score", "risk_tier", "declined", "decline_reason", "drivers"),
    ),
    Tool(
        name="compute_offer",
        description=(
            "Compute the advance amount, repayment percentage, expected duration and total "
            "repayable, all within policy bounds. Requires the revenue metrics and the risk "
            "assessment."
        ),
        function=compute_offer,
        parameters={
            "type": "object",
            "properties": {
                "revenue_metrics": {"type": "string", "description": "Always 'revenue_metrics'."},
                "risk": {"type": "string", "description": "Always 'risk'."},
            },
            "required": ["revenue_metrics", "risk"],
        },
        returns=(
            "advance_amount",
            "repayment_pct",
            "expected_duration_months",
            "total_repayable",
            "advance_cap_applied",
            "declined",
            "decline_reason",
            "policy_version",
        ),
    ),
)

BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


def get(name: str) -> Tool:
    """Resolve a tool by name, failing loudly on an unknown one.

    A model hallucinating a tool name is expected traffic, not an exceptional condition --
    the dispatcher catches this and tells the model what it actually has.
    """
    try:
        return BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"unknown tool {name!r}; available tools: {', '.join(sorted(BY_NAME))}"
        ) from None


def schemas() -> list[dict]:
    """The tool list as the model is shown it."""
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for tool in TOOLS
    ]
