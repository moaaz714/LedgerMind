"""The provider interface.

Whether a 7B model is reliable enough at tool-calling is this project's main technical
risk, so the boundary exists to make that bet reversible in an afternoon. No
provider-specific request or response type may cross it: a caller that has to know whether
it is talking to Ollama or Groq is a caller that cannot be switched.

Deliberately small. A chat call takes messages and optional tool schemas and returns
either tool calls or prose. Everything else a provider might offer -- streaming,
embeddings, logprobs -- is absent because nothing in this pipeline needs it, and an
interface wide enough to be convenient is an interface too wide to reimplement quickly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    """A request from the model to run one analysis."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Completion:
    """One provider response.

    A response may carry tool calls, prose, or both -- some models emit a sentence of
    commentary alongside a call. The loop treats `tool_calls` as taking precedence: while
    the model still wants analyses run, its prose is working notes rather than the memo.
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class Provider(Protocol):
    """A language model the agent loop can drive."""

    @property
    def name(self) -> str:
        """Identifies the provider and model, for the evaluation report."""
        ...

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """Send the conversation so far and return the model's next move."""
        ...


class ProviderError(RuntimeError):
    """The provider could not be reached or returned something unusable."""
