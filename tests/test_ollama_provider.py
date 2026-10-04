"""The Ollama provider (T018), with the transport stubbed.

What a test can establish here: that the request is built correctly and the response is
parsed correctly, including the shapes a real Ollama build actually returns. What it cannot
establish is whether the model cooperates -- that is settled by a real run, not a fixture,
and pretending otherwise would be the kind of test that passes while the thing is broken.
"""

import json

import pytest

from ledgermind.llm.base import Completion, ProviderError
from ledgermind.llm.ollama import OllamaProvider


class Stub:
    """Records the request and returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.seen = []

    def __call__(self, url, payload, timeout):
        self.seen.append({"url": url, "payload": payload, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _provider(response):
    stub = Stub(response)
    return OllamaProvider(transport=stub), stub


# --- the request ----------------------------------------------------------------------


def test_the_request_goes_to_the_chat_endpoint():
    provider, stub = _provider({"message": {"content": "hello"}})
    provider.chat([{"role": "user", "content": "hi"}])
    assert stub.seen[0]["url"] == "http://127.0.0.1:11434/api/chat"


def test_temperature_is_zero_for_reproducibility():
    """SC-004 wants repeated runs to agree. The offer is tool-computed and so identical
    regardless, but a deterministic sampler removes one source of drift from the memo."""
    provider, stub = _provider({"message": {"content": "hello"}})
    provider.chat([{"role": "user", "content": "hi"}])
    assert stub.seen[0]["payload"]["options"]["temperature"] == 0


def test_streaming_is_off():
    provider, stub = _provider({"message": {"content": "hello"}})
    provider.chat([{"role": "user", "content": "hi"}])
    assert stub.seen[0]["payload"]["stream"] is False


def test_tool_schemas_are_wrapped_in_ollamas_envelope():
    """The registry publishes neutral schemas; the envelope is this provider's business.

    Keeping the wrapping here is what lets the registry stay free of any provider's shape,
    which is the whole point of the interface.
    """
    provider, stub = _provider({"message": {"content": "hello"}})
    provider.chat([], tools=[{"name": "compute_offer", "description": "d", "parameters": {}}])

    sent = stub.seen[0]["payload"]["tools"]
    assert sent == [
        {"type": "function", "function": {"name": "compute_offer", "description": "d", "parameters": {}}}
    ]


def test_no_tools_key_when_none_are_offered():
    provider, stub = _provider({"message": {"content": "hello"}})
    provider.chat([{"role": "user", "content": "hi"}])
    assert "tools" not in stub.seen[0]["payload"]


def test_the_model_name_is_reported():
    provider = OllamaProvider(model="qwen2.5", transport=Stub({"message": {"content": ""}}))
    assert provider.name == "ollama/qwen2.5"


def test_a_trailing_slash_on_the_host_does_not_double_up():
    stub = Stub({"message": {"content": ""}})
    OllamaProvider(host="http://localhost:11434/", transport=stub).chat([])
    assert stub.seen[0]["url"] == "http://localhost:11434/api/chat"


# --- the response ---------------------------------------------------------------------


def test_prose_becomes_a_completion_with_no_tool_calls():
    provider, _ = _provider({"message": {"role": "assistant", "content": "the memo"}})
    completion = provider.chat([])
    assert completion == Completion(text="the memo")
    assert completion.wants_tools is False


def test_tool_calls_are_parsed():
    provider, _ = _provider(
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "compute_revenue_metrics", "arguments": {"transactions": "transactions"}}}
                ],
            }
        }
    )
    completion = provider.chat([])
    assert completion.wants_tools is True
    assert completion.tool_calls[0].name == "compute_revenue_metrics"
    assert completion.tool_calls[0].arguments == {"transactions": "transactions"}


def test_arguments_arriving_as_a_json_string_are_decoded():
    """Some builds return the arguments as a string rather than an object."""
    provider, _ = _provider(
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "score_risk", "arguments": json.dumps({"a": 1})}}],
            }
        }
    )
    assert provider.chat([]).tool_calls[0].arguments == {"a": 1}


def test_unparseable_arguments_degrade_to_empty_rather_than_crashing():
    """The loop supplies every value by name anyway, so malformed arguments cost nothing.

    Crashing here would turn a cosmetic quirk of the model's output into a failed run.
    """
    provider, _ = _provider(
        {"message": {"content": "", "tool_calls": [{"function": {"name": "score_risk", "arguments": "{oh no"}}]}}
    )
    assert provider.chat([]).tool_calls[0].arguments == {}


def test_prose_alongside_tool_calls_is_kept():
    provider, _ = _provider(
        {
            "message": {
                "content": "Let me check the revenue first.",
                "tool_calls": [{"function": {"name": "compute_revenue_metrics"}}],
            }
        }
    )
    completion = provider.chat([])
    assert completion.text == "Let me check the revenue first."
    assert completion.wants_tools is True, "tool calls take precedence while analyses remain"


def test_a_missing_content_field_becomes_an_empty_string():
    provider, _ = _provider({"message": {"role": "assistant"}})
    assert provider.chat([]).text == ""


# --- failures -------------------------------------------------------------------------


def test_a_response_without_a_message_is_an_error():
    provider, _ = _provider({"error": "model not found"})
    with pytest.raises(ProviderError, match="no message object"):
        provider.chat([])


def test_a_tool_call_without_a_name_is_an_error():
    """Unlike malformed arguments, this is unusable: there is nothing to dispatch."""
    provider, _ = _provider({"message": {"content": "", "tool_calls": [{"function": {}}]}})
    with pytest.raises(ProviderError, match="no name"):
        provider.chat([])


def test_an_unreachable_server_says_so_and_suggests_why():
    import urllib.error

    provider, _ = _provider(urllib.error.URLError("connection refused"))
    with pytest.raises(ProviderError, match="Is the server running"):
        provider.chat([])
