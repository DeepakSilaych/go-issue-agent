"""
Provider-agnostic LLM client that normalizes Anthropic, Groq, and Azure OpenAI APIs.

Both providers are wrapped to a common interface:
  client.chat(messages, system, tools) -> (reply_text, tool_calls, stop_reason)
  client.tool_result_message(tool_call_id, content) -> message dict
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ChatResult:
    text: str               # assistant text (may be empty if tool_use)
    tool_calls: list[ToolCall]
    stop_reason: str        # "end_turn" | "tool_use"
    raw: Any                # original SDK response


# ---------------------------------------------------------------------------
# Anthropic client wrapper
# ---------------------------------------------------------------------------

class AnthropicClient:
    def __init__(self, model: str):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
    ) -> ChatResult:
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools  # already in Anthropic format

        resp = self.client.messages.create(**kwargs)

        text = ""
        tool_calls = []
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        stop = "tool_use" if tool_calls else "end_turn"
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=stop, raw=resp)

    def assistant_message(self, result: ChatResult) -> dict:
        """Convert ChatResult back to the message format for history."""
        return {"role": "assistant", "content": result.raw.content}

    def tool_result_message(self, tool_calls: list[ToolCall], results: list[str]) -> dict:
        """Build a user message containing tool results."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": res,
                }
                for tc, res in zip(tool_calls, results)
            ],
        }


# ---------------------------------------------------------------------------
# Groq / OpenAI-compatible client wrapper
# ---------------------------------------------------------------------------

# OpenAI-format tool definitions (converted from Anthropic format at load time)
def _anthropic_to_openai_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


class GroqClient:
    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, model: str):
        from groq import Groq
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.model = model or self.DEFAULT_MODEL

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
    ) -> ChatResult:
        history = []
        if system:
            history.append({"role": "system", "content": system})
        history.extend(messages)

        kwargs = dict(model=self.model, messages=history, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = _anthropic_to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        stop = resp.choices[0].finish_reason  # "stop" | "tool_calls"

        text = msg.content or ""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

        normalized_stop = "tool_use" if tool_calls else "end_turn"
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=normalized_stop, raw=resp)

    def assistant_message(self, result: ChatResult) -> dict:
        msg = result.raw.choices[0].message
        content = msg.content or ""
        out: dict = {"role": "assistant", "content": content}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        return out

    def tool_result_message(self, tool_calls: list[ToolCall], results: list[str]) -> list[dict]:
        """Groq/OpenAI uses one message per tool result."""
        return [
            {"role": "tool", "tool_call_id": tc.id, "content": res}
            for tc, res in zip(tool_calls, results)
        ]


# ---------------------------------------------------------------------------
# Azure OpenAI client wrapper
# ---------------------------------------------------------------------------

class AzureClient:
    """
    Azure OpenAI via the openai SDK's AzureOpenAI client.

    Required env vars:
      AZURE_OPENAI_API_KEY      — your Azure key
      AZURE_OPENAI_ENDPOINT     — e.g. https://my-resource.openai.azure.com/
      AZURE_OPENAI_DEPLOYMENT   — deployment name, e.g. gpt-4o

    Optional:
      AZURE_OPENAI_API_VERSION  — default: 2024-02-01
    """
    DEFAULT_API_VERSION = "2024-02-01"

    def __init__(self, model: str):
        from openai import AzureOpenAI

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        api_key  = os.environ.get("AZURE_OPENAI_API_KEY", "")
        version  = os.environ.get("AZURE_OPENAI_API_VERSION", self.DEFAULT_API_VERSION)

        if not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is not set.")
        if not api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is not set.")

        self.client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=version,
        )
        # model here is the deployment name; env var overrides --model
        self.model = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model or "gpt-4o"

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
    ) -> ChatResult:
        history = []
        if system:
            history.append({"role": "system", "content": system})
        history.extend(messages)

        kwargs = dict(model=self.model, messages=history, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = _anthropic_to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        text = msg.content or ""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

        normalized_stop = "tool_use" if tool_calls else "end_turn"
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=normalized_stop, raw=resp)

    def assistant_message(self, result: ChatResult) -> dict:
        msg = result.raw.choices[0].message
        out: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        return out

    def tool_result_message(self, tool_calls: list[ToolCall], results: list[str]) -> list[dict]:
        return [
            {"role": "tool", "tool_call_id": tc.id, "content": res}
            for tc, res in zip(tool_calls, results)
        ]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_client(provider: str, model: str):
    if provider == "anthropic":
        return AnthropicClient(model or "claude-sonnet-4-6")
    elif provider == "groq":
        return GroqClient(model or GroqClient.DEFAULT_MODEL)
    elif provider == "azure":
        return AzureClient(model or "gpt-4o")
    else:
        raise ValueError(f"Unknown provider: {provider!r}. Choose 'anthropic', 'groq', or 'azure'.")
