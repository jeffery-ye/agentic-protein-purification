"""
Centralized LLM configuration for the agent engine.

The provider is selected with LLM_PROVIDER (defaults to "gemini"):

- "gemini", "openai" or "anthropic": API_KEY holds that provider's key.
- "bedrock": Amazon Bedrock's Converse API, signed with the default AWS
  credential chain (a local profile in dev, the instance role in deploy). No
  API_KEY is needed.

LLM_MODEL names the model within that provider and is required: there is no
default model.

The model is resolved on first use rather than at import, so the server
starts without credentials. A missing or invalid configuration then surfaces
as a clear error on the first LLM call, which the pipeline reports as a failed
extraction or synthesis step.
"""

import os
from dataclasses import replace
from functools import cached_property

from dotenv import load_dotenv
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

from agent_engine.settings import is_deploy

load_dotenv()

DEFAULT_PROVIDER = "gemini"

PROVIDERS = ("gemini", "openai", "anthropic", "bedrock")

# No default model. The paper's original test cases ran on gemini-2.5-pro,
# which Google's Gemini API retires on 2026-10-16.

DEFAULT_AWS_REGION = "us-east-1"


class _OpenAIResponsesModel(OpenAIResponsesModel):
    """
    OpenAI's Responses API with the call's token counts kept.

    pydantic-ai (1.42 through at least 1.107) maps usage through genai-prices,
    whose extracted usage carries `output_reasoning_tokens`, a field
    RequestUsage doesn't accept. The TypeError is swallowed and every count
    comes back 0 for reasoning models, so rebuild them from the response's own
    usage block. The Job Info tab's tokens and costs depend on them (#77).
    """

    def _process_response(self, response, model_request_parameters):
        processed = super()._process_response(response, model_request_parameters)
        usage = response.usage
        if usage is None or processed.usage.input_tokens or processed.usage.output_tokens:
            return processed
        input_details = usage.input_tokens_details
        output_details = usage.output_tokens_details
        return replace(
            processed,
            usage=RequestUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=(input_details.cached_tokens or 0) if input_details else 0,
                details={
                    **processed.usage.details,
                    "reasoning_tokens": (output_details.reasoning_tokens or 0)
                    if output_details
                    else 0,
                },
            ),
        )


def reasoning_summaries_on() -> bool:
    """
    Whether to ask the model for a summary of its reasoning, which the Job Info tab
    shows (#77). LLM_REASONING_SUMMARY=off turns it off, for a model that rejects
    the setting. Only OpenAI's Responses API and Gemini are asked: on Anthropic
    and Bedrock, thinking can clash with the tool-based structured output
    ProtocolAgent uses, so those steps have no reasoning.
    """
    return (os.getenv("LLM_REASONING_SUMMARY") or "auto").strip().lower() != "off"


def resolve_model(provider: str, model_name: str, api_key: str | None) -> Model:
    """Build a pydantic-ai Model for `provider`/`model_name`, authenticated with `api_key`."""
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}; got {provider!r}")
    if not model_name:
        raise ValueError(
            f"LLM_MODEL environment variable not set (required by LLM_PROVIDER={provider!r})"
        )

    if provider == "bedrock":
        return _bedrock_model(model_name)

    if not api_key:
        raise ValueError(
            f"API_KEY environment variable not set (required by LLM_PROVIDER={provider!r})"
        )

    if provider == "openai":
        # OPENAI_BASE_URL is a widely used variable, so it is only honoured for
        # the openai provider. LLM_BASE_URL belongs to this application, so setting
        # it for another provider is a mistake worth reporting rather than ignoring.
        base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        provider_client = OpenAIProvider(base_url=base_url, api_key=api_key)
        # OpenAI itself goes through the Responses API: newer models such as
        # gpt-6-luna refuse function tools with reasoning on Chat Completions.
        # OpenAI-compatible servers (vLLM, Ollama, ...) mostly implement only
        # Chat Completions, so a custom base URL keeps using it.
        if base_url:
            return OpenAIChatModel(model_name, provider=provider_client)
        settings = (
            OpenAIResponsesModelSettings(openai_reasoning_summary="auto")
            if reasoning_summaries_on()
            else None
        )
        return _OpenAIResponsesModel(model_name, provider=provider_client, settings=settings)

    if os.getenv("LLM_BASE_URL"):
        raise ValueError(
            f"LLM_BASE_URL is only supported for the 'openai' provider; LLM_PROVIDER is {provider!r}"
        )

    if provider == "gemini":
        settings = (
            GoogleModelSettings(google_thinking_config={"include_thoughts": True})
            if reasoning_summaries_on()
            else None
        )
        return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key), settings=settings)

    return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))


def _bedrock_model(model_name: str) -> Model:
    """A Bedrock model signed with SigV4 through the default AWS credential chain."""
    if os.getenv("LLM_BASE_URL"):
        raise ValueError(
            "LLM_BASE_URL is only supported for the 'openai' provider; LLM_PROVIDER is 'bedrock'"
        )
    # A Bedrock API key would take precedence over SigV4. The deploy signs with
    # the instance role only, so a key there is a misconfiguration.
    if is_deploy() and os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
        raise ValueError(
            "AWS_BEARER_TOKEN_BEDROCK must not be set in deploy; use the instance role"
        )

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_AWS_REGION
    return BedrockConverseModel(model_name, provider=BedrockProvider(region_name=region))


def _configured_provider_and_model() -> tuple[str, str]:
    provider = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    model_name = (os.getenv("LLM_MODEL") or "").strip()
    return provider, model_name


def configured_model_name() -> str:
    """
    The configured model as "provider:model", without resolving it.

    Needs no credentials, so the job store can record which model a job used.
    """
    provider, model_name = _configured_provider_and_model()
    return f"{provider}:{model_name or 'unset'}"


def configured_model() -> Model:
    """Resolve the model named by LLM_PROVIDER, LLM_MODEL and API_KEY."""
    provider, model_name = _configured_provider_and_model()
    return resolve_model(provider, model_name, os.getenv("API_KEY"))


class LazyModel(WrapperModel):
    """
    A model that resolves its configuration on first use.

    Agents are built with this in place of a concrete model, so constructing
    them (and importing their modules) never needs credentials. A failed
    resolution is not cached: the next call retries it.
    """

    def __init__(self):
        Model.__init__(self)

    @cached_property
    def wrapped(self) -> Model:
        return configured_model()

    def __repr__(self) -> str:
        return "LazyModel()"


# Shared model instance for all agents.
reasoning_model = LazyModel()
