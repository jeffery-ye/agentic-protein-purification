"""
The report contract (#46): typed hits, papers and source protocols.

Stored reports are never migrated, so a report stored before a field
existed must still validate, and status strings must never be reworded.
"""

import copy
import json
from pathlib import Path

import pytest

from agent_engine.agent_tools import grounding_tool
from agent_engine.agent_tools.grounding_tool import GroundingTool
from agent_engine.models import HitStatus, Paper, PaperAccess
from schemas import ProtocolResult
from scripts.export_openapi import OUTPUT, openapi_json

SNAPSHOT = Path(__file__).parent / "fixtures" / "pipeline_snapshot.json"

# Added by #46. A report stored before then has none of them.
ADDED_HIT_FIELDS = ("pmid",)
ADDED_PROTOCOL_FIELDS = ("source", "pdb_id")


def test_the_committed_openapi_schema_is_current():
    """The frontend's types are generated from it; regenerate both when this fails."""
    assert OUTPUT.read_text(encoding="utf-8") == openapi_json(), (
        "Run `uv run python scripts/export_openapi.py`, then `npm run gen:api` in "
        "purification-rescue-frontend"
    )


def test_status_values_are_the_strings_stored_reports_hold():
    assert [status.value for status in HitStatus] == [
        "Not Analyzed",
        "No RCSB Metadata",
        "Citation Lookup Failed",
        "No PMC Primary Citation",
        "Paper Already Found",
        "No Open Access",
        "PMC Retrieval Failed",
        "No Protocol Found in Paper",
        "Protocol Found",
        "Error Processing",
    ]


def _report_from_before_46() -> dict:
    report = copy.deepcopy(json.loads(SNAPSHOT.read_text(encoding="utf-8"))["result"])
    del report["papers"]
    for hit in report["blast_results"]:
        for name in ADDED_HIT_FIELDS:
            del hit[name]
        # Ranking only set these keys when RCSB had them.
        for name in ("uniprot_id", "organism_name", "taxonomy_id"):
            if hit[name] is None:
                del hit[name]
    for protocol in report["purifications"]:
        for name in ADDED_PROTOCOL_FIELDS:
            del protocol[name]
    return report


def test_a_report_stored_before_typed_state_still_validates():
    old = _report_from_before_46()

    report = ProtocolResult.model_validate(old)

    assert report.papers is None
    assert [hit.status for hit in report.blast_results][:2] == [
        HitStatus.PROTOCOL_FOUND,
        HitStatus.NO_PMC_PRIMARY_CITATION,
    ]
    assert all(protocol.source is None for protocol in report.purifications)


def test_a_stored_report_round_trips_unchanged():
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["result"]

    assert ProtocolResult.model_validate(stored).model_dump(mode="json") == stored


@pytest.mark.parametrize(
    ("pmcid", "doi", "link"),
    [
        ("PMC1", "10.1/x", "https://pmc.ncbi.nlm.nih.gov/articles/PMC1"),
        (None, "10.1/x", "https://doi.org/10.1/x"),
        (None, None, "https://pubmed.ncbi.nlm.nih.gov/123"),
    ],
)
def test_a_papers_link_prefers_pmc_then_doi_then_pubmed(pmcid, doi, link):
    assert Paper(pmid="123", pmcid=pmcid, doi=doi).link == link


# --- The citation lookup -----------------------------------------------------


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise grounding_tool.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _lookup(monkeypatch, response):
    def get(url, timeout=None, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(grounding_tool.requests, "get", get)
    return GroundingTool().lookup_citation("1ABC")


def test_an_entry_with_no_publication_has_no_pmc_citation(monkeypatch):
    assert _lookup(monkeypatch, _Response(404)) == (None, HitStatus.NO_PMC_PRIMARY_CITATION)


@pytest.mark.parametrize(
    "response",
    [_Response(503), grounding_tool.requests.Timeout("read timed out")],
    ids=["server_error", "timeout"],
)
def test_a_failed_lookup_is_not_counted_as_access_dropout(monkeypatch, response):
    assert _lookup(monkeypatch, response) == (None, HitStatus.CITATION_LOOKUP_FAILED)


def test_a_pubmed_only_paper_is_not_in_pmc(monkeypatch):
    record = {
        "rcsb_id": "21134638",
        "rcsb_pubmed_container_identifiers": {"pubmed_id": 21134638},
        "rcsb_pubmed_doi": "10.1000/abc",
        "rcsb_pubmed_abstract_text": "An abstract.",
    }

    paper, status = _lookup(monkeypatch, _Response(200, record))

    assert status is None
    assert paper == Paper(
        pmid="21134638",
        doi="10.1000/abc",
        abstract="An abstract.",
        access=PaperAccess.NOT_IN_PMC,
    )


def test_an_abstract_is_stored_as_plain_text(monkeypatch):
    record = {
        "rcsb_id": "1",
        "rcsb_pubmed_abstract_text": " <i>Mycobacterium tuberculosis </i> ( <i>Mtb </i>) &amp; H<sub>2</sub>O.",
    }

    paper, _ = _lookup(monkeypatch, _Response(200, record))

    assert paper.abstract == "Mycobacterium tuberculosis ( Mtb ) & H2O."


@pytest.mark.parametrize(
    ("access", "listed"),
    [
        (PaperAccess.PMC_RESTRICTED, True),
        (PaperAccess.NOT_IN_PMC, True),
        (PaperAccess.OPEN, False),
        (PaperAccess.FETCH_FAILED, False),  # access unknown, not closed
        (None, False),  # past the protocol stop, never fetched
    ],
)
def test_only_closed_access_papers_are_listed_for_manual_review(access, listed):
    assert Paper(pmid="1", access=access).for_manual_review is listed


def test_a_pmc_paper_waits_for_the_fetch_to_learn_its_access(monkeypatch):
    record = {"rcsb_id": "1", "rcsb_pubmed_central_id": "PMC9"}

    paper, _ = _lookup(monkeypatch, _Response(200, record))

    assert (paper.pmid, paper.pmcid, paper.access) == ("1", "PMC9", None)
