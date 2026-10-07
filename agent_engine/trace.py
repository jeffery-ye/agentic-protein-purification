"""
A job's trace (#77): every LLM call the pipeline made, with the model's summary
of its reasoning, its output, tokens, wall time and estimated cost, plus the wall
time of each stage.

The trace for the job running in this thread is held in a context variable, so
agents record their calls without the pipeline threading an object through
every signature. `collecting` sets it for a run; `set_subject` names what the
calls after it work on; `StageClock` times stages; `run_traced` runs an agent
and records the call.

Costs come from pydantic-ai's built-in pricing (genai-prices) and are estimates
from public prices. A model missing from the price data gets no cost.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, List, Literal, Optional

from pydantic import BaseModel, computed_field
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import ModelResponse, ThinkingPart

AgentName = Literal["extraction", "structuring", "planner", "formatter"]


class TraceStep(BaseModel):
    """One LLM call."""

    agent: AgentName
    # What the call worked on, for people: "PDB 1ABC", "Synthesis".
    subject: Optional[str] = None
    # Links the call to its source protocol: the hit's PDB ID for a paper,
    # "user" or "internal" for the failed protocol, None for synthesis.
    source_key: Optional[str] = None
    model: Optional[str] = None
    # The model's summary of its reasoning; None when the provider returns none.
    reasoning: Optional[str] = None
    output: Optional[str] = None
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Optional[float] = None
    # Set when the call failed: the exception, as the job's error reports it.
    # Its tokens and cost are what the model answered before the failure.
    error: Optional[str] = None


class StageTime(BaseModel):
    name: str
    seconds: float


class Trace(BaseModel):
    model: Optional[str] = None  # the configured "provider:model"
    steps: List[TraceStep] = []
    stages: List[StageTime] = []

    @computed_field
    @property
    def total_seconds(self) -> float:
        return round(sum(stage.seconds for stage in self.stages), 2)

    @computed_field
    @property
    def input_tokens(self) -> int:
        return sum(step.input_tokens for step in self.steps)

    @computed_field
    @property
    def output_tokens(self) -> int:
        return sum(step.output_tokens for step in self.steps)

    @computed_field
    @property
    def reasoning_tokens(self) -> int:
        return sum(step.reasoning_tokens for step in self.steps)

    @computed_field
    @property
    def cost_usd(self) -> Optional[float]:
        """The estimated total, or None when any step's model has no price."""
        costs = [step.cost_usd for step in self.steps]
        if any(cost is None for cost in costs):
            return None
        return round(sum(costs), 6)


_trace: ContextVar[Optional[Trace]] = ContextVar("trace", default=None)
_subject: ContextVar[tuple[Optional[str], Optional[str]]] = ContextVar(
    "trace_subject", default=(None, None)
)


@contextmanager
def collecting(trace: Trace) -> Iterator[Trace]:
    """Record the LLM calls and stages made inside this block into `trace`."""
    trace_token = _trace.set(trace)
    subject_token = _subject.set((None, None))
    try:
        yield trace
    finally:
        _subject.reset(subject_token)
        _trace.reset(trace_token)


def set_subject(label: Optional[str], source_key: Optional[str] = None) -> None:
    """Name what the following LLM calls work on, until the next call to this."""
    _subject.set((label, source_key))


class StageClock:
    """Times consecutive stages: each `done` records the time since the last one."""

    def __init__(self):
        self._last = time.perf_counter()

    def done(self, name: str) -> None:
        now = time.perf_counter()
        trace = _trace.get()
        if trace is not None:
            trace.stages.append(StageTime(name=name, seconds=round(now - self._last, 2)))
        self._last = now


def run_traced(agent: Agent, name: AgentName, prompt: str):
    """
    `agent.run_sync(prompt)`, recorded as a step of the current trace. A call
    that raises is recorded too, with its error, then re-raised: a failed run
    still spent what the model answered before the failure.
    """
    start = time.perf_counter()
    with capture_run_messages() as messages:
        try:
            result = agent.run_sync(prompt)
        except Exception as e:
            _record(name, messages, None, start, error=f"{type(e).__name__}: {e}")
            raise
    _record(name, result.new_messages(), result.output, start)
    return result


def _record(name: AgentName, messages, output, start: float, error: Optional[str] = None) -> None:
    trace = _trace.get()
    if trace is not None:
        seconds = time.perf_counter() - start
        trace.steps.append(_step(name, messages, output, seconds, error))


def _step(name: AgentName, messages, output, seconds: float, error: Optional[str]) -> TraceStep:
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    reasoning = "\n\n".join(
        part.content
        for response in responses
        for part in response.parts
        if isinstance(part, ThinkingPart) and part.content
    )
    usages = [response.usage for response in responses]
    label, source_key = _subject.get()
    if output is not None and not isinstance(output, str):
        output = output.model_dump_json(indent=2)
    return TraceStep(
        agent=name,
        subject=label,
        source_key=source_key,
        model=responses[-1].model_name if responses else None,
        reasoning=reasoning or None,
        output=output,
        seconds=round(seconds, 2),
        input_tokens=sum(usage.input_tokens or 0 for usage in usages),
        output_tokens=sum(usage.output_tokens or 0 for usage in usages),
        # OpenAI reports reasoning tokens as reasoning_tokens, Gemini as thoughts_tokens.
        reasoning_tokens=sum(
            (usage.details or {}).get("reasoning_tokens", 0)
            + (usage.details or {}).get("thoughts_tokens", 0)
            for usage in usages
        ),
        cost_usd=_cost(responses),
        error=error,
    )


def _cost(responses: list[ModelResponse]) -> Optional[float]:
    try:
        return round(sum(float(r.cost().total_price) for r in responses), 6)
    except Exception:
        # LookupError when the model isn't in the price data; an assertion
        # when a test model has no name.
        return None
