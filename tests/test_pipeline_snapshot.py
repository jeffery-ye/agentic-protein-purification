"""
The whole pipeline, from a request to the stored report, against a snapshot.

Only the outermost boundaries are stubbed: HTTP (UniProt, RCSB), the PMC fetch,
blastp and the LLM agents. Input resolution, ranking, the per-hit loop,
MethodsTool's parsing and ProtocolResult are the real code, so a change to
what a job stores or reports shows up here as a diff to review.

The snapshot is tests/fixtures/pipeline_snapshot.json. After a deliberate
change, regenerate it and review the diff before committing:

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_pipeline_snapshot.py
"""

import json
import os
import urllib.error
from pathlib import Path

import pytest

import main
from agent_engine.agent_tools import grounding_tool, protein_similarity_tool
from agent_engine.agents import agent_body
from agent_engine.job_store import InMemoryJobRepository, Owner
from agent_engine.models import Hit
from schemas import PurificationRequest

SNAPSHOT = Path(__file__).parent / "fixtures" / "pipeline_snapshot.json"

FASTA = ">sp|P9WQA3|ALF_MYCTU Fructose-bisphosphate aldolase\nMKAWVTLLAGLLAAQ\n"

# One hit per per-hit outcome, in BLAST order. The PMIDs drive RCSB's citation
# stub (None: no publication, a 404), and the PMC IDs drive the PMC fetch stub.
HITS = {
    "1ABC": {"pident": 92.0, "pmid": "11111111", "pmc": "PMC1234567"},  # Protocol Found
    "2DEF": {"pident": 88.0, "pmid": None},  # no publication: No PMC Primary Citation
    "3GHI": {"pident": 85.0, "pmid": "11111111", "pmc": "PMC1234567"},  # Paper Already Found
    "4JKL": {"pident": 80.0, "pmid": "44444444", "pmc": "PMC-FETCH-FAILS"},  # fetch fails
    "5MNO": {"pident": 75.0, "pmid": "55555555", "pmc": "PMC-CLOSED"},  # No Open Access
    # No RCSB metadata: ranked by identity alone. The loop still reaches it, so its
    # "No RCSB Metadata" status gives way to the citation lookup's.
    "6PQR": {"pident": 70.0, "pmid": None, "rcsb_down": True},
    "7STU": {"pident": 65.0, "pmid": "77777777", "pmc": None},  # PubMed only, not in PMC
    "8VWX": {"pident": 60.0, "pmid": "77777777", "pmc": None},  # the same PubMed-only paper
    "9YZA": {"pident": 55.0, "citation_down": True},  # Citation Lookup Failed
}


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise grounding_tool.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _requests_get(url, params=None, timeout=None, **kwargs):
    """UniProt search and RCSB's citation record for a PDB entry."""
    if "uniprotkb/search" in url:
        return _Response(
            {
                "results": [
                    {
                        "primaryAccession": "P9WQA3",
                        "organism": {"scientificName": "Mycobacterium tuberculosis"},
                        "comments": [
                            {
                                "commentType": "SUBCELLULAR LOCATION",
                                "subcellularLocations": [{"location": {"value": "Cytoplasm"}}],
                            }
                        ],
                    }
                ]
            }
        )
    hit = HITS[url.rstrip("/").split("/")[-1]]
    if "/core/entry/" in url:
        # The entry's primary citation: title, journal and year (#47).
        return _Response(
            {
                "rcsb_primary_citation": {
                    "title": f"Paper {hit['pmid']}",
                    "rcsb_journal_abbrev": "J. Test",
                    "year": 2020,
                }
            }
        )
    if hit.get("citation_down"):
        return _Response({}, status=503)
    if hit["pmid"] is None:
        return _Response({"status": 404, "message": "No data found"}, status=404)
    record = {
        "rcsb_id": hit["pmid"],
        "rcsb_pubmed_container_identifiers": {"pubmed_id": int(hit["pmid"])},
        "rcsb_pubmed_doi": f"10.1000/{hit['pmid']}",
        "rcsb_pubmed_abstract_text": f"Abstract of PMID {hit['pmid']}.",
    }
    if hit["pmc"]:
        record["rcsb_pubmed_central_id"] = hit["pmc"]
    return _Response(record)


class _SimilaritySession:
    """RCSB's PDB -> UniProt mapping for the ranking step."""

    def get(self, url, timeout=None, **kwargs):
        pdb_id = url.rstrip("/").split("/")[-2]
        if HITS[pdb_id].get("rcsb_down"):
            raise protein_similarity_tool.requests.exceptions.RequestException("reset")
        return _Response(
            [
                {
                    "rcsb_uniprot_container_identifiers": {"uniprot_id": f"UP{pdb_id}"},
                    "rcsb_uniprot_protein": {
                        "source_organism": {
                            "scientific_name": "Mycobacterium smegmatis",
                            "taxonomy_id": 1772,
                        }
                    },
                }
            ]
        )


class _Extraction:
    def run(self, methods, protein_name):
        return f"EXTRACTED[{protein_name}]: {methods[:60]}"


class _Protocol:
    def find_protocol(self, text):
        return [
            {
                "purification_step": "Ni-NTA Affinity Chromatography - Elution",
                "buffer_name": "Elution buffer",
                "buffer_composition": "50 mM Tris",
                "ph": 8.0,
                "salt_type": "NaCl",
                "buffer_supplement": "250 mM imidazole",
                "step_number": 1,
            }
        ]


class _Synthesis:
    def run(self, purifications, failed_purification=None, target_metadata=None):
        sources = [p.article_title for p in purifications]
        failed = failed_purification.article_title if failed_purification else None
        return f"## Step 0\nFrom {sources}; failed: {failed}", "raw plan"


@pytest.fixture
def pipeline(monkeypatch, pmc_article):
    monkeypatch.setenv("BLAST_DB_PATH", "/db/pdbaa/pdbaa")
    for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.setattr(protein_similarity_tool, name, None)

    def blastp(fasta, qcov, pident, evalue, max_hits, db_path, extra_args=None):
        return [
            Hit(
                protein_name=f"pdb|{pdb_id}|A Chain A, Aldolase",
                pdb_id=pdb_id,
                length=350,
                e_value=1e-50,
                pident=hit["pident"],
                query_coverage=95.0,
                query_start=1,
                query_end=15,
                subject_start=1,
                subject_end=15,
            )
            for pdb_id, hit in HITS.items()
        ]

    def fetch_pmc_xml(pmc_id):
        if pmc_id == "PMC-FETCH-FAILS":
            raise grounding_tool.requests.ConnectionError("connection reset")
        if pmc_id == "PMC-CLOSED":
            return "<error>The publisher does not allow downloading</error>"
        return pmc_article

    monkeypatch.setattr(agent_body, "run_blastp", blastp)
    monkeypatch.setattr(grounding_tool.requests, "get", _requests_get)
    monkeypatch.setattr(grounding_tool, "fetch_pmc_xml", fetch_pmc_xml)
    monkeypatch.setattr(grounding_tool.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        protein_similarity_tool, "create_retry_session", lambda: _SimilaritySession()
    )
    monkeypatch.setattr(agent_body, "ExtractionAgent", _Extraction)
    monkeypatch.setattr(agent_body, "ProtocolAgent", _Protocol)
    monkeypatch.setattr(agent_body, "SuggestedProtocolAgent", _Synthesis)


def run_job() -> tuple[dict, list[str]]:
    repo = InMemoryJobRepository()
    request = PurificationRequest(
        fasta_id=FASTA,
        failed_purification_text="Lysed in 50 mM Tris pH 8; the protein precipitated on Ni-NTA.",
        min_percent_identity=40,
        min_query_coverage=40,
        max_evalue=1e-3,
        max_hits=len(HITS),
    )
    repo.create("job", request.model_dump(mode="json"), Owner("sub", "user"), None, None)
    main.run_agent_task(repo, "job", request)
    job = repo.get("job")
    assert job.state == "completed", job.error
    return job.result, [entry.message for entry in repo.history("job")]


def test_the_pipeline_stores_the_report_in_the_snapshot(pipeline):
    result, progress = run_job()
    actual = {"progress": progress, "result": result}

    if os.getenv("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")

    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert actual == expected


class _Recording(_Extraction):
    """Records what the extraction agent is sent; answers only for text containing `finds`."""

    sent: list = []
    finds: str | None = None

    def run(self, text, protein_name):
        self.sent.append(text)
        if self.finds is not None and self.finds in text:
            return f"EXTRACTED: {self.finds}"
        return None


@pytest.fixture
def recording(monkeypatch):
    monkeypatch.setattr(_Recording, "sent", [])
    monkeypatch.setattr(_Recording, "finds", None)
    monkeypatch.setattr(agent_body, "ExtractionAgent", _Recording)
    return _Recording


def _paper(result, pmid):
    return next(paper for paper in result["papers"] if paper["pmid"] == pmid)


def test_only_the_methods_sections_are_sent(pipeline, recording):
    """There is no whole-article fallback: text outside the methods is never read."""
    recording.finds = "The structure was solved"  # only in Results

    result, _ = run_job()

    [methods] = recording.sent
    assert "The structure was solved" not in methods
    assert [p["source"] for p in result["purifications"]] == ["user"]
    assert result["blast_results"][0]["status"] == "No Protocol Found in Paper"


def test_the_paper_records_that_its_methods_sections_held_the_protocol(pipeline, recording):
    recording.finds = "50 mM Tris pH 8.0"  # in the fixture's methods

    result, _ = run_job()

    assert _paper(result, "11111111")["methods_source"] == "sections"


def test_nothing_is_tabulated_when_the_methods_hold_no_purification_text(pipeline, recording):
    """The raw methods text no longer goes to the table agent (#29)."""
    result, _ = run_job()

    assert len(recording.sent) == 1
    assert [p["source"] for p in result["purifications"]] == ["user"]
    assert result["blast_results"][0]["status"] == "No Protocol Found in Paper"
    assert _paper(result, "11111111")["methods_source"] is None


def test_synthesis_is_skipped_when_the_literature_yields_no_protocol(
    pipeline, recording, monkeypatch
):
    """
    #89: the step derives the protocol from the differences between the failed
    attempt and the successful ones, so with none of the latter it must not run.
    Running it on metadata alone is not the method the paper describes.
    """
    called = False

    class _Unexpected(_Synthesis):
        def run(self, *args, **kwargs):
            nonlocal called
            called = True
            return super().run(*args, **kwargs)

    monkeypatch.setattr(agent_body, "SuggestedProtocolAgent", _Unexpected)

    result, progress = run_job()

    assert not called
    assert result["comprehensive_protocol"] is None
    assert result["raw_plan"] is None
    assert result["synthesis_skipped"] == agent_body.NO_SOURCE_PROTOCOLS
    # A skip is not a failure: the expensive part of the run is still reported.
    assert result["error_message"] is None
    assert result["blast_results"]
    assert progress[-1] == "No source protocols found. Skipping synthesis..."


def test_synthesis_runs_when_one_source_protocol_was_found(pipeline):
    """The guard is on the literature protocols only, not on the whole report."""
    result, _ = run_job()

    assert result["synthesis_skipped"] is None
    assert result["comprehensive_protocol"]


def test_the_methods_text_is_capped_before_the_llm(pipeline, recording, monkeypatch):
    """A job's token cost stays bounded however long an article runs (#26)."""
    monkeypatch.setattr(agent_body, "MAX_METHODS_CHARS", 100)

    run_job()

    [methods] = recording.sent
    assert 0 < len(methods) <= 100


def test_the_search_stops_at_the_requested_number_of_protocols(pipeline, monkeypatch):
    """max_protocols comes from the request; past it, hits get no full text or LLM calls."""
    fetched = []
    fetch = grounding_tool.fetch_pmc_xml

    def counting(pmc_id):
        fetched.append(pmc_id)
        return fetch(pmc_id)

    monkeypatch.setattr(grounding_tool, "fetch_pmc_xml", counting)
    repo = InMemoryJobRepository()
    request = PurificationRequest(fasta_id=FASTA, max_hits=len(HITS), max_protocols=1)
    repo.create("job", request.model_dump(mode="json"), Owner("sub", "user"), None, None)

    main.run_agent_task(repo, "job", request)

    assert fetched == ["PMC1234567"]
    assert [p["pdb_id"] for p in repo.get("job").result["purifications"]] == ["1ABC"]


def test_the_cap_cuts_at_the_last_line_break():
    text = "first paragraph\nsecond paragraph\nthird"

    assert agent_body._capped(text, 30, "Text", "1ABC") == "first paragraph"
    assert agent_body._capped(text, 100, "Text", "1ABC") == text
    # One line longer than the cap is cut mid-line rather than dropped.
    assert agent_body._capped("x" * 50, 20, "Text", "1ABC") == "x" * 20


def test_a_paper_whose_fetch_failed_is_fetched_again_for_a_later_hit(pipeline, monkeypatch):
    """A failed fetch says nothing about the paper, so it isn't "Paper Already Found"."""
    fetches = []
    fetch = grounding_tool.fetch_pmc_xml

    def flaky(pmc_id):
        fetches.append(pmc_id)
        if pmc_id == "PMC1234567" and fetches.count(pmc_id) <= 3:  # every retry for the first hit
            raise urllib.error.HTTPError("url", 503, "Service Unavailable", None, None)
        return fetch(pmc_id)

    monkeypatch.setattr(grounding_tool, "fetch_pmc_xml", flaky)

    result, _ = run_job()

    statuses = {hit["pdb_id"]: hit["status"] for hit in result["blast_results"]}
    assert statuses["1ABC"] == "PMC Retrieval Failed"
    assert statuses["3GHI"] == "Protocol Found"  # the same paper, read for this hit
    assert _paper(result, "11111111")["access"] == "open"
    assert [p["pdb_id"] for p in result["purifications"]] == [None, "3GHI"]


def test_a_rate_limited_fetch_is_a_retrieval_failure(pipeline, monkeypatch):
    """NCBI's 429 is a failed fetch, not an error in the paper's processing."""

    def rate_limited(pmc_id):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", None, None)

    monkeypatch.setattr(grounding_tool, "fetch_pmc_xml", rate_limited)

    result, _ = run_job()

    hit = next(h for h in result["blast_results"] if h["pdb_id"] == "1ABC")
    assert hit["status"] == "PMC Retrieval Failed"
    assert _paper(result, "11111111")["access"] == "fetch_failed"


def test_hits_past_the_protocol_stop_are_resolved_from_rcsb_alone(pipeline, monkeypatch):
    """#47: the manual-review list and #20's funnel cover every hit, with no more full text."""
    fetched = []
    efetch = agent_body.GroundingTool.search_pmc

    def search_pmc(self, pmc_id):
        fetched.append(pmc_id)
        return efetch(self, pmc_id)

    monkeypatch.setattr(agent_body.GroundingTool, "search_pmc", search_pmc)

    result = agent_body.ProteinPurificationAgent().run(
        protein_name=FASTA,
        min_pident=40,
        min_qcov=40,
        max_evalue=1e-3,
        max_hits=len(HITS),
        max_protocols=1,
    )

    # Only the first hit's article is fetched; the stop comes right after it.
    assert fetched == ["PMC1234567"]
    assert {hit.pdb_id: hit.status.value for hit in result.similar_proteins} == {
        "1ABC": "Protocol Found",
        "2DEF": "No PMC Primary Citation",  # no publication
        "3GHI": "Paper Already Found",
        "4JKL": "Not Analyzed",  # in PMC: only the full text says whether it is open
        "5MNO": "Not Analyzed",
        "6PQR": "No PMC Primary Citation",
        "7STU": "No PMC Primary Citation",  # PubMed only
        "8VWX": "Paper Already Found",
        "9YZA": "Citation Lookup Failed",
    }
    listed = [paper for paper in result.papers if paper.for_manual_review]
    assert [(p.pmid, p.title, p.journal, p.year) for p in listed] == [
        ("77777777", "Paper 77777777", "J. Test", 2020)
    ]


def test_the_planner_gets_the_targets_computed_properties(pipeline, monkeypatch):
    """The computed pI and MW reach synthesis alongside the UniProt metadata (#81)."""
    received = []

    class _Recording(_Synthesis):
        def run(self, purifications, failed_purification=None, target_metadata=None):
            received.append(target_metadata)
            return super().run(purifications, failed_purification, target_metadata)

    monkeypatch.setattr(agent_body, "SuggestedProtocolAgent", _Recording)

    run_job()

    [metadata] = received
    assert metadata["organism"] == "Mycobacterium tuberculosis"
    assert metadata["theoretical_pi"] == 8.5
    assert metadata["molecular_weight_da"] == 1586
