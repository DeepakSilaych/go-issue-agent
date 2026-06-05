"""
LangChain chat-model factory.

Returns a provider-specific `BaseChatModel` (Anthropic / Groq / Azure OpenAI) with a
single, uniform interface. The rest of the system speaks LangChain message objects
(SystemMessage / HumanMessage / AIMessage / ToolMessage) and tool calling, so there is
no per-provider normalization code to maintain — LangChain handles it.

Usage:
    llm = make_chat_model("anthropic", "claude-sonnet-4-6")
    llm.invoke([HumanMessage(content="hi")])               # plain completion
    llm.with_structured_output(MySchema).invoke([...])     # structured JSON
    llm.bind(max_tokens=2048).invoke([...])                # per-call overrides
"""

import os

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "groq": "llama-3.3-70b-versatile",
    "azure": "gpt-4o",
}

# Anthropic requires max_tokens; pick a default large enough for a patch turn.
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 6  # SDKs back off on 429/503 internally


def make_chat_model(provider: str, model: str = "", max_tokens: int = DEFAULT_MAX_TOKENS):
    """Build a LangChain chat model for the given provider."""
    provider = provider.lower()
    model = model or DEFAULT_MODELS.get(provider, "")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            max_tokens=max_tokens,
            max_retries=MAX_RETRIES,
            timeout=120,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            max_tokens=max_tokens,
            max_retries=MAX_RETRIES,
        )

    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        if not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is not set.")
        # Newer OpenAI/Azure models (o-series, gpt-5 family) reject `max_tokens` and
        # require `max_completion_tokens`; passing it via model_kwargs works for both
        # those and classic chat models.
        return AzureChatOpenAI(
            azure_deployment=model or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or "gpt-4o",
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_endpoint=endpoint,
            max_retries=MAX_RETRIES,
            model_kwargs={"max_completion_tokens": max_tokens},
        )

    raise ValueError(
        f"Unknown provider: {provider!r}. Choose 'anthropic', 'groq', or 'azure'."
    )
