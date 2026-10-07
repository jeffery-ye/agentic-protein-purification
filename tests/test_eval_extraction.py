"""The sampling logic of scripts/eval_extraction.py (#20), with RCSB and PMC faked."""

import importlib.util
import random
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_extraction.py"
spec = importlib.util.spec_from_file_location("eval_extraction", SCRIPT)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

# The draw resolves each entity's description from RCSB; the tests stub it out.
ev.entity_description = lambda entity_id: f"protein {entity_id}"


def test_allocation_matches_the_plan():
    targets = {"Bacteria": 14_532, "Eukaryota": 4_586, "Viruses": 645}
    assert ev.allocate(targets, 50) == {"Bacteria": 37, "Eukaryota": 11, "Viruses": 2}


def test_strain_rows_merge_into_their_species():
    rows = [
        {"species": "M. tuberculosis", "taxonomy_id": "83332", "superkingdom": "Bacteria", "targets": "596"},
        {"species": "M. tuberculosis CDC1551", "taxonomy_id": "83331", "superkingdom": "Bacteria", "targets": "10"},
        {"species": "B. burgdorferi", "taxonomy_id": "224326", "superkingdom": "Bacteria", "targets": "613"},
    ]  # fmt: skip
    resolved = {"83332": ("83332", "1773"), "83331": ("83331", "1773"), "224326": ("224326", "139")}
    organisms = {o.species_taxid: o for o in ev.organisms_from_rows(rows, resolved)}
    assert organisms["1773"].name == "M. tuberculosis"
    assert organisms["1773"].targets == 606
    assert organisms["1773"].row_taxids == ["83331", "83332"]


def _organism(name, superkingdom, taxid, targets=1):
    return ev.Organism(name, superkingdom, taxid, targets)


def test_draw_rejects_closed_and_duplicate_papers_and_redraws_empty_organisms():
    organisms = [
        _organism("Open bug", "Bacteria", "1", targets=1),
        _organism("Closed bug", "Bacteria", "2", targets=1000),
        _organism("Yeast", "Eukaryota", "3"),
        _organism("Virus", "Viruses", "4"),
    ]
    entities = {
        "1": ["1AAA_1", "1AAB_1", "1AAB_2"],
        "2": ["2AAA_1"],
        "3": ["3AAA_1"],
        "4": ["4AAA_1"],
    }
    papers = {
        "1AAA": ("P1", "PMC1"),
        "1AAB": ("P1", "PMC1"),  # same paper as 1AAA
        "2AAA": ("P2", None),  # PubMed only
        "3AAA": ("P3", "PMC3"),
        "4AAA": ("P4", "PMC4"),
    }

    def check(pdb_id):
        pmid, pmcid = papers[pdb_id]
        ids = {"pmid": pmid, "pmcid": pmcid}
        if not pmcid:
            return None, "PubMed only", ids
        return {**ids, "xml": "<article/>"}, None, ids

    log = []
    sample = ev.draw_sample(
        organisms,
        {"Bacteria": 1, "Eukaryota": 1, "Viruses": 1},
        random.Random(0),
        lambda organism: entities[organism.species_taxid],
        check,
        lambda **row: log.append(row),
    )

    assert [p["superkingdom"] for p in sample] == ["Bacteria", "Eukaryota", "Viruses"]
    assert len({p["pmid"] for p in sample}) == 3
    assert sample[0]["entity_id"].split("_")[0] == sample[0]["pdb_id"]
    reasons = [row.get("reason") for row in log]
    assert "PubMed only" in reasons
    assert "no qualifying entry left" in reasons


def test_draw_logs_a_stratum_that_cannot_fill():
    organisms = [_organism("Virus", "Viruses", "4")]
    log = []
    sample = ev.draw_sample(
        organisms,
        {"Bacteria": 0, "Eukaryota": 0, "Viruses": 2},
        random.Random(0),
        lambda organism: ["4AAA_1"],
        lambda pdb_id: ({"pmid": "P4", "pmcid": "PMC4"}, None, {"pmid": "P4", "pmcid": "PMC4"}),
        lambda **row: log.append(row),
    )
    assert len(sample) == 1
    assert log[-1]["outcome"] == "stratum exhausted"


def test_replacements_continue_the_draw_without_repeating_an_entry_or_paper():
    organisms = [_organism("Bug", "Bacteria", "1")]
    entries = [f"{i}AAA_1" for i in range(1, 7)]
    papers = {e.split("_")[0]: f"P{i % 4}" for i, e in enumerate(entries)}  # some share a paper

    def check(pdb_id):
        ids = {"pmid": papers[pdb_id], "pmcid": "PMC" + papers[pdb_id]}
        return dict(ids), None, ids

    sampler = ev.Sampler(organisms, lambda o: entries, check, lambda **row: None)
    first = ev.initial_draw(
        sampler, {"Bacteria": 2, "Eukaryota": 0, "Viruses": 0}, random.Random(0)
    )
    rng = ev.replacement_rng(0, "Bacteria")
    more = [sampler.draw("Bacteria", rng) for _ in range(3)]

    drawn = first + [p for p in more if p]
    assert len(drawn) == 4  # four distinct papers exist
    assert len({p["pdb_id"] for p in drawn}) == len({p["pmid"] for p in drawn}) == 4
    assert more[-1] is None


def test_replacement_order_is_fixed_by_the_seed():
    a, b = ev.replacement_rng(20, "Viruses"), ev.replacement_rng(20, "Viruses")
    assert [a.random() for _ in range(3)] == [b.random() for _ in range(3)]


def test_protocol_table_gives_each_step_a_row_beside_the_merged_paper():
    from openpyxl import Workbook

    step = {
        "step_number": 1,
        "purification_step": "Ni-NTA - Elution",
        "buffer_name": "Buffer B",
        "buffer_composition": "20 mM Tris",
        "ph": 8.0,
        "salt_type": "300 mM NaCl",
        "buffer_supplement": "250 mM imidazole",
    }
    steps = [step, {**step, "step_number": 2, "purification_step": "SEC", "ph": None}]
    wb = Workbook()
    sheet = ev.add_table(
        wb, "Ratings", [("PDB ID", 8), ("Text", 70)] + [(n, w) for n, w, _ in ev.STEP_COLUMNS]
        + [("Rater", 14)],
    )  # fmt: skip
    assert ev.add_rated_paper(sheet, 2, ["1AAA", "Purified on Ni-NTA."], steps, False) == 3

    row = lambda r: [sheet.cell(r, c).value for c in range(1, sheet.max_column + 1)]  # noqa: E731
    assert row(2) == ["1AAA", "Purified on Ni-NTA.", 1, "Ni-NTA - Elution", "Buffer B",
                      "20 mM Tris", 8.0, "300 mM NaCl", "250 mM imidazole", None]  # fmt: skip
    assert row(3)[2:5] == [2, "SEC", "Buffer B"] and row(3)[6] is None
    assert {str(m) for m in sheet.merged_cells.ranges} == {"A2:A3", "B2:B3", "J2:J3"}


def test_gate_rejects_a_paper_with_no_protocol_and_the_draw_moves_on():
    """A gated-out paper is logged with its reason and never drawn again."""
    organisms = [_organism("Bug", "Bacteria", "1")]
    entities = {"1": ["1AAA_1", "1AAB_1", "1AAC_1"]}
    papers = {"1AAA": "P1", "1AAB": "P2", "1AAC": "P3"}
    # Only the third paper carries a protocol.
    gated = {"PMCP1": (False, "deferred to a citation"), "PMCP2": (False, "no compositions"),
             "PMCP3": (True, "compositions for two steps")}  # fmt: skip

    def check(pdb_id):
        ids = {"pmid": papers[pdb_id], "pmcid": "PMC" + papers[pdb_id]}
        return dict(ids), None, ids

    log = []
    sampler = ev.Sampler(
        organisms,
        lambda organism: entities[organism.species_taxid],
        check,
        lambda **row: log.append(row),
        gate=lambda pmcid, protein_name: gated[pmcid],
    )
    drawn = sampler.draw("Bacteria", random.Random(0))

    assert drawn["pmcid"] == "PMCP3"
    assert drawn["gate_reason"] == "compositions for two steps"
    # Whichever gated papers came up first, each is logged with its own reason.
    rejected = [r for r in log if r["outcome"] == "rejected"]
    assert rejected
    assert all(r["reason"].startswith("no protocol in paper: ") for r in rejected)
    assert {r["pmcid"] for r in rejected} <= {"PMCP1", "PMCP2"}
    # A rejected paper is spent too, so a later draw cannot return it.
    assert "P3" in sampler.pmids
