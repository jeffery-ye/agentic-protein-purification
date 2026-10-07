"""
Tests for ProteinPurificationAgent._resolve_input.

Input type is discriminated heuristically from the raw string, so these pin
down which branch each shape of input takes. CTTdb and the tabulation agent are
stubbed; no database or LLM call is made.
"""

import pytest

from agent_engine.agents import agent_body
from agent_engine.agents.agent_body import AgentResult, ProteinPurificationAgent
from agent_engine.models import BufferStep, SourceKind

# ATG at index 3; translates to MKAW before the TAA stop.
CODING_SEQUENCE = "GGGATGAAAGCTTGGTAA"
TAXONOMY_ROW = ("Mycobacterium", "tuberculosis", "H37Rv", "1773")


@pytest.fixture
def agent():
    return ProteinPurificationAgent()


@pytest.fixture(autouse=True)
def stub_protocol_agent(monkeypatch):
    """_resolve_input constructs a ProtocolAgent unconditionally."""

    class _Stub:
        def find_protocol(self, text):
            return [{"purification_step": "Ni-NTA - Elution"}]

    monkeypatch.setattr(agent_body, "ProtocolAgent", _Stub)


def resolve(agent, value):
    return agent._resolve_input(value, lambda _msg: None)


# --- Branch selection --------------------------------------------------------


def test_uniprot_accession_passes_through(agent):
    fasta_id, taxonomy_id, ssgcid = resolve(agent, "P9WQA3")

    assert fasta_id == "P9WQA3"
    assert taxonomy_id == ""
    assert ssgcid is None


def test_fasta_passes_through_unchanged(agent):
    fasta = ">sp|P9WQA3|Test protein\nMKAWVTLL\n"

    fasta_id, _, ssgcid = resolve(agent, fasta)

    assert fasta_id == fasta
    assert ssgcid is None


def test_long_dotted_identifier_is_not_treated_as_ssgcid(agent):
    """The SSGCID heuristic also requires fewer than 15 characters."""
    value = "some.very.long.identifier"

    fasta_id, _, ssgcid = resolve(agent, value)

    assert fasta_id == value
    assert ssgcid is None


# --- SSGCID branch -----------------------------------------------------------


def test_ssgcid_id_translates_sequence_and_builds_failed_protocol(agent, monkeypatch):
    monkeypatch.setattr(
        agent_body,
        "get_cttdb_info",
        lambda _id: ("Lysed in 50 mM Tris.", CODING_SEQUENCE, TAXONOMY_ROW),
    )

    fasta_id, taxonomy_id, ssgcid = resolve(agent, "MytuD.00516.a")

    assert fasta_id.startswith(">MytuD.00516.a")
    assert "MKAW" in fasta_id
    assert taxonomy_id == "1773"
    assert ssgcid.organism_name == "Mycobacterium tuberculosis"
    assert ssgcid.purification_text == "Lysed in 50 mM Tris."
    assert ssgcid.article_link.endswith("MytuD.00516.a")
    assert ssgcid.protocol == [BufferStep(purification_step="Ni-NTA - Elution")]
    assert ssgcid.source == SourceKind.INTERNAL


def test_sequence_without_start_codon_fails_the_run(agent, monkeypatch):
    monkeypatch.setattr(
        agent_body,
        "get_cttdb_info",
        lambda _id: ("text", "GGGCCCTTT", TAXONOMY_ROW),
    )

    result = resolve(agent, "MytuD.00516.a")

    assert isinstance(result, AgentResult)
    assert result.success is False
    assert "start codon" in result.error_message


def test_cttdb_miss_falls_back_to_the_raw_identifier(agent, monkeypatch):
    monkeypatch.setattr(agent_body, "get_cttdb_info", lambda _id: (None, None, None))

    fasta_id, taxonomy_id, ssgcid = resolve(agent, "MytuD.00516.a")

    assert fasta_id == "MytuD.00516.a"
    assert ssgcid is None


def test_cttdb_error_falls_back_to_the_raw_identifier(agent, monkeypatch):
    def _boom(_id):
        raise RuntimeError("ODBC driver not found")

    monkeypatch.setattr(agent_body, "get_cttdb_info", _boom)

    fasta_id, _, ssgcid = resolve(agent, "MytuD.00516.a")

    assert fasta_id == "MytuD.00516.a"
    assert ssgcid is None


def test_sequence_without_taxonomy_yields_no_failed_protocol(agent, monkeypatch):
    """Taxonomy is the only source of the ids the protocol entry is built from."""
    monkeypatch.setattr(
        agent_body,
        "get_cttdb_info",
        lambda _id: ("text", CODING_SEQUENCE, None),
    )

    fasta_id, taxonomy_id, ssgcid = resolve(agent, "MytuD.00516.a")

    assert fasta_id.startswith(">MytuD.00516.a")
    assert taxonomy_id == ""
    assert ssgcid is None


# --- Known limitation --------------------------------------------------------


def test_known_gap_short_dotted_gene_name_is_misread_as_ssgcid(agent, monkeypatch):
    """
    `len < 15 and "." in value` also matches ordinary short identifiers such as
    an isoform-suffixed gene name, sending them down the CTTdb path. They then
    fall through to the raw identifier and fail UniProt resolution downstream.
    """
    called = {}

    def _record(identifier):
        called["id"] = identifier
        return None, None, None

    monkeypatch.setattr(agent_body, "get_cttdb_info", _record)

    fasta_id, _, _ = resolve(agent, "rpoB.1")

    assert called["id"] == "rpoB.1", "short dotted names are routed to CTTdb"
    assert fasta_id == "rpoB.1"


# --- Sequence fetch ----------------------------------------------------------


def test_the_uniprot_id_cannot_leave_the_entry_path(agent, monkeypatch):
    """The ID is user input, so it is escaped into a single path segment."""
    urls = []

    def fake_urlopen(url, timeout):
        urls.append(url)
        raise agent_body.urllib.error.URLError("offline")

    monkeypatch.setattr(agent_body.urllib.request, "urlopen", fake_urlopen)

    agent._get_fasta_from_uniprot("../search?query=x#y")

    assert urls == ["https://rest.uniprot.org/uniprotkb/..%2Fsearch%3Fquery%3Dx%23y.fasta"]


def test_an_error_message_shortens_a_long_input():
    echoed = agent_body._echo("MKAW" * 1000)

    assert len(echoed) == agent_body.ECHO_LENGTH
    assert echoed.endswith("\u2026")
