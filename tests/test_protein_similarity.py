"""
Tests for ProteinSimilarityTool.

RCSB is replaced with a stub session. Neo4j is left unconfigured, which is the
default path for every non-SSGCID input since only the CTTdb branch supplies a
target taxonomy ID.
"""

import pytest
import requests

from agent_engine.agent_tools.protein_similarity_tool import ProteinSimilarityTool
from agent_engine.models import Hit, HitStatus

HEALTHY = [
    {
        "rcsb_uniprot_container_identifiers": {"uniprot_id": "P00001"},
        "rcsb_uniprot_protein": {
            "source_organism": {
                "scientific_name": "Mycobacterium tuberculosis",
                "taxonomy_id": 1773,
            }
        },
    }
]
NO_PROTEIN_BLOCK = [{"rcsb_uniprot_container_identifiers": {"uniprot_id": "P00002"}}]
NULL_SOURCE_ORGANISM = [{"rcsb_uniprot_protein": {"source_organism": None}}]
NULL_RECORD = [None]
NO_CONTAINER_IDS = [
    {
        "rcsb_uniprot_protein": {
            "source_organism": {"scientific_name": "Escherichia coli", "taxonomy_id": 562}
        }
    }
]


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    """Maps PDB id -> payload; the sentinel "BOOM" raises instead."""

    def __init__(self, payloads):
        self.payloads = payloads

    def get(self, url, timeout=None, **kwargs):
        pdb_id = url.rstrip("/").split("/")[-2]
        payload = self.payloads[pdb_id]
        if payload == "BOOM":
            raise requests.exceptions.RequestException("connection reset")
        return _Response(payload)


def make_tool(payloads):
    tool = ProteinSimilarityTool.__new__(ProteinSimilarityTool)
    tool.driver = None  # Neo4j unconfigured
    tool.http_session = _Session(payloads)
    return tool


def hit(pdb_id, pident=90.0):
    return Hit(
        protein_name=f"pdb|{pdb_id}|A",
        pdb_id=pdb_id,
        length=100,
        e_value=1e-30,
        pident=pident,
        query_coverage=95.0,
        query_start=1,
        query_end=100,
        subject_start=1,
        subject_end=100,
    )


def hits(*pdb_ids, pident=90.0):
    return [hit(p, pident) for p in pdb_ids]


# --- Regression: issue #5 ----------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [NO_PROTEIN_BLOCK, NULL_SOURCE_ORGANISM, NULL_RECORD],
    ids=["no_rcsb_uniprot_protein", "null_source_organism", "null_record"],
)
def test_hits_without_organism_metadata_do_not_crash_the_sort(payload):
    """
    Previously `similarity_score` was only assigned inside the
    `if source_organism:` branch while the hit was appended unconditionally,
    so the closing sorted() raised KeyError.
    """
    tool = make_tool({"1ABC": payload})

    results = tool.calculate_similarity("1773", hits("1ABC"))

    assert len(results) == 1
    assert results[0].similarity_score == pytest.approx(0.9)
    assert results[0].status == "No RCSB Metadata"


def test_every_hit_is_retained_and_scored():
    payloads = {
        "1ABC": HEALTHY,
        "2DEF": NO_PROTEIN_BLOCK,
        "3GHI": NULL_RECORD,
        "4JKL": "BOOM",
        "5MNO": NO_CONTAINER_IDS,
    }
    tool = make_tool(payloads)

    results = tool.calculate_similarity("1773", hits(*payloads))

    assert len(results) == len(payloads)
    assert all(r.similarity_score is not None for r in results)


def test_http_failure_falls_back_to_identity_score():
    tool = make_tool({"1ABC": "BOOM"})

    result = tool.calculate_similarity("1773", hits("1ABC", pident=72.0))[0]

    assert result.similarity_score == pytest.approx(0.72)
    assert result.status == "No RCSB Metadata"


# --- Metadata and ordering ---------------------------------------------------


def test_organism_metadata_is_attached():
    tool = make_tool({"1ABC": HEALTHY})

    result = tool.calculate_similarity("1773", hits("1ABC"))[0]

    assert result.organism_name == "Mycobacterium tuberculosis"
    assert result.taxonomy_id == 1773
    assert result.uniprot_id == "P00001"
    assert result.status == HitStatus.NOT_ANALYZED  # healthy hits are not marked


def test_results_sorted_by_score_descending():
    tool = make_tool({"1ABC": HEALTHY, "2DEF": HEALTHY, "3GHI": HEALTHY})
    raw = [hit("1ABC", 40.0), hit("2DEF", 95.0), hit("3GHI", 70.0)]

    results = tool.calculate_similarity("1773", raw)

    assert [r.pdb_id for r in results] == ["2DEF", "3GHI", "1ABC"]


def test_scoring_is_identity_only_without_neo4j():
    """The blended score needs a driver; without one it is pident alone."""
    tool = make_tool({"1ABC": HEALTHY})

    result = tool.calculate_similarity("1773", hits("1ABC", pident=88.0))[0]

    assert result.similarity_score == pytest.approx(0.88)


def test_missing_uniprot_id_does_not_crash_scoring():
    """source_organism present but no container identifiers to read an id from."""
    tool = make_tool({"1ABC": NO_CONTAINER_IDS})

    result = tool.calculate_similarity("1773", hits("1ABC"))[0]

    assert result.organism_name == "Escherichia coli"
    assert result.uniprot_id is None


# --- Taxonomic scoring math --------------------------------------------------


def test_normalized_score_penalizes_higher_ranks():
    tool = ProteinSimilarityTool.__new__(ProteinSimilarityTool)

    class _Record:
        def __init__(self, ranks):
            self._ranks = ranks

        def data(self):
            return {"result": [{"rank": r} for r in self._ranks]}

    species = tool._calculate_normalized_score([_Record(["species"])], None, 0)
    deep = tool._calculate_normalized_score([_Record(["species", "genus", "family"])], None, 0)

    assert species > deep, "a closer taxonomic path must score higher"
    assert 0 < deep < 1


def test_normalized_score_halves_a_perfect_match():
    """A zero-weight path would score 1.0, which is halved to avoid a self-match."""
    tool = ProteinSimilarityTool.__new__(ProteinSimilarityTool)

    class _Empty:
        def data(self):
            return {"result": []}

    assert tool._calculate_normalized_score([_Empty()], None, 0) == pytest.approx(0.5)
