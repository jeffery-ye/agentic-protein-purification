"""
Regression tests for issue #6.

A synthesis exception used to be swallowed and replaced with placeholder prose
while the job still reported COMPLETED, so a failed run was indistinguishable
from a successful one.
"""

import pytest

from agent_engine.agents import agent_body
from agent_engine.agents.agent_body import AgentResult, ProteinPurificationAgent
from agent_engine.models import Hit, SourceProtocol
from schemas import ProtocolResult

PURIFICATIONS = [SourceProtocol(purification_text="Lysed in 50 mM Tris.")]
HIT = Hit(
    protein_name="pdb|1ABC|A",
    pdb_id="1ABC",
    length=100,
    e_value=1e-30,
    pident=90.0,
    query_coverage=95.0,
    query_start=1,
    query_end=100,
    subject_start=1,
    subject_end=100,
    similarity_score=0.9,
)


@pytest.fixture
def agent():
    return ProteinPurificationAgent()


def synthesize(agent):
    return agent._synthesize(PURIFICATIONS, None, None, lambda _msg: None)


def stub_suggestion_agent(monkeypatch, behavior):
    class _Stub:
        run = staticmethod(behavior)

    monkeypatch.setattr(agent_body, "SuggestedProtocolAgent", _Stub)


def test_failure_returns_the_error_instead_of_placeholder_prose(agent, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("429 quota exceeded")

    stub_suggestion_agent(monkeypatch, _boom)

    protocol, raw_plan, error = synthesize(agent)

    assert protocol is None
    assert raw_plan is None
    assert error == "RuntimeError: 429 quota exceeded"


def test_error_includes_the_exception_type(agent, monkeypatch):
    """Quota, safety blocks and context overflow are told apart by type."""

    def _boom(*args, **kwargs):
        raise ValueError("context length exceeded")

    stub_suggestion_agent(monkeypatch, _boom)

    assert synthesize(agent)[2].startswith("ValueError:")


def test_success_path_reports_no_error(agent, monkeypatch):
    stub_suggestion_agent(monkeypatch, lambda *a, **k: ("## Step 0", "raw draft"))

    protocol, raw_plan, error = synthesize(agent)

    assert (protocol, raw_plan, error) == ("## Step 0", "raw draft", None)


def test_partial_result_keeps_blast_and_source_protocols():
    """A synthesis failure must not discard the expensive part of the run."""
    result = AgentResult(
        success=True,
        purifications=PURIFICATIONS,
        similar_proteins=[HIT],
        synthesis_error="RuntimeError: 429 quota exceeded",
    )

    payload = ProtocolResult(
        purifications=result.purifications,
        comprehensive_protocol=result.comprehensive_protocol,
        raw_plan=result.raw_plan,
        blast_results=result.similar_proteins,
        error_message=result.synthesis_error,
    )

    assert payload.error_message == "RuntimeError: 429 quota exceeded"
    assert payload.comprehensive_protocol is None
    assert payload.blast_results and payload.purifications


def test_synthesis_error_is_distinct_from_hard_failure():
    """error_message is reserved for runs that failed outright."""
    partial = AgentResult(success=True, synthesis_error="RuntimeError: boom")
    hard = AgentResult(success=False, error_message="No BLAST results found.")

    assert partial.error_message is None
    assert hard.synthesis_error is None


def test_a_skip_is_distinct_from_a_synthesis_error():
    """
    #89: a skip is an expected outcome of finding no literature, so the report
    must not present it as a provider failure the owner should investigate.
    """
    skipped = AgentResult(success=True, synthesis_skipped=agent_body.NO_SOURCE_PROTOCOLS)
    failed = AgentResult(success=True, synthesis_error="ModelHTTPError: status_code: 400")

    assert skipped.synthesis_error is None
    assert failed.synthesis_skipped is None


def test_a_failed_purification_the_llm_cannot_read_fails_with_the_providers_message(
    agent, monkeypatch
):
    """Reading the pasted failed protocol is the first LLM call, so its error must reach the user."""

    class _Grounding:
        def get_uniprot_metadata(self, _query):
            return None

    class _Protocol:
        def find_protocol(self, _text):
            raise RuntimeError("429 quota exceeded")

    monkeypatch.setattr(agent_body, "GroundingTool", _Grounding)
    monkeypatch.setattr(agent_body, "ProtocolAgent", _Protocol)
    monkeypatch.setattr(agent, "_get_fasta_from_uniprot", lambda _id: ">P9WQA3\nMKAW\n")

    result = agent.run(
        protein_name="P9WQA3",
        min_pident=40,
        min_qcov=40,
        max_evalue=1e-3,
        max_hits=5,
        max_protocols=5,
        failed_purification_text="Lysed in 50 mM Tris; precipitated on Ni-NTA.",
    )

    assert (result.success, result.error_message) == (False, "429 quota exceeded")
