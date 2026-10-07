"""
A job's trace (#77): each LLM call's reasoning, tokens, time and cost, and each
stage's time, stored with the job and served by /trace.
"""

import pytest
from fastapi.testclient import TestClient
from openai.types import responses
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

import main
from agent_engine.agents.extraction_agent import ExtractionAgent
from agent_engine.auth import DEV_USER
from agent_engine.job_store import InMemoryJobRepository, Owner
from agent_engine.job_store.sqlite import SqliteJobRepository
from agent_engine.llm import _OpenAIResponsesModel
from agent_engine.trace import StageClock, Trace, TraceStep, collecting, set_subject
from schemas import PurificationRequest


def _thinking_model(messages, info):
    return ModelResponse(
        parts=[
            ThinkingPart(content="The Methods name Ni-NTA."),
            TextPart(content="Ni-NTA, 250 mM imidazole."),
        ],
        usage=RequestUsage(input_tokens=120, output_tokens=30, details={"reasoning_tokens": 12}),
        model_name="test-model",
    )


def test_an_llm_call_is_recorded_with_its_reasoning_and_usage():
    agent = ExtractionAgent()
    trace = Trace(model="test:model")

    with collecting(trace), agent.agent.override(model=FunctionModel(_thinking_model)):
        set_subject("PDB 1ABC", "1ABC")
        text = agent.run("Methods.", "Aldolase")

    assert text == "Ni-NTA, 250 mM imidazole."
    [step] = trace.steps
    assert (step.agent, step.subject, step.source_key) == ("extraction", "PDB 1ABC", "1ABC")
    assert step.reasoning == "The Methods name Ni-NTA."
    assert step.output == "Ni-NTA, 250 mM imidazole."
    assert (step.input_tokens, step.output_tokens, step.reasoning_tokens) == (120, 30, 12)
    # "test-model" has no public price.
    assert step.cost_usd is None and trace.cost_usd is None
    assert (trace.input_tokens, trace.output_tokens, trace.reasoning_tokens) == (120, 30, 12)


def _failing_model(messages, info):
    raise RuntimeError("provider quota exhausted")


def test_a_failed_call_is_recorded_with_its_error_then_raised():
    """A failed run still spent time, and whatever the model answered before failing."""
    agent = ExtractionAgent()
    trace = Trace()

    with collecting(trace), agent.agent.override(model=FunctionModel(_failing_model)):
        set_subject("PDB 1ABC", "1ABC")
        with pytest.raises(RuntimeError, match="quota"):
            agent.run("Methods.", "Aldolase")

    [step] = trace.steps
    assert (step.agent, step.source_key) == ("extraction", "1ABC")
    assert step.error == "RuntimeError: provider quota exhausted"
    assert step.output is None and step.input_tokens == 0


def test_calls_outside_a_trace_are_not_recorded():
    agent = ExtractionAgent()

    with agent.agent.override(model=FunctionModel(_thinking_model)):
        assert agent.run("Methods.", "Aldolase") == "Ni-NTA, 250 mM imidazole."


def test_stages_are_timed_in_order():
    trace = Trace()
    with collecting(trace):
        clock = StageClock()
        clock.done("Input resolution")
        clock.done("BLAST and ranking")

    assert [stage.name for stage in trace.stages] == ["Input resolution", "BLAST and ranking"]
    assert trace.total_seconds == round(sum(s.seconds for s in trace.stages), 2)


def test_the_total_cost_needs_every_steps_price():
    trace = Trace.model_validate(
        {
            "steps": [
                {"agent": "planner", "seconds": 1, "cost_usd": 0.01},
                {"agent": "formatter", "seconds": 1, "cost_usd": 0.02},
            ]
        }
    )
    assert trace.cost_usd == 0.03

    trace.steps[1].cost_usd = None
    assert trace.cost_usd is None


# --- Token counts on OpenAI's Responses API ----------------------------------


def test_token_counts_survive_the_responses_api_usage_mapping():
    """pydantic-ai drops them when genai-prices reports reasoning tokens (#77)."""
    response = responses.Response.model_validate(
        {
            "id": "resp_1",
            "created_at": 1790000000,
            "model": "gpt-6-luna",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Hi", "annotations": []}],
                }
            ],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 43,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 233,
                "output_tokens_details": {"reasoning_tokens": 130},
                "total_tokens": 276,
            },
        }
    )
    model = _OpenAIResponsesModel("gpt-6-luna", provider=OpenAIProvider(api_key="test"))

    processed = model._process_response(response, ModelRequestParameters())

    assert (processed.usage.input_tokens, processed.usage.output_tokens) == (43, 233)
    assert processed.usage.details["reasoning_tokens"] == 130
    assert processed.cost().total_price > 0  # gpt-6-luna is in genai-prices 0.1.8


# --- Storage and /trace ------------------------------------------------------


@pytest.fixture
def repo(monkeypatch):
    repo = InMemoryJobRepository()
    monkeypatch.setattr(main, "_job_repo", repo)
    return repo


def _job(repo, job_id="job"):
    request = PurificationRequest(fasta_id=">t\nMKAW")
    repo.create(job_id, request.model_dump(mode="json"), DEV_USER.owner, None, None)
    return request


def test_a_failed_job_keeps_the_steps_it_recorded(repo, monkeypatch):
    def crash(self, **kwargs):
        kwargs["trace"].steps.append(TraceStep(agent="structuring", seconds=2.5))
        raise RuntimeError("boom")

    monkeypatch.setattr(main.ProteinPurificationAgent, "run", crash)
    main.run_agent_task(repo, "job", _job(repo))

    assert repo.get("job").state == "failed"
    [step] = repo.get_trace("job")["steps"]
    assert (step["agent"], step["seconds"]) == ("structuring", 2.5)


def test_trace_serves_the_stored_trace_and_null_without_one(repo, monkeypatch):
    monkeypatch.setattr(main, "run_agent_task", lambda *args: None)
    main.app.dependency_overrides[main.current_user] = lambda: DEV_USER
    try:
        client = TestClient(main.app)
        _job(repo, "old")
        _job(repo, "new")
        repo.save_trace("new", Trace(model="openai:gpt-6-luna").model_dump(mode="json"))

        assert client.get("/trace/old").json() is None
        assert client.get("/trace/new").json()["model"] == "openai:gpt-6-luna"
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)


def test_sqlite_stores_a_trace_apart_from_the_job(tmp_path):
    repo = SqliteJobRepository(tmp_path / "jobs.sqlite3")
    repo.create("job", {"fasta_id": "x"}, Owner("sub", "user"), None, None)

    assert repo.get_trace("job") is None
    assert repo.save_trace("job", {"model": "m", "steps": [], "stages": []})
    assert repo.get_trace("job") == {"model": "m", "steps": [], "stages": []}
    assert repo.get("job").state == "queued"  # the record itself is untouched
