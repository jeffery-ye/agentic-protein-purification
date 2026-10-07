"""
Expert evaluation of protocol extraction: sample papers, extract their protocols,
and write the rater workbook.

Three steps, each writing to data/eval-extraction/ (gitignored):

    uv run python scripts/eval_extraction.py sample    # draw 50 papers with a protocol
    uv run python scripts/eval_extraction.py extract   # parser and agents; replace failures
    uv run python scripts/eval_extraction.py sheet     # the rater workbook

`sample` draws each paper, then asks `ProtocolGate` whether the paper carries
a purification protocol at all; one that defers its protocol to a citation is
rejected and redrawn, so it never counts against the extraction scores. The
gate reads the methods text only and never sees the extraction output, so a
paper the tool later handles badly stays in the sample. `--no-gate` skips it,
which is the dry run and makes no LLM calls.

`extract` runs the pipeline on each paper, then replaces each paper that
yielded no protocol table with the next draw from its superkingdom until 50
papers have one. It resumes, skipping papers already done, and retries any
that failed.
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORGANISMS_CSV = ROOT / "docs" / "research" / "data" / "ssgcid-organisms.csv"
OUT = ROOT / "data" / "eval-extraction"
ARTICLES = OUT / "articles"
SAMPLE_JSON = OUT / "sample.json"
LOG_CSV = OUT / "sampling-log.csv"
ENTITIES_JSON = OUT / "rcsb-entities.json"
CHECKS_JSON = OUT / "entry-checks.json"
GATE_JSON = OUT / "protocol-gate.json"
RESULTS_JSON = OUT / "results.json"
SHEET_XLSX = OUT / "extraction-ratings.xlsx"

PAPERS = 50
SUPERKINGDOMS = ("Bacteria", "Eukaryota", "Viruses")
RELEASED_FROM, RELEASED_TO = "2008-01-01", "2024-12-31"
DEFAULT_SEED = 20

NCBI_TAXONOMY_URL = "https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/dataset_report"
RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_ENTITY_URL = "https://data.rcsb.org/rest/v1/core/polymer_entity/"
PMC_ARTICLE_URL = "https://pmc.ncbi.nlm.nih.gov/articles/"

# Excel's limit on the characters in one cell.
CELL_LIMIT = 32_767


# --- Sampling -----------------------------------------------------------------


@dataclass
class Organism:
    """One species on SSGCID's list: its CSV rows summed, keyed by NCBI species."""

    name: str
    superkingdom: str
    species_taxid: str
    targets: int
    row_taxids: list[str] = field(default_factory=list)


def allocate(targets: dict[str, int], total: int) -> dict[str, int]:
    """Seats in proportion to `targets`, by largest remainder, summing to `total`."""
    whole = sum(targets.values())
    shares = {k: total * v / whole for k, v in targets.items()}
    seats = {k: int(s) for k, s in shares.items()}
    by_remainder = sorted(shares, key=lambda k: shares[k] - seats[k], reverse=True)
    for k in by_remainder[: total - sum(seats.values())]:
        seats[k] += 1
    return seats


def read_organism_rows() -> list[dict]:
    with open(ORGANISMS_CSV, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ncbi_taxa(queries: list[str]) -> dict[str, tuple[str, str | None]]:
    """
    Each taxonomy ID or name NCBI knows, as (its taxonomy ID, its species' ID),
    from NCBI Datasets (Entrez's taxonomy efetch is too slow for a few hundred
    IDs). The species is None above species rank.
    """
    taxa = {}
    for start in range(0, len(queries), 100):
        batch = queries[start : start + 100]
        response = requests.post(NCBI_TAXONOMY_URL, json={"taxons": batch}, timeout=120)
        response.raise_for_status()
        for report in response.json().get("reports", []):
            taxonomy = report.get("taxonomy")
            if not taxonomy:
                continue
            species = (taxonomy.get("classification", {}).get("species") or {}).get("id")
            if species is None and (taxonomy.get("rank") or "").upper() == "SPECIES":
                species = taxonomy["tax_id"]
            for query in report.get("query", []):
                taxa[str(query)] = (str(taxonomy["tax_id"]), str(species) if species else None)
    return taxa


def resolve_rows(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """
    Each row's (taxonomy ID to query RCSB with, species ID), keyed by the
    export's taxonomy ID. The export's IDs are mostly strains (83332 is
    M. tuberculosis H37Rv). Some are placeholders NCBI doesn't know (99999xx,
    for viral subtypes); those rows are resolved by name, the export's
    "genus name" form trimmed a word at a time from the front, as in
    "Influenzavirus A Influenza A virus". A row resolved by neither, such as
    "unclassified Caudoviricetes unclassified", is left out.
    """
    by_id = ncbi_taxa(sorted({r["taxonomy_id"] for r in rows}))
    resolved = {}
    for taxid, (taxon, species) in by_id.items():
        resolved[taxid] = (taxon, species or taxon)
    unknown = [r for r in rows if r["taxonomy_id"] not in resolved]
    names = sorted({r["species"] for r in unknown})
    trimmed = {n: [" ".join(n.split()[i:]) for i in range(len(n.split()))] for n in names}
    by_name = ncbi_taxa(sorted({t for ts in trimmed.values() for t in ts})) if names else {}
    for row in unknown:
        for name in trimmed[row["species"]]:
            taxon, species = by_name.get(name, (None, None))
            if species:
                resolved[row["taxonomy_id"]] = (taxon, species)
                break
    return resolved


def organisms_from_rows(rows: list[dict], resolved: dict[str, tuple[str, str]]) -> list[Organism]:
    """Rows grouped by NCBI species, named after the row with the most targets."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["taxonomy_id"] in resolved:
            groups[resolved[row["taxonomy_id"]][1]].append(row)
    organisms = []
    for species, members in sorted(groups.items()):
        lead = max(members, key=lambda r: int(r["targets"]))
        organisms.append(
            Organism(
                name=lead["species"],
                superkingdom=lead["superkingdom"],
                species_taxid=species,
                targets=sum(int(r["targets"]) for r in members),
                row_taxids=sorted({resolved[r["taxonomy_id"]][0] for r in members}),
            )
        )
    return organisms


def rcsb_protein_entities(taxids: list[str]) -> list[str]:
    """
    Protein entities in entries released in the window whose source lineage
    holds any of `taxids`, as sorted "1ABC_1" IDs. Given the species and the
    export's own IDs, this finds every strain even where RCSB's taxonomy
    predates NCBI's renaming of a species (SARS-CoV-2 is now 3418604).
    """
    query = {
        "query": {
            "type": "group",
            "logical_operator": "and",
            "nodes": [
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": "rcsb_entity_source_organism.taxonomy_lineage.id",
                        "operator": "in",
                        "value": taxids,
                    },
                },
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": "entity_poly.rcsb_entity_polymer_type",
                        "operator": "exact_match",
                        "value": "Protein",
                    },
                },
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": "rcsb_accession_info.initial_release_date",
                        "operator": "range",
                        "value": {
                            "from": RELEASED_FROM,
                            "to": RELEASED_TO,
                            "include_lower": True,
                            "include_upper": True,
                        },
                    },
                },
            ],
        },
        "return_type": "polymer_entity",
        "request_options": {"return_all_hits": True, "results_verbosity": "compact"},
    }
    response = requests.post(RCSB_SEARCH_URL, json=query, timeout=60)
    if response.status_code == 204:  # no hits
        return []
    response.raise_for_status()
    return sorted(response.json().get("result_set", []))


def entity_description(entity_id: str) -> str | None:
    entry, entity = entity_id.split("_")
    response = requests.get(f"{RCSB_ENTITY_URL}{entry}/{entity}", timeout=10)
    response.raise_for_status()
    return (response.json().get("rcsb_polymer_entity") or {}).get("pdbx_description")


def check_entry(pdb_id: str) -> tuple[dict | None, str | None, dict]:
    """
    Whether an entry's primary citation is open access in PMC, the same way
    the pipeline decides it. Returns (open paper, None, ids) or (None,
    rejection reason, ids). An open paper's article is saved to ARTICLES.
    """
    from agent_engine.agent_tools.grounding_tool import GroundingTool
    from agent_engine.models import HitStatus

    tool = GroundingTool()
    paper, status = tool.lookup_citation(pdb_id)
    if paper is None:
        reason = (
            "citation lookup failed"
            if status == HitStatus.CITATION_LOOKUP_FAILED
            else "no PubMed citation"
        )
        return None, reason, {}
    ids = {"pmid": paper.pmid, "pmcid": paper.pmcid}
    if not paper.pmcid:
        return None, "PubMed only", ids
    xml, fetch_status = tool.search_pmc(paper.pmcid)
    if not xml:
        reason = (
            "fetch failure" if fetch_status == HitStatus.PMC_RETRIEVAL_FAILED else "PMC restricted"
        )
        return None, reason, ids
    ARTICLES.mkdir(parents=True, exist_ok=True)
    (ARTICLES / f"{paper.pmcid}.xml").write_text(xml, encoding="utf-8")
    return {**ids, "doi": paper.doi}, None, ids


GATE_INSTRUCTIONS = """
You decide whether a paper's methods text contains a usable protein
purification protocol at all, so that it can be used to evaluate an
extraction tool. You are judging the SOURCE TEXT ONLY. Do not extract
anything, and do not judge how easy the text would be to process.

Answer `present = true` when the text states, for ANY protein the paper
purified, the composition of the buffers used in at least two distinct
purification steps (for example a lysis or binding buffer and an elution or
gel-filtration buffer). A composition means the buffering agent and its
concentration, such as "20 mM Tris pH 8.0, 150 mM NaCl".

Do NOT decide whether that protocol belongs to the protein named in the
prompt, whether the naming matches, or whether the protocol is for a complex
rather than a single protein. Working that out is the job of the tool being
evaluated, so a paper must not be rejected for being ambiguous about it. The
protein name is given only as context.

Answer `present = false` only when the text holds no such protocol, because:
- it defers the procedure to another publication or to supplementary material
  (for example "purified as described previously"), or
- it names the columns or steps but gives no buffer compositions for them, or
- the only composition given is a single storage, dialysis or crystallization
  buffer at the end.

Buffer compositions that belong to assays, crystallization screens or
spectroscopy do not count; only the purification itself.

`reason` is one short phrase saying which of the cases above applies.
"""


class ProtocolGate:
    """
    Whether a paper carries a purification protocol at all, asked of the
    methods text before any extraction. It gates the draw, so papers
    that defer their protocol to a citation are redrawn instead of counting
    against the extraction scores. It never sees the extraction output, so a
    paper the extractor later handles badly still stays in the sample.
    """

    def __init__(self):
        from pydantic import BaseModel, Field
        from pydantic_ai import Agent

        from agent_engine.llm import reasoning_model

        class GateDecision(BaseModel):
            present: bool = Field(description="Does the text hold a usable purification protocol?")
            reason: str = Field(description="One short phrase giving the deciding factor.")

        self.agent = Agent(
            model=reasoning_model, output_type=GateDecision, instructions=GATE_INSTRUCTIONS
        )

    def check(self, methods: str, protein_name: str):
        from agent_engine.trace import run_traced

        decision = run_traced(
            self.agent, "extraction", f"Protein name: {protein_name}\n\nText:\n{methods}"
        ).output
        return decision.present, decision.reason


def cached_gate():
    """
    `ProtocolGate` over a cached article, with its decisions kept in
    GATE_JSON so a re-run of the draw costs no LLM calls.
    """
    from agent_engine.agent_tools.methods_tool import MethodsTool

    cache = json.loads(GATE_JSON.read_text()) if GATE_JSON.exists() else {}
    gate = ProtocolGate()

    def check(pmcid: str, protein_name: str | None):
        if pmcid not in cache:
            xml = (ARTICLES / f"{pmcid}.xml").read_text(encoding="utf-8")
            methods = MethodsTool().parse_article(xml).get("methods")
            if not methods:
                decision = [False, "no methods section found"]
            else:
                present, reason = gate.check(
                    _capped_methods(methods, pmcid), protein_name or "the deposited protein"
                )
                decision = [present, reason]
            cache[pmcid] = decision
            GATE_JSON.write_text(json.dumps(cache, indent=1))
        return tuple(cache[pmcid])

    return check


def _capped_methods(methods: str, label: str) -> str:
    from agent_engine.agents.agent_body import MAX_METHODS_CHARS, _capped

    return _capped(methods, MAX_METHODS_CHARS, "Methods text", label)


def cached(check):
    """
    `check` with its answers kept in CHECKS_JSON. A fetch failure isn't kept:
    it says nothing about the paper, so a later run asks again.
    """
    cache = json.loads(CHECKS_JSON.read_text()) if CHECKS_JSON.exists() else {}

    def checked(pdb_id):
        if pdb_id not in cache:
            paper, reason, ids = check(pdb_id)
            if reason == "fetch failure":
                return paper, reason, ids
            cache[pdb_id] = [paper, reason, ids]
            CHECKS_JSON.write_text(json.dumps(cache, indent=1))
        return tuple(cache[pdb_id])

    return checked


class Sampler:
    """
    The two-stage draw. Per superkingdom: an organism weighted by its SSGCID
    targets, then random entries of it until one's paper is open in PMC and
    new to the sample. An organism with no such entry left is logged, dropped
    and redrawn. `log` receives every draw and rejection.

    It keeps what has been drawn, so replacements continue the same draw
    without repeating an entry or a paper.

    `gate(pmcid, methods, protein_name)` decides whether the paper carries a
    protocol at all; a paper without one is rejected here rather than counted
    as a bad extraction. Passing None skips it, which is the dry run.
    """

    def __init__(self, organisms, entities_for, check, log, gate=None):
        self.pools = {k: [o for o in organisms if o.superkingdom == k] for k in SUPERKINGDOMS}
        self.entities_for, self.check, self.log = entities_for, check, log
        self.gate = gate
        self.pmids: set[str] = set()
        self.entities_of: dict[tuple, list[str]] = {}  # (species, entry) -> its entities
        self.untried: dict[str, list[str]] = {}  # species -> its entries not yet drawn

    def draw(self, superkingdom: str, rng: random.Random) -> dict | None:
        """One accepted paper from the superkingdom, or None when it has none left."""
        pool = self.pools[superkingdom]
        while pool:
            organism = rng.choices(pool, weights=[o.targets for o in pool])[0]
            base = {
                "superkingdom": superkingdom,
                "organism": organism.name,
                "species_taxid": organism.species_taxid,
            }
            species = organism.species_taxid
            if species not in self.untried:
                entries = set()
                for entity_id in self.entities_for(organism):
                    entry = entity_id.split("_")[0]
                    self.entities_of.setdefault((species, entry), []).append(entity_id)
                    entries.add(entry)
                self.untried[species] = sorted(entries)
            remaining = self.untried[species]
            while remaining:
                pdb_id = remaining.pop(rng.randrange(len(remaining)))
                paper, reason, ids = self.check(pdb_id)
                if paper and paper["pmid"] in self.pmids:
                    paper, reason = None, "duplicate paper"
                if paper is None:
                    self.log(**base, pdb_id=pdb_id, **ids, outcome="rejected", reason=reason)
                    continue
                entity_id = rng.choice(sorted(self.entities_of[(species, pdb_id)]))
                protein_name = entity_description(entity_id)
                drawn = {**base, "pdb_id": pdb_id, "entity_id": entity_id,
                         "protein_name": protein_name, **paper}  # fmt: skip
                # A paper is spent either way: rejected here, it is not drawn
                # again through another of its entries.
                self.pmids.add(paper["pmid"])
                if self.gate is not None:
                    present, reason = self.gate(paper["pmcid"], protein_name)
                    if not present:
                        self.log(**base, pdb_id=pdb_id, entity_id=entity_id, **ids,
                                 outcome="rejected", reason=f"no protocol in paper: {reason}")  # fmt: skip
                        continue
                    drawn["gate_reason"] = reason
                self.log(**base, pdb_id=pdb_id, entity_id=entity_id, **ids, outcome="accepted")
                return drawn
            self.log(**base, outcome="organism redrawn", reason="no qualifying entry left")
            pool.remove(organism)
        self.log(superkingdom=superkingdom, outcome="stratum exhausted")
        return None


def initial_draw(sampler: Sampler, quotas: dict[str, int], rng: random.Random) -> list[dict]:
    """The first `quotas` papers, one superkingdom after another, on one generator."""
    sample = []
    for superkingdom in SUPERKINGDOMS:
        for _ in range(quotas[superkingdom]):
            paper = sampler.draw(superkingdom, rng)
            if paper is None:
                break
            sample.append(paper)
    return sample


def draw_sample(organisms, quotas, rng, entities_for, check, log):
    return initial_draw(Sampler(organisms, entities_for, check, log), quotas, rng)


def replacement_rng(seed: int, superkingdom: str) -> random.Random:
    """
    Replacements for papers with no extracted protocol come from a generator
    of their own per superkingdom, so their order doesn't depend on how many
    other superkingdoms needed.
    """
    return random.Random(f"{seed}-{superkingdom}-replacements")


LOG_COLUMNS = [
    "draw",
    "stage",
    "superkingdom",
    "organism",
    "species_taxid",
    "pdb_id",
    "entity_id",
    "pmid",
    "pmcid",
    "outcome",
    "reason",
]


class DrawLog:
    """Writes each draw to LOG_CSV as it happens, numbered on from the rows already there."""

    def __init__(self, stage: str, append: bool):
        self.stage = stage
        if not append or not LOG_CSV.exists():
            with open(LOG_CSV, "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=LOG_COLUMNS).writeheader()
        with open(LOG_CSV, encoding="utf-8", newline="") as f:
            self.first = 1 + sum(1 for _ in csv.DictReader(f))
        self.rows: list[dict] = []

    def __call__(self, **row):
        row.update(draw=self.first + len(self.rows), stage=self.stage)
        self.rows.append(row)
        with open(LOG_CSV, "a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=LOG_COLUMNS).writerow(row)
        detail = row.get("reason") or row.get("pmcid") or ""
        print(
            f"  {row['draw']:>4} {row.get('organism', '')[:40]:<40} "
            f"{row.get('pdb_id') or '':<5} {row['outcome']}: {detail}"
        )


def draw_setup(log, quiet=False, gate=None):
    """The allocation and a Sampler over SSGCID's organisms, with RCSB and PMC answers cached."""
    rows = read_organism_rows()
    targets = defaultdict(int)
    for row in rows:
        targets[row["superkingdom"]] += int(row["targets"])
    quotas = allocate({k: targets[k] for k in SUPERKINGDOMS}, PAPERS)

    resolved = resolve_rows(rows)
    organisms = organisms_from_rows(rows, resolved)
    left_out = [r for r in rows if r["taxonomy_id"] not in resolved]
    if not quiet:
        print(f"Allocation: {quotas}")
        print(f"{len(rows)} rows -> {len(organisms)} species")
        print(
            f"Left out, no NCBI species: {len(left_out)} rows, "
            f"{sum(int(r['targets']) for r in left_out)} targets: "
            f"{sorted({r['species'] for r in left_out})}"
        )

    # RCSB's and PMC's answers are cached, so a re-run with the same seed draws
    # the same sample, and `extract` can replay the draw to continue it.
    cache = json.loads(ENTITIES_JSON.read_text()) if ENTITIES_JSON.exists() else {}

    def entities_for(organism):
        key = organism.species_taxid
        if key not in cache:
            cache[key] = rcsb_protein_entities([key, *organism.row_taxids])
            ENTITIES_JSON.write_text(json.dumps(cache, indent=1))
        return cache[key]

    sampler = Sampler(organisms, entities_for, cached(check_entry), log, gate)
    return quotas, sampler, left_out


def cmd_sample(args):
    OUT.mkdir(parents=True, exist_ok=True)

    log = DrawLog("initial", append=False)
    gate = None if args.no_gate else cached_gate()
    quotas, sampler, left_out = draw_setup(log, gate=gate)
    sample = initial_draw(sampler, quotas, random.Random(args.seed))

    for paper in sample:
        paper["stage"] = "initial"
    SAMPLE_JSON.write_text(
        json.dumps(
            {
                "seed": args.seed,
                "released": [RELEASED_FROM, RELEASED_TO],
                "gate": not args.no_gate,
                "quotas": quotas,
                "left_out": sorted({r["species"] for r in left_out}),
                "papers": sample,
            },
            indent=1,
        )
    )

    print("\nFilled per superkingdom:")
    for superkingdom in SUPERKINGDOMS:
        got = sum(p["superkingdom"] == superkingdom for p in sample)
        print(f"  {superkingdom:<10} {got}/{quotas[superkingdom]}")
    reasons = defaultdict(int)
    for row in log.rows:
        if row["outcome"] == "rejected":
            reasons[row["reason"]] += 1
    print("Entry rejections:", dict(reasons))
    print(f"Wrote {SAMPLE_JSON.relative_to(ROOT)} and {LOG_CSV.relative_to(ROOT)}")


# --- Extraction ---------------------------------------------------------------


def extract_one(paper: dict) -> dict:
    """The pipeline's calls from `_find_protocols`, on the cached article."""
    from agent_engine.agent_tools.methods_tool import MethodsTool
    from agent_engine.agents.agent_body import MAX_METHODS_CHARS, _capped
    from agent_engine.agents.extraction_agent import ExtractionAgent
    from agent_engine.agents.outline_protocol_agent import ProtocolAgent

    xml = (ARTICLES / f"{paper['pmcid']}.xml").read_text(encoding="utf-8")
    article = MethodsTool().parse_article(xml)
    methods = article.get("methods")
    result = {
        "pdb_id": paper["pdb_id"],
        "title": article.get("title"),
        "methods": None,
        "methods_full_chars": len(methods) if methods else 0,
        "purification_text": None,
        "protocol": [],
        "error": None,
    }
    if not methods:
        return result
    methods = _capped(methods, MAX_METHODS_CHARS, "Methods text", paper["pdb_id"])
    result["methods"] = methods
    protein_name = paper.get("protein_name") or paper["pdb_id"]
    result["purification_text"] = ExtractionAgent().run(methods, protein_name)
    if result["purification_text"]:
        steps = ProtocolAgent().find_protocol(result["purification_text"])
        result["protocol"] = [step.model_dump() for step in steps]
    return result


def has_protocol(result: dict) -> bool:
    return bool(result.get("protocol")) and not result.get("error")


def replay(sample: dict) -> tuple[Sampler, dict[str, random.Random]]:
    """
    The sampler as it stood after the draws in `sample`, rebuilt by drawing
    them again from the caches, and each superkingdom's replacement generator
    where it stopped. Stops if the replay draws anything different.
    """
    _, sampler, _ = draw_setup(lambda **row: None, quiet=True, gate=cached_gate())
    redrawn = initial_draw(sampler, sample["quotas"], random.Random(sample["seed"]))
    rngs = {k: replacement_rng(sample["seed"], k) for k in SUPERKINGDOMS}
    replacements = [p for p in sample["papers"] if p["stage"] == "replacement"]
    for paper in replacements:
        redrawn.append(sampler.draw(paper["superkingdom"], rngs[paper["superkingdom"]]))
    initial = [p for p in sample["papers"] if p["stage"] == "initial"]
    expected = [p["pdb_id"] for p in initial + replacements]
    if [p and p["pdb_id"] for p in redrawn] != expected:
        sys.exit("Replaying the draw from the caches gave a different sample; not continuing.")
    return sampler, rngs


def cmd_extract(args):
    sample = json.loads(SAMPLE_JSON.read_text())
    done = json.loads(RESULTS_JSON.read_text()) if RESULTS_JSON.exists() else {"papers": {}}

    def extract(paper):
        key = paper["pdb_id"]
        if key in done["papers"] and not done["papers"][key].get("error"):
            return done["papers"][key]
        print(f"[{paper['superkingdom']}] {key} {paper['pmcid']}")
        try:
            result = extract_one(paper)
        except Exception as e:
            result = {"pdb_id": key, "error": f"{type(e).__name__}: {e}"}
            print(f"   failed: {result['error']}")
        done["papers"][key] = result
        RESULTS_JSON.write_text(json.dumps(done, indent=1))
        return result

    for paper in sample["papers"]:
        extract(paper)
    failed = [k for k, r in done["papers"].items() if r.get("error")]
    if failed:
        sys.exit(f"{len(failed)} papers failed; run extract again to retry them: {failed}")

    # A paper with no extracted protocol is replaced by the next draw from its
    # superkingdom, until each has its quota of papers with one.
    short = {}
    for superkingdom in SUPERKINGDOMS:
        papers = [p for p in sample["papers"] if p["superkingdom"] == superkingdom]
        got = sum(has_protocol(done["papers"][p["pdb_id"]]) for p in papers)
        short[superkingdom] = sample["quotas"][superkingdom] - got
    if any(n > 0 for n in short.values()):
        print(f"Papers still needed with a protocol: {short}")
        sampler, rngs = replay(sample)
        sampler.log = DrawLog("replacement", append=True)
        for superkingdom in SUPERKINGDOMS:
            while short[superkingdom] > 0:
                paper = sampler.draw(superkingdom, rngs[superkingdom])
                if paper is None:
                    print(f"{superkingdom} has no papers left; it stays short")
                    break
                paper["stage"] = "replacement"
                sample["papers"].append(paper)
                SAMPLE_JSON.write_text(json.dumps(sample, indent=1))
                result = extract(paper)
                if result.get("error"):
                    sys.exit(f"{paper['pdb_id']} failed; run extract again to retry and continue")
                if has_protocol(result):
                    short[superkingdom] -= 1

    with_protocol = sum(has_protocol(done["papers"][p["pdb_id"]]) for p in sample["papers"])
    print(
        f"Wrote {RESULTS_JSON.relative_to(ROOT)}: {len(sample['papers'])} papers, "
        f"{with_protocol} with a protocol"
    )


# --- Rater sheet --------------------------------------------------------------

INSTRUCTIONS = [
    ("How to rate", ""),
    (
        "Your papers",
        "Rate only the rows you were assigned, and put your name in the Rater column of each.",
    ),
    (
        "What you are rating",
        "Each paper on the Ratings tab is a block of rows, one per step of its buffer table. "
        "The pipeline read the Methods text, pulled out the purification text for the named "
        "protein, then built the table from that text. Judge the purification text and the "
        "table together against the paper, and score the paper once, in the merged cells on "
        "the right; say in Comments which one an error is in. The Methods text tab has the "
        "text the agent was given; the paper link opens the full article.",
    ),
    ("Accuracy (1–5)", "Is anything wrong or invented?"),
    ("  5", "Nothing wrong."),
    ("  4", "A minor slip that would not change how the protein is purified."),
    ("  3", "One error that would change a step: a wrong concentration, pH, resin or buffer."),
    ("  2", "Several such errors."),
    ("  1", "Mostly wrong or invented, or describes a different protein."),
    ("Completeness (1–5)", "Is anything missing?"),
    ("  5", "Every purification step and buffer component the paper gives is there."),
    ("  4", "A minor detail is missing."),
    ("  3", "One step or buffer is missing or incomplete."),
    ("  2", "Several steps or buffers are missing."),
    ("  1", "Most of the protocol is missing."),
    (
        "No protocol tab",
        "Optional. Papers the sample drew that yielded no buffer table, replaced on the Ratings "
        "tab by further papers from the same group. If you check one, answer: Absent if the "
        "paper gives no purification for this protein, Missed if it does.",
    ),
]

STOPPED_AT = {
    "methods": "No methods section found",
    "text": "No purification text found in the methods",
    "table": "Purification text, but no buffer table",
}


def stopped_at(result: dict) -> str:
    if not result.get("methods"):
        return STOPPED_AT["methods"]
    if not result.get("purification_text"):
        return STOPPED_AT["text"]
    return STOPPED_AT["table"]


# The protocol table on the Ratings tab: one row per step, one column per field.
STEP_COLUMNS = [
    ("Step", 5, "step_number"),
    ("Purification step", 30, "purification_step"),
    ("Buffer name", 14, "buffer_name"),
    ("Buffering agent", 16, "buffer_composition"),
    ("pH", 6, "ph"),
    ("Salts", 18, "salt_type"),
    ("Supplements", 26, "buffer_supplement"),
]
# Row heights are set in code: openpyxl can't autofit, and Excel caps a row.
LINE_PT, MAX_ROW_PT = 15, 409.5


def wrapped_lines(text, width: float) -> int:
    """
    How many lines text wraps to in a column of this Excel width, wrapping at
    spaces as Excel does. About one Calibri 11 character per unit of width,
    checked against Excel's own row autofit on the evaluation sample.
    """
    per_line = max(1, int(width))
    total = 0
    for paragraph in str(text or "").split("\n"):
        lines, used = 1, 0
        for word in paragraph.split(" "):
            if used and used + 1 + len(word) > per_line:
                lines, used = lines + 1, 0
            used += len(word) + (1 if used else 0)
            while used > per_line:  # a word longer than the column breaks
                lines, used = lines + 1, used - per_line
        total += lines
    return total


def row_height(lines: int) -> float:
    return min(MAX_ROW_PT, lines * LINE_PT + 4)


def add_table(wb, title, header):
    from openpyxl.styles import Alignment, Font

    sheet = wb.create_sheet(title)
    sheet.append([h for h, _ in header])
    for col, (_, width) in enumerate(header, start=1):
        sheet.column_dimensions[sheet.cell(1, col).column_letter].width = width
        sheet.cell(1, col).font = Font(bold=True)
        sheet.cell(1, col).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "C2"
    return sheet


def add_rated_paper(sheet, row: int, paper_values: list, steps: list[dict], shaded: bool) -> int:
    """
    Write one paper as a block of rows, one per step. The paper's cells, its
    purification text last, and the rating cells after the steps are merged
    down the block, so the text's height spreads over the step rows. Returns
    the block's last row.
    """
    from openpyxl.styles import Alignment, Border, PatternFill, Side

    top = Alignment(wrap_text=True, vertical="top")
    first, last = row, row + len(steps) - 1
    step_col = len(paper_values) + 1
    rating_col = step_col + len(STEP_COLUMNS)
    shade = PatternFill("solid", fgColor="F2F2F2") if shaded else None

    for col, value in enumerate(paper_values, start=1):
        sheet.cell(first, col, value)
    step_lines = []
    for i, step in enumerate(steps):
        lines = 1
        for j, (_, width, key) in enumerate(STEP_COLUMNS):
            cell = sheet.cell(first + i, step_col + j, step.get(key))
            lines = max(lines, wrapped_lines(cell.value, width))
        step_lines.append(lines)

    text_width = sheet.column_dimensions[sheet.cell(1, len(paper_values)).column_letter].width
    # One spare line: the estimate is close, and a short cell only wastes space
    # where a tall one would hide text.
    text_lines = wrapped_lines(paper_values[-1], text_width) + 1
    extra = max(0, text_lines - sum(step_lines))
    for i, lines in enumerate(step_lines):
        share = extra // len(steps) + (i < extra % len(steps))
        sheet.row_dimensions[first + i].height = row_height(lines + share)

    # A line under each paper. A merged range takes its edges from its top cell.
    edge = Border(bottom=Side(style="medium", color="808080"))
    for col in range(1, sheet.max_column + 1):
        if step_col <= col < rating_col:
            for r in range(first, last + 1):
                sheet.cell(r, col).alignment = top
                if shade:
                    sheet.cell(r, col).fill = shade
            sheet.cell(last, col).border = edge
        else:
            sheet.cell(first, col).alignment = top
            sheet.cell(first, col).border = edge
            if last > first:
                sheet.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)
    return last


def add_paper_link(sheet, row, col, pmcid):
    link = sheet.cell(row, col)
    alignment = copy(link.alignment)  # the named style would reset it
    link.hyperlink = f"{PMC_ARTICLE_URL}{pmcid}/"
    link.style = "Hyperlink"
    link.alignment = alignment


def wrap_rows(sheet):
    from openpyxl.styles import Alignment

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def cmd_sheet(args):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    sample = json.loads(SAMPLE_JSON.read_text())
    results = json.loads(RESULTS_JSON.read_text())
    papers = results["papers"]
    missing = [
        p["pdb_id"]
        for p in sample["papers"]
        if p["pdb_id"] not in papers or papers[p["pdb_id"]].get("error")
    ]
    if missing:
        sys.exit(f"Run extract first; not done or failed: {missing}")
    rated = [p for p in sample["papers"] if has_protocol(papers[p["pdb_id"]])]
    unrated = [p for p in sample["papers"] if not has_protocol(papers[p["pdb_id"]])]

    wb = Workbook()
    wb.remove(wb.active)
    identity = [
        ("#", 5),
        ("PDB ID", 8),
        ("Protein", 28),
        ("Organism", 24),
        ("Superkingdom", 14),
        ("PMCID", 14),
        ("Paper", 8),
    ]
    # The methods text is left off: at up to a full cell it would set every
    # block's height. Raters read it on the Methods text sheet or the paper.
    rating_columns = [
        ("Rater", 14),
        ("Accuracy (1–5)", 10),
        ("Completeness (1–5)", 12),
        ("Comments", 40),
    ]
    ratings = add_table(
        wb,
        "Ratings",
        identity
        + [("Extracted purification text", 70)]
        + [(name, width) for name, width, _ in STEP_COLUMNS]
        + rating_columns,
    )
    ratings.row_dimensions[1].height = 30
    methods_sheet = wb.create_sheet("Methods text")
    methods_sheet.append(
        ["PDB ID", "PMCID", "Methods text given to the agent (continues across columns)"]
    )
    chunk = CELL_LIMIT - 100

    def ids(n, paper):
        return [
            n,
            paper["pdb_id"],
            paper.get("protein_name"),
            paper["organism"],
            paper["superkingdom"],
            paper["pmcid"],
            "Open",
        ]

    row = 2
    for n, paper in enumerate(rated, start=1):
        result = papers[paper["pdb_id"]]
        methods = result["methods"]
        last = add_rated_paper(
            ratings,
            row,
            ids(n, paper) + [result["purification_text"]],
            result["protocol"],
            shaded=n % 2 == 0,
        )
        add_paper_link(ratings, row, 7, paper["pmcid"])
        methods_sheet.append(
            [paper["pdb_id"], paper["pmcid"]]
            + [methods[i : i + chunk] for i in range(0, len(methods), chunk)]
        )
        row = last + 1
    score = DataValidation(
        type="whole",
        operator="between",
        formula1="1",
        formula2="5",
        allow_blank=True,
        error="Enter a whole number from 1 to 5.",
    )
    ratings.add_data_validation(score)
    accuracy = ratings.max_column - len(rating_columns) + 2
    score.add(f"{get_column_letter(accuracy)}2:{get_column_letter(accuracy + 1)}{ratings.max_row}")

    no_protocol = add_table(
        wb,
        "No protocol",
        identity
        + [
            ("Where the pipeline stopped", 24),
            ("Extracted purification text", 70),
            ("Rater", 14),
            ("Purification for this protein in the paper? Absent or Missed", 16),
            ("Comments", 40),
        ],
    )
    for n, paper in enumerate(unrated, start=1):
        result = papers[paper["pdb_id"]]
        no_protocol.append(
            ids(n, paper) + [stopped_at(result), result.get("purification_text") or "(none)"]
        )
        add_paper_link(no_protocol, n + 1, 7, paper["pmcid"])
    wrap_rows(no_protocol)
    absent = DataValidation(type="list", formula1='"Absent,Missed"', allow_blank=True)
    no_protocol.add_data_validation(absent)
    absent.add(f"K2:K{len(unrated) + 1}")

    instructions = wb.create_sheet("Instructions", 0)
    for label, text in INSTRUCTIONS:
        instructions.append([label, text])
    instructions.column_dimensions["A"].width = 22
    instructions.column_dimensions["B"].width = 100
    for row in instructions.iter_rows():
        row[0].font = Font(bold=not row[0].value.startswith("  "))
        row[0].alignment = Alignment(vertical="top")
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    info = wb.create_sheet("Sample")
    stopped = defaultdict(int)
    for paper in unrated:
        stopped[stopped_at(papers[paper["pdb_id"]])] += 1
    lines = [
        ("Seed", sample["seed"]),
        ("PDB release dates", " to ".join(sample["released"])),
        ("Papers per superkingdom", json.dumps(sample["quotas"])),
        ("Papers drawn", len(sample["papers"])),
        ("With a protocol (rated)", len(rated)),
        *[(f"  {reason}", count) for reason, count in stopped.items()],
    ]
    for label, value in lines:
        info.append([label, value])
    info.column_dimensions["A"].width = 44
    info.column_dimensions["B"].width = 90

    wb.save(SHEET_XLSX)
    print(f"Wrote {SHEET_XLSX.relative_to(ROOT)}: {len(rated)} rated, {len(unrated)} no protocol")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)
    sample = steps.add_parser(
        "sample", help="draw the papers, gate out those with no protocol, and log every draw"
    )
    sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sample.add_argument(
        "--no-gate", action="store_true", help="skip the protocol gate (dry run, no LLM calls)"
    )
    sample.set_defaults(run=cmd_sample)
    steps.add_parser("extract", help="run the parser and agents on the sample").set_defaults(
        run=cmd_extract
    )
    steps.add_parser("sheet", help="write the rater workbook").set_defaults(run=cmd_sheet)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
