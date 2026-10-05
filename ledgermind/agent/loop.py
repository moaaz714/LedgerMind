"""The hand-rolled agent loop.

Deliberately no framework. The reason is one line of this file: in `_dispatch`, the tool's
result is registered to the fact ledger **before** it is appended to the model's transcript.
That ordering is the whole grounding guarantee -- the model cannot see a value that is not
already recorded, so anything it quotes is in the ledger by construction, and Article I
holds structurally rather than by instruction. A framework that owns tool dispatch owns that
line, and hides the seam the project's central claim rests on.

The loop is otherwise unremarkable, which is the point: drive the provider, dispatch what it
asks for, append results, repeat until it writes prose. Then verify, and retry a bounded
number of times with the specific reason, falling back to a templated memo if the model
cannot produce a verifiable one.

Three failures a model will actually produce are handled rather than crashed on: calling a
tool that does not exist, calling one whose inputs are not ready, and never stopping. Each
is answered with a message the model can act on, because an exception would turn an ordinary
model mistake into a failed run.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from ledgermind import policy
from ledgermind.agent import prompts
from ledgermind.agent.ledger import FactLedger
from ledgermind.agent.schema import Decision, VerificationResult
from ledgermind.guardrail.check import check_output, fallback_memo
from ledgermind.llm.base import Completion, Provider
from ledgermind.tools import registry

Observer = Callable[[str, dict], None]


class LoopError(RuntimeError):
    """The run could not reach a decision at all, as distinct from reaching a bad one."""


def _emit(observer: Observer | None, event: str, **payload: Any) -> None:
    """Report progress.

    Exists so the interface can show analyses as they happen (FR-029, T036) and so tests can
    assert on ordering -- specifically that registration precedes the transcript append,
    which is otherwise invisible from outside.
    """
    if observer is not None:
        observer(event, payload)


def _resolve_arguments(tool: registry.Tool, bindings: dict[str, Any]) -> list[Any]:
    """Map the tool's declared parameters onto what is available.

    The model names the input it wants; it never supplies a value. Raises KeyError naming
    the missing binding, which the caller turns into a message rather than a crash.
    """
    arguments = []
    for parameter in tool.parameters["required"]:
        if parameter in bindings:
            arguments.append(bindings[parameter])
            continue
        source = registry.DERIVED_BINDINGS.get(parameter)
        if source and source[0] in bindings:
            arguments.append(bindings[source[0]][source[1]])
            continue
        raise KeyError(parameter)
    return arguments


def _dispatch(
    name: str,
    bindings: dict[str, Any],
    ledger: FactLedger,
    messages: list[dict[str, Any]],
    observer: Observer | None,
) -> str | None:
    """Run one tool. Returns an error string for the model, or None on success."""
    try:
        tool = registry.get(name)
    except KeyError as exc:
        return str(exc.args[0])

    try:
        arguments = _resolve_arguments(tool, bindings)
    except KeyError as missing:
        return (
            f"{name} needs {missing.args[0]!r}, which no analysis has produced yet. "
            f"Run the analysis that produces it first."
        )

    try:
        result = tool.function(*arguments)
    except Exception as exc:  # a tool refusing bad input is the model's problem to fix
        return f"{name} could not run: {exc}"

    # ---- THE ORDERING THAT MATTERS -------------------------------------------------
    # Register first. Append second. The model cannot quote a figure that is not already
    # a recorded fact, because it has not been shown one.
    entries = ledger.register(tool.namespace, tool.name, result)
    _emit(observer, "registered", tool=name, keys=[entry.key for entry in entries])

    # The tool name goes inside the content as well as in the field, so the model can tell
    # which result it is reading regardless of what the chat template does with extra keys.
    messages.append({"role": "tool", "name": name, "content": f"{name} returned:\n{_render(result)}"})
    _emit(observer, "appended", tool=name, result=result)
    # --------------------------------------------------------------------------------

    bindings[tool.binding] = result
    return None


def _display(value: Any) -> Any:
    """A value at a precision the model may legally quote.

    The fix for the single most common real failure. Shown `mom_growth_pct = 1.9886` the
    model copies it as "1.99%", which the grounding ladder rejects because two decimal
    places is not a rung -- so it was being handed a number it could not legally write, and
    then rejected for writing it. Entirely the model doing the reasonable thing.

    Rounding to one decimal place means whatever it copies is already a permitted rendering
    of the recorded value. The ledger still holds full precision; this changes only what the
    model is shown. Prompt instructions were tried first and did not work: the real run
    produced "1.99%" twice in a row immediately after being told not to.
    """
    if isinstance(value, bool) or not isinstance(value, float):
        return value
    return round(value, policy.GROUNDING_DECIMAL_PLACES)


def _render(result: dict) -> str:
    """A tool result as the model sees it.

    Plain key-value lines rather than JSON: smaller in context, and a 7B model follows it
    more reliably than nested braces.
    """
    lines = []
    for key, value in result.items():
        if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
            lines.extend(f"  {key}.{item[0]} = {_display(item[1])}" for item in value)
        elif isinstance(value, list):
            lines.append(f"  {key} = {[_display(item) for item in value]}")
        else:
            lines.append(f"  {key} = {_display(value)}")
    return "\n".join(lines)


def run(
    merchant: dict,
    provider: Provider,
    observer: Observer | None = None,
    ledger: FactLedger | None = None,
) -> Decision:
    """Drive one merchant to a verified decision.

    `merchant` is `load_merchant`'s output: an id plus the two loaded record sets. The
    model receives neither record set -- only results (FR-015).
    """
    merchant_id = merchant["merchant_id"]
    ledger = ledger if ledger is not None else FactLedger()
    messages = prompts.initial_messages(merchant_id)
    bindings: dict[str, Any] = {
        "transactions": merchant["transactions"],
        "sales": merchant["sales"],
    }
    called: list[str] = []
    schemas = registry.schemas()

    completion = Completion()
    for _ in range(policy.MAX_TOOL_CALLS):
        completion = provider.chat(messages, tools=schemas)
        if not completion.wants_tools:
            break

        # Record that the model asked, before recording what it got. Without this the
        # transcript shows a tool result with no request behind it, and the model has no
        # evidence its call ran -- so it calls again. On the first real run that produced
        # four consecutive identical calls and about 78 seconds of wasted turns.
        messages.append(
            {
                "role": "assistant",
                "content": completion.text,
                "tool_calls": [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in completion.tool_calls
                ],
            }
        )

        for call in completion.tool_calls:
            _emit(observer, "tool_call", tool=call.name)
            error = _dispatch(call.name, bindings, ledger, messages, observer)
            if error:
                _emit(observer, "tool_error", tool=call.name, error=error)
                messages.append(
                    prompts.tool_error_message(error, sorted(registry.BY_NAME), called)
                )
            else:
                called.append(call.name)
    else:
        # The allowance ran out with the model still asking for analyses. It has not
        # understood the task, and there is no memo to verify.
        raise LoopError(
            f"{merchant_id}: still requesting tools after {policy.MAX_TOOL_CALLS} calls "
            f"(ran: {', '.join(called) or 'none'})"
        )

    if "offer" not in bindings or "risk" not in bindings:
        raise LoopError(
            f"{merchant_id}: the model stopped before computing an offer "
            f"(ran: {', '.join(called) or 'none'})"
        )

    return _verify_with_retries(
        merchant_id=merchant_id,
        memo=completion.text,
        bindings=bindings,
        ledger=ledger,
        messages=messages,
        provider=provider,
        called=called,
        observer=observer,
    )


def _verify_with_retries(
    merchant_id: str,
    memo: str,
    bindings: dict[str, Any],
    ledger: FactLedger,
    messages: list[dict[str, Any]],
    provider: Provider,
    called: list[str],
    observer: Observer | None,
) -> Decision:
    """Check the memo, and ask again with the reason if it fails (FR-023, FR-024).

    Bounded, and the bound is declared in policy. On exhaustion the memo becomes a summary
    templated from recorded facts -- never an error, never unverified prose.
    """
    offer, risk = bindings["offer"], bindings["risk"]
    verification: VerificationResult | None = None
    provenance: tuple = ()
    rejected: tuple[str, ...] = ()

    for attempt in range(1, policy.MAX_REGENERATION_ATTEMPTS + 1):
        verification, provenance = check_output(memo, ledger, offer, risk, attempt=attempt)
        _emit(observer, "verification", attempt=attempt, passed=verification.passed,
              reason=verification.reason())
        if verification.passed:
            break
        # Remember what the model got wrong, so the fallback can report it.
        rejected = verification.unresolved_numerals or verification.missing_citations
        if attempt == policy.MAX_REGENERATION_ATTEMPTS:
            break

        messages.append(
            prompts.rejection_message(
                verification.reason(), attempt + 1, policy.MAX_REGENERATION_ATTEMPTS
            )
        )
        _emit(observer, "retry", attempt=attempt + 1)
        memo = provider.chat(messages, tools=None).text

    if verification is None or not verification.passed:
        memo = fallback_memo(
            bindings["revenue_metrics"], bindings["volatility"], bindings["flags"], risk, offer
        )
        verification, provenance = check_output(
            memo, ledger, offer, risk,
            attempt=policy.MAX_REGENERATION_ATTEMPTS, used_fallback=True,
        )
        verification = replace(verification, rejected_numerals=tuple(rejected))
        _emit(observer, "fallback", passed=verification.passed)
        if not verification.passed:
            # The safe path must always be available. Reaching here means the fallback
            # itself cannot be verified, which Article II forbids outright.
            raise LoopError(
                f"{merchant_id}: the deterministic fallback failed verification "
                f"({verification.reason()})"
            )

    return Decision(
        merchant_id=merchant_id,
        revenue_metrics=bindings["revenue_metrics"],
        volatility=bindings["volatility"],
        flags=bindings["flags"],
        risk=risk,
        offer=offer,
        memo=memo,
        verification=verification,
        provenance=provenance,
        tool_calls=tuple(called),
        provider_name=provider.name,
        facts=ledger.snapshot(),
    )
