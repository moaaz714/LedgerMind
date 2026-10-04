"""Ollama provider: plain HTTP against a local server.

No HTTP library. `urllib.request` from the standard library does this in about fifteen
lines, and the alternative was depending on a package that only arrives as a transitive of
Streamlit -- relying on something you did not declare is how an environment breaks when an
unrelated dependency drops it.

Financial data never leaves the machine. That is the main reason for a local model, and it
is also why the evaluation can afford to run twenty-two merchants three times over: there
is no meter running, so nothing pushes toward measuring less.

The transport is injectable so the tests exercise the request-building and
response-parsing without a server. Those are the parts that can be wrong in a way a test
can catch; whether the model cooperates is a question only a real run answers.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from ledgermind.llm.base import Completion, ProviderError, ToolCall

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5"
DEFAULT_TIMEOUT_SECONDS = 180


def _post(url: str, payload: dict, timeout: int) -> dict:
    """Raw HTTP. Exceptions are translated by the caller, not here.

    Deliberately thin: translation belongs around *whatever* transport is in use, so an
    injected one gets the same error contract as this. Catching here would have let a
    stubbed transport raise a bare URLError past a caller promised a ProviderError.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class OllamaProvider:
    """Satisfies the `Provider` protocol against a local Ollama server."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        transport: Callable[[str, dict, int], dict] | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _post

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # Zero temperature for reproducibility. The structured offer is tool-computed
            # and so identical regardless, but SC-004 also wants repeated runs to agree,
            # and a deterministic sampler removes one source of drift from the memo.
            "options": {"temperature": 0},
        }
        if tools:
            # The registry publishes neutral schemas; wrapping them in Ollama's envelope is
            # provider-specific work and belongs here, behind the interface, so the registry
            # stays free of any provider's shape.
            payload["tools"] = [{"type": "function", "function": schema} for schema in tools]

        url = f"{self.host}/api/chat"
        try:
            response = self._transport(url, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"ollama returned HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"could not reach ollama at {url}: {exc.reason}. Is the server running?"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"ollama returned a response that is not JSON: {exc}") from exc
        return self._parse(response)

    @staticmethod
    def _parse(response: dict) -> Completion:
        message = response.get("message")
        if not isinstance(message, dict):
            raise ProviderError(f"ollama response has no message object: {response!r}")

        calls = []
        for raw in message.get("tool_calls") or ():
            function = (raw or {}).get("function") or {}
            name = function.get("name")
            if not name:
                raise ProviderError(f"ollama returned a tool call with no name: {raw!r}")
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                # Some builds return the arguments as a JSON string rather than an object.
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(ToolCall(name=name, arguments=dict(arguments)))

        return Completion(text=message.get("content") or "", tool_calls=tuple(calls))
