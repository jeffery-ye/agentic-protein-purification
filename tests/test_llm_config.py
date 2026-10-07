"""
Tests for model selection in agent_engine.llm.

resolve_model reads LLM_BASE_URL/OPENAI_BASE_URL from the environment at call
time, so these exercise it directly rather than re-importing the module.
"""

import pytest
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from agent_engine import llm


@pytest.fixture
def clean_env(monkeypatch):
    """Start from an environment with no base-url configuration at all."""
    for name in ["LLM_BASE_URL", "OPENAI_BASE_URL"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_gemini_provider_selects_a_gemini_model(clean_env):
    model = llm.resolve_model("gemini", "gemini-3.8-flash", "dummy")

    assert model.model_name == "gemini-3.8-flash"
    assert model.system == "google-gla"


def test_openai_provider_selects_an_openai_model(clean_env):
    model = llm.resolve_model("openai", "gpt-5.5", "dummy")

    assert model.model_name == "gpt-5.5"
    assert model.system == "openai"


def test_openai_uses_the_responses_api(clean_env):
    """gpt-6-luna rejects function tools with reasoning on Chat Completions."""
    model = llm.resolve_model("openai", "gpt-6-luna", "dummy")

    assert isinstance(model, OpenAIResponsesModel)


def test_a_custom_openai_endpoint_keeps_chat_completions(clean_env):
    """OpenAI-compatible servers mostly implement only Chat Completions."""
    clean_env.setenv("LLM_BASE_URL", "http://localhost:1234/v1")

    assert isinstance(llm.resolve_model("openai", "local-model", "dummy"), OpenAIChatModel)


def test_anthropic_provider_selects_an_anthropic_model(clean_env):
    model = llm.resolve_model("anthropic", "claude-sonnet-5", "dummy")

    assert model.model_name == "claude-sonnet-5"
    assert model.system == "anthropic"


def test_missing_api_key_names_the_variable(clean_env):
    with pytest.raises(ValueError, match="API_KEY"):
        llm.resolve_model("openai", "gpt-5.5", None)


def test_unknown_provider_is_rejected(clean_env):
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        llm.resolve_model("groq", "llama-3.3-70b", "dummy")


# --- Bedrock -----------------------------------------------------------------


@pytest.fixture
def bedrock_env(clean_env):
    """No AWS network access happens here: building the model only creates a boto3 client."""
    clean_env.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    clean_env.setenv("AWS_ACCESS_KEY_ID", "testing")
    clean_env.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    clean_env.delenv("AWS_PROFILE", raising=False)
    return clean_env


def test_bedrock_needs_no_api_key(bedrock_env):
    model = llm.resolve_model("bedrock", "us.anthropic.claude-sonnet-5", None)

    assert model.model_name == "us.anthropic.claude-sonnet-5"
    assert model.system == "bedrock"


def test_bedrock_region_comes_from_the_environment(bedrock_env):
    bedrock_env.setenv("AWS_REGION", "eu-west-1")

    model = llm.resolve_model("bedrock", "us.anthropic.claude-sonnet-5", None)

    assert "eu-west-1" in model.base_url


def test_bedrock_api_key_is_rejected_in_deploy(bedrock_env):
    bedrock_env.setenv("APP_ENV", "deploy")
    bedrock_env.setenv("AWS_BEARER_TOKEN_BEDROCK", "a-key")

    with pytest.raises(ValueError, match="AWS_BEARER_TOKEN_BEDROCK"):
        llm.resolve_model("bedrock", "us.anthropic.claude-sonnet-5", None)


def test_keyed_provider_without_a_key_fails_in_deploy(clean_env):
    """A key left as the Parameter Store placeholder reaches the app as no API_KEY at all."""
    clean_env.setenv("APP_ENV", "deploy")

    with pytest.raises(ValueError, match="API_KEY"):
        llm.resolve_model("gemini", "gemini-3.8-flash", None)


# --- Custom endpoints --------------------------------------------------------


@pytest.mark.parametrize("variable", ["LLM_BASE_URL", "OPENAI_BASE_URL"])
def test_base_url_is_applied_to_openai_models(clean_env, variable):
    clean_env.setenv(variable, "http://localhost:1234/v1")

    model = llm.resolve_model("openai", "local-model", "dummy")

    assert model.model_name == "local-model"
    assert "localhost:1234" in model.base_url


def test_llm_base_url_takes_precedence(clean_env):
    clean_env.setenv("OPENAI_BASE_URL", "http://ignored:9999/v1")
    clean_env.setenv("LLM_BASE_URL", "http://preferred:1234/v1")

    assert "preferred" in llm.resolve_model("openai", "local-model", "dummy").base_url


def test_openai_base_url_is_ignored_for_other_providers(clean_env):
    """OPENAI_BASE_URL is a common global, so it must not break a Gemini setup."""
    clean_env.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")

    assert llm.resolve_model("gemini", "gemini-3.8-flash", "dummy").system == "google-gla"


def test_llm_base_url_on_a_non_openai_provider_is_an_error(clean_env):
    """LLM_BASE_URL belongs to this app, so setting it elsewhere is a mistake."""
    clean_env.setenv("LLM_BASE_URL", "http://localhost:1234/v1")

    with pytest.raises(ValueError, match="only supported for the 'openai' provider"):
        llm.resolve_model("gemini", "gemini-3.8-flash", "dummy")


# --- Lazy resolution ---------------------------------------------------------


@pytest.fixture
def no_api_key(clean_env):
    """An empty value rather than delenv, so a local .env cannot fill it back in."""
    clean_env.setenv("API_KEY", "")
    clean_env.delenv("LLM_PROVIDER", raising=False)
    clean_env.delenv("LLM_MODEL", raising=False)
    return clean_env


def test_building_agents_needs_no_credentials(no_api_key):
    """Importing and constructing agents must not resolve the model."""
    from agent_engine.agents.comprehensive_protocol_agent import SuggestedProtocolAgent
    from agent_engine.agents.extraction_agent import ExtractionAgent
    from agent_engine.agents.outline_protocol_agent import ProtocolAgent

    ProtocolAgent()
    ExtractionAgent()
    SuggestedProtocolAgent()


def test_missing_credentials_fail_at_first_use(no_api_key):
    no_api_key.setenv("LLM_MODEL", "gemini-3.8-flash")
    with pytest.raises(ValueError, match="API_KEY"):
        llm.LazyModel().model_name


def test_there_is_no_default_model(no_api_key):
    no_api_key.setenv("API_KEY", "dummy")
    with pytest.raises(ValueError, match="LLM_MODEL"):
        llm.LazyModel().model_name


def test_lazy_model_resolves_the_configured_provider(no_api_key):
    no_api_key.setenv("API_KEY", "dummy")
    no_api_key.setenv("LLM_PROVIDER", "anthropic")
    no_api_key.setenv("LLM_MODEL", "claude-sonnet-5")

    model = llm.LazyModel()

    assert model.model_name == "claude-sonnet-5"
    assert model.system == "anthropic"


def test_a_failed_resolution_is_retried(no_api_key):
    model = llm.LazyModel()
    with pytest.raises(ValueError):
        model.model_name

    no_api_key.setenv("API_KEY", "dummy")
    no_api_key.setenv("LLM_MODEL", "gemini-3.8-flash")

    assert model.model_name == "gemini-3.8-flash"


def test_the_configured_model_name_needs_no_credentials(no_api_key):
    no_api_key.setenv("LLM_PROVIDER", "Bedrock")
    no_api_key.setenv("LLM_MODEL", "us.anthropic.claude-sonnet-5")

    assert llm.configured_model_name() == "bedrock:us.anthropic.claude-sonnet-5"


def test_an_unset_model_is_recorded_as_unset(no_api_key):
    assert llm.configured_model_name() == "gemini:unset"
