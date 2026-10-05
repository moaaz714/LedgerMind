"""The agent loop, driven by a scripted provider (T022, T026, T028).

No model runs here. A fake provider returns canned responses, so every assertion is
deterministic -- which is what makes it possible to test the loop's *mechanics* separately
from the open question of whether a 7B model cooperates. The two failure modes are
different and want different kinds of evidence.
"""

import pytest

from ledgermind import policy
from ledgermind.agent import loop
from ledgermind.agent.ledger import FactLedger
from ledgermind.data import load
from ledgermind.llm.base import Completion, ToolCall
from ledgermind.tools import registry

THE_SIX = [
    "compute_revenue_metrics",
    "compute_volatility",
    "detect_cashflow_flags",
    "reconcile_sales",
    "score_risk",
    "compute_offer",
]


class FakeProvider:
    """Replays a script. Records every transcript it was given."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.name = "fake/scripted"

    def chat(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.script:
            raise AssertionError("the loop asked for more turns than the script provides")
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def _tools_then(memo, names=THE_SIX):
    """One tool call per turn, then the memo."""
    return [Completion(tool_calls=(ToolCall(name=name),)) for name in names] + [
        Completion(text=memo)
    ]


@pytest.fixture
def merchant(merchant_root):
    return load.load_merchant(merchant_root / "m02_healthy_mid")


@pytest.fixture
def honest_memo():
    """Written to pass: every figure is one the tools produce for m02."""
    return (
        "Revenue averaged 57,100 a month across eighteen months with a stability score of "
        "95 out of 100 and no cashflow flags. The sales records reconcile. We can advance "
        "77,000, repaid at 13% of monthly revenue over about 12 months, 86,240 in total."
    )


# --- the happy path -------------------------------------------------------------------


def test_a_clean_run_produces_a_verified_decision(merchant, honest_memo):
    provider = FakeProvider(_tools_then(honest_memo))
    decision = loop.run(merchant, provider)

    assert decision.verification.passed, decision.verification.reason()
    assert decision.used_fallback is False
    assert decision.memo == honest_memo
    assert decision.merchant_id == "m02_healthy_mid"
    assert decision.provider_name == "fake/scripted"
    assert list(decision.tool_calls) == THE_SIX


def test_the_offer_is_the_tools_return_value_untouched(merchant, honest_memo):
    """Article I. The model contributes the memo and nothing else."""
    from ledgermind.tools.offer import compute_offer
    from ledgermind.tools.revenue import compute_revenue_metrics
    from ledgermind.tools.volatility import compute_volatility
    from ledgermind.tools.flags import detect_cashflow_flags
    from ledgermind.tools.reconcile import reconcile_sales
    from ledgermind.tools.scoring import score_risk

    tx, sl = merchant["transactions"], merchant["sales"]
    m = compute_revenue_metrics(tx)
    expected = compute_offer(
        m, score_risk(m, compute_volatility(m["monthly_revenue"]),
                      detect_cashflow_flags(tx), reconcile_sales(tx, sl))
    )

    decision = loop.run(merchant, FakeProvider(_tools_then(honest_memo)))
    assert decision.offer == expected


def test_provenance_is_recorded_for_what_the_memo_cited(merchant, honest_memo):
    decision = loop.run(merchant, FakeProvider(_tools_then(honest_memo)))
    cited = {key for p in decision.provenance for key in p.fact_keys}
    assert "offer.advance_amount" in cited
    assert "revenue.avg_monthly_revenue" in cited


# --- the ordering the whole design rests on -------------------------------------------


def test_the_ledger_is_written_before_the_model_sees_the_result(merchant, honest_memo):
    """The single line that makes Article I structural.

    If a result reached the transcript first, the model could quote a figure that was not
    yet a recorded fact, and grounding would be checking against an incomplete ledger. The
    observer makes the ordering visible from outside, where it is otherwise invisible.
    """
    events = []
    loop.run(merchant, FakeProvider(_tools_then(honest_memo)),
             observer=lambda event, payload: events.append((event, payload.get("tool"))))

    for tool in THE_SIX:
        order = [event for event, name in events if name == tool]
        assert order.index("registered") < order.index("appended"), tool


def test_every_tool_result_is_in_the_ledger_before_the_next_turn(merchant, honest_memo):
    """Checked from the provider's side: by the time the model is asked again, the facts
    behind the result it is about to read are already recorded."""
    seen = []
    ledger = FactLedger()

    def record(messages):
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        seen.append((len(tool_messages), len(ledger)))
        return Completion(tool_calls=(ToolCall(name=THE_SIX[len(tool_messages)]),)) \
            if len(tool_messages) < len(THE_SIX) else Completion(text=honest_memo)

    loop.run(merchant, FakeProvider([record] * (len(THE_SIX) + 1)), ledger=ledger)
    for tool_count, fact_count in seen[1:]:
        assert fact_count > 0, "facts must exist before the model reads a result"


# --- model mistakes are answered, not crashed on --------------------------------------


def test_an_unknown_tool_is_answered_with_the_real_list(merchant, honest_memo):
    provider = FakeProvider(
        [Completion(tool_calls=(ToolCall(name="compute_vibes"),))] + _tools_then(honest_memo)
    )
    decision = loop.run(merchant, provider)

    assert decision.verification.passed
    assert "compute_vibes" not in decision.tool_calls
    nudge = provider.calls[1]["messages"][-1]["content"]
    assert "unknown tool" in nudge
    assert "compute_revenue_metrics" in nudge, "the model is told what it actually has"


def test_a_tool_called_out_of_order_is_told_what_is_missing(merchant, honest_memo):
    """score_risk before its inputs exist must not crash the run."""
    provider = FakeProvider(
        [Completion(tool_calls=(ToolCall(name="score_risk"),))] + _tools_then(honest_memo)
    )
    decision = loop.run(merchant, provider)

    assert decision.verification.passed
    nudge = provider.calls[1]["messages"][-1]["content"]
    assert "revenue_metrics" in nudge
    assert "Run the analysis that produces it first" in nudge


def test_a_model_that_never_stops_is_cut_off(merchant):
    forever = [Completion(tool_calls=(ToolCall(name="compute_revenue_metrics"),))] * 50
    with pytest.raises(loop.LoopError, match="still requesting tools"):
        loop.run(merchant, FakeProvider(forever))


def test_stopping_before_an_offer_is_an_error_not_a_decision(merchant):
    """A memo with no offer behind it is not a decision, and must not be presented as one."""
    provider = FakeProvider(
        [Completion(tool_calls=(ToolCall(name="compute_revenue_metrics"),)),
         Completion(text="Looks fine to me.")]
    )
    with pytest.raises(loop.LoopError, match="stopped before computing an offer"):
        loop.run(merchant, provider)


# --- bounded regeneration (T028, FR-023) ----------------------------------------------


def test_a_rejected_memo_is_retried_with_the_specific_reason(merchant, honest_memo):
    bad = honest_memo.replace("77,000", "999,999")
    provider = FakeProvider(_tools_then(bad) + [Completion(text=honest_memo)])
    decision = loop.run(merchant, provider)

    assert decision.verification.passed
    assert decision.verification.attempt == 2
    assert decision.used_fallback is False

    nudge = provider.calls[-1]["messages"][-1]["content"]
    assert "999,999" in nudge, "the model is told which figure was wrong"
    assert "rejected" in nudge


def test_retries_stop_at_the_declared_allowance(merchant, honest_memo):
    bad = honest_memo.replace("77,000", "999,999")
    provider = FakeProvider(_tools_then(bad) + [Completion(text=bad)] * 10)
    decision = loop.run(merchant, provider)

    assert decision.used_fallback is True
    model_turns = sum(1 for call in provider.calls if call["tools"] is None)
    assert model_turns == policy.MAX_REGENERATION_ATTEMPTS - 1


def test_the_fallback_is_used_and_labelled_when_the_model_keeps_failing(merchant, honest_memo):
    bad = honest_memo.replace("77,000", "999,999")
    decision = loop.run(merchant, FakeProvider(_tools_then(bad) + [Completion(text=bad)] * 10))

    assert decision.used_fallback is True
    assert decision.verification.passed, "the fallback must itself verify"
    assert "NOTICE" in decision.memo
    assert "verification failed" in decision.memo
    assert "999,999" not in decision.memo


def test_an_incomplete_memo_is_rejected_and_retried(merchant, honest_memo):
    """Grounding alone passes a memo with no figures; completeness is what catches it."""
    provider = FakeProvider(
        _tools_then("This merchant looks solid. We recommend proceeding.")
        + [Completion(text=honest_memo)]
    )
    decision = loop.run(merchant, provider)

    assert decision.verification.passed
    assert decision.verification.attempt == 2
    nudge = provider.calls[-1]["messages"][-1]["content"]
    assert "must also cite" in nudge


# --- what the model is and is not given -----------------------------------------------


def test_the_model_never_receives_a_transaction_row(merchant, honest_memo):
    """FR-015, asserted against every transcript the provider was handed.

    Not a prompt instruction: the loop binds inputs by name and substitutes them inside the
    dispatcher, so there is no path by which a record reaches the context.
    """
    provider = FakeProvider(_tools_then(honest_memo))
    loop.run(merchant, provider)

    a_description = merchant["transactions"][0]["description"]
    a_balance = str(merchant["transactions"][0]["balance"])
    for call in provider.calls:
        blob = "\n".join(message.get("content") or "" for message in call["messages"])
        assert a_balance not in blob, "a raw balance reached the model"
        assert blob.count(a_description) == 0, "a raw description reached the model"


def test_the_model_is_offered_every_tool(merchant, honest_memo):
    provider = FakeProvider(_tools_then(honest_memo))
    loop.run(merchant, provider)
    offered = {schema["name"] for schema in provider.calls[0]["tools"]}
    assert offered == set(registry.BY_NAME)


def test_retry_turns_offer_no_tools(merchant, honest_memo):
    """A rejected memo is a writing problem, not an analysis problem."""
    bad = honest_memo.replace("77,000", "999,999")
    provider = FakeProvider(_tools_then(bad) + [Completion(text=honest_memo)])
    loop.run(merchant, provider)
    assert provider.calls[-1]["tools"] is None


# --- a declined merchant --------------------------------------------------------------


def test_a_declined_merchant_reaches_a_verified_decline(merchant_root):
    declined = load.load_merchant(merchant_root / "m21_recon_overstated")
    # Note the absence of "the two views": a spelled cardinal used structurally is still
    # read as a numeric claim, and nothing in the ledger equals 2. A false rejection rather
    # than a false pass, which is the safe direction -- but it is why the prompt warns
    # against counting words the results did not supply.
    memo = (
        "The sales records do not agree with the bank statement: banked revenue is 74.1% "
        "of what the sales export implies, outside the tolerance of 8.0%. Across 15 months "
        "the records differ by more than we can explain, so we are declining."
    )
    decision = loop.run(declined, FakeProvider(_tools_then(memo)))

    assert decision.declined is True
    assert decision.verification.passed, decision.verification.reason()
    assert decision.offer["advance_amount"] == 0.0


# --- rendering must never reach the decision path -------------------------------------


def _boundary_merchant(growth_pct):
    """A merchant growing at exactly `growth_pct` a month."""
    months = [f"2026-{m:02d}" for m in range(1, 13)] + [f"2027-{m:02d}" for m in range(1, 4)]
    rate = 1 + growth_pct / 100
    transactions, balance = [], 100_000.0
    for i, month in enumerate(months):
        amount = round(50_000 * rate**i, 2)
        balance = round(balance + amount, 2)
        transactions.append(
            {"date": f"{month}-10", "amount": amount, "description": "card settlement", "balance": balance}
        )
    sales = [
        {"date": f"{m}-05", "amount": round(50_000 * rate**i / 0.975, 2)}
        for i, m in enumerate(months)
    ]
    return {"merchant_id": "boundary_probe", "transactions": transactions, "sales": sales}


def _memo_for(merchant):
    """A memo built from the merchant's own figures, so it verifies.

    Composed from the tool results rather than hand-written, because these tests are about
    where precision flows -- a memo that failed verification would send the loop into
    retries and obscure what is being measured.
    """
    from ledgermind.tools.flags import detect_cashflow_flags
    from ledgermind.tools.offer import compute_offer
    from ledgermind.tools.reconcile import reconcile_sales
    from ledgermind.tools.revenue import compute_revenue_metrics
    from ledgermind.tools.scoring import score_risk
    from ledgermind.tools.volatility import compute_volatility

    tx, sl = merchant["transactions"], merchant["sales"]
    metrics = compute_revenue_metrics(tx)
    risk = score_risk(metrics, compute_volatility(metrics["monthly_revenue"]),
                      detect_cashflow_flags(tx), reconcile_sales(tx, sl))
    offer = compute_offer(metrics, risk)
    return (
        f"Revenue averaged {metrics['avg_monthly_revenue']:,.2f} a month over "
        f"{metrics['months_covered']} months. We can advance "
        f"{offer['advance_amount']:,.2f} at {offer['repayment_pct']:.1f}% over "
        f"{offer['expected_duration_months']} months."
    )


def test_scoring_uses_the_recorded_value_not_the_rendered_one():
    """The invariant that keeps a display change out of the decision.

    Tool results are rendered to one decimal place before the model sees them, so a figure
    it copies is always a permitted rendering. That rounding must never reach the analyses:
    a merchant growing at 2.04% a month is above the trend boundary of 2.0 and earns zero
    trend points, while the rendered 2.0 is at the boundary and would earn ten. A refactor
    that passed the rendered values into the tools would silently reprice this merchant.
    """
    merchant = _boundary_merchant(2.04)
    decision = loop.run(merchant, FakeProvider(_tools_then(_memo_for(merchant))))

    real = decision.revenue_metrics["mom_growth_pct"]
    assert real == pytest.approx(2.04, abs=1e-6), "fixture must sit just above the boundary"
    assert real > policy.TREND_RISING_PCT

    assert decision.revenue_metrics["trend"] == "rising"
    assert policy.trend_risk_points(real) == 0
    assert policy.trend_risk_points(round(real, policy.GROUNDING_DECIMAL_PLACES)) == 10, (
        "the rendered value lands on the other side of the band, which is what makes this "
        "test meaningful"
    )

    volatility_points = policy.volatility_band(decision.volatility["revenue_cv"])[1]
    history_points = policy.history_risk_points(decision.revenue_metrics["months_covered"])
    assert decision.risk["risk_score"] == volatility_points + history_points, (
        "the score must contain no trend points, which is only true of the real value"
    )


def test_the_full_precision_value_reaches_the_analyst():
    """Rounding happens on exactly one path: what the model is shown.

    The Decision carries every tool result whole, so the interface renders full precision
    even where the memo says something rounded.
    """
    merchant = _boundary_merchant(2.04)
    decision = loop.run(merchant, FakeProvider(_tools_then(_memo_for(merchant))))
    assert decision.revenue_metrics["mom_growth_pct"] == pytest.approx(2.04, abs=1e-6)
    assert decision.revenue_metrics["mom_growth_pct"] != round(2.04, 1)


# --- FR-013: the record stays inspectable after the run -------------------------------


def test_the_decision_carries_every_recorded_fact(merchant, honest_memo):
    """FR-013. Provenance gives a key; this is what lets a consumer reach the value.

    Without it an interface could show "you wrote 2.0%" and nothing else -- not the 1.9886
    behind it, which is the entire point of displaying provenance.
    """
    decision = loop.run(merchant, FakeProvider(_tools_then(honest_memo)))

    assert decision.facts, "the decision must carry the recorded facts"
    for entry in decision.provenance:
        for key in entry.fact_keys:
            assert key in decision.facts, key
            assert decision.recorded_value(key) is not None


def test_a_rounded_quotation_resolves_to_its_full_precision_value(merchant, honest_memo):
    decision = loop.run(merchant, FakeProvider(_tools_then(honest_memo)))
    recorded = decision.recorded_value("revenue.mom_growth_pct")
    assert recorded == decision.revenue_metrics["mom_growth_pct"]
    assert recorded != round(recorded, policy.GROUNDING_DECIMAL_PLACES), (
        "this merchant's growth rate must carry more precision than the memo can show, "
        "or the test proves nothing"
    )


def test_the_fallback_decision_also_carries_the_facts(merchant, honest_memo):
    bad = honest_memo.replace("77,000", "999,999")
    decision = loop.run(merchant, FakeProvider(_tools_then(bad) + [Completion(text=bad)] * 10))
    assert decision.used_fallback is True
    assert decision.facts
