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
from ledgermind.tools.reconcile import reconcile_sales
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
    # Short prefix the fact ledger keys on: `revenue.avg_monthly_revenue`, not
    # `compute_revenue_metrics.avg_monthly_revenue`. Lives here so the namespace and the
    # tool it belongs to cannot drift apart (data-model.md).
    namespace: str
    # The name this tool's result is stored under for later tools to consume. Parameter
    # names refer to these bindings, so the two together describe the dependency graph --
    # and a test asserts every parameter is either a loaded input or some tool's binding,
    # which is how an unsatisfiable tool set fails at import rather than mid-run.
    binding: str
    description: str
    function: Callable[..., dict]
    parameters: dict[str, Any]
    returns: tuple[str, ...]


# The agent is never handed raw transaction rows or sales rows (FR-015). It names the input
# it wants -- `transactions`, `sales` -- and the dispatcher substitutes the loaded data, so
# the model can ask for an analysis without ever seeing the records it runs on. That is what
# makes Article I structural rather than a prompt instruction.
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
        binding="revenue_metrics",
        namespace="revenue",
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
        binding="volatility",
        namespace="volatility",
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
        returns=(
            "revenue_cv",
            "revenue_cv_pct",
            "revenue_stdev",
            "stability_score",
            "stability_score_max",
            "insufficient_history",
        ),
    ),
    Tool(
        name="detect_cashflow_flags",
        binding="flags",
        namespace="flags",
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
        name="reconcile_sales",
        binding="reconciliation",
        namespace="reconcile",
        description=(
            "Check the sales export against the bank statement: whether the money the sales "
            "records imply should have been banked actually was, month by month. Returns the "
            "median monthly ratio, the period totals, and whether the period reconciles. "
            "Requires both inputs."
        ),
        function=reconcile_sales,
        parameters={
            "type": "object",
            "properties": {
                "transactions": {"type": "string", "description": "Always 'transactions'."},
                "sales": {"type": "string", "description": "Always 'sales'."},
            },
            "required": ["transactions", "sales"],
        },
        returns=(
            "sales_total",
            "expected_banked_total",
            "banked_total",
            "reconciliation_ratio",
            "reconciliation_ratio_pct",
            "period_ratio",
            "months_compared",
            "mismatched_month_count",
            "max_month_gap_pct",
            "reconciliation_tolerance_pct",
            "reconciled",
        ),
    ),
    Tool(
        name="score_risk",
        binding="risk",
        namespace="risk",
        description=(
            "Assign a risk score and tier from the revenue, volatility, flag and "
            "reconciliation results, or decline. Requires all four earlier analyses."
        ),
        function=score_risk,
        parameters={
            "type": "object",
            "properties": {
                "revenue_metrics": {"type": "string", "description": "Always 'revenue_metrics'."},
                "volatility": {"type": "string", "description": "Always 'volatility'."},
                "flags": {"type": "string", "description": "Always 'flags'."},
                "reconciliation": {"type": "string", "description": "Always 'reconciliation'."},
            },
            "required": ["revenue_metrics", "volatility", "flags", "reconciliation"],
        },
        returns=(
            "risk_score",
            "risk_score_max",
            "decline_threshold",
            "min_months_history",
            "risk_tier",
            "declined",
            "decline_reason",
            "drivers",
        ),
    ),
    Tool(
        name="compute_offer",
        binding="offer",
        namespace="offer",
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

# Inputs the dispatcher loads from the merchant's files. Everything else a tool asks for
# must be some earlier tool's binding.
LOADED_INPUTS = ("transactions", "sales")

# `compute_volatility` takes the monthly series rather than the whole revenue result, so
# that one field is bound separately. Declared here rather than special-cased in the loop.
DERIVED_BINDINGS = {"monthly_revenue": ("revenue_metrics", "monthly_revenue")}


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
