import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from dotenv import load_dotenv

from ..agent_tools.blast import BlastError, run_blastp
from ..agent_tools.cttdb_protocols import get_cttdb_info
from ..agent_tools.grounding_tool import GroundingTool
from ..agent_tools.methods_tool import MethodsTool
from ..agent_tools.protein_similarity_tool import ProteinSimilarityTool
from ..agent_tools.sequence_properties import sequence_properties
from ..agents.comprehensive_protocol_agent import SuggestedProtocolAgent
from ..agents.extraction_agent import ExtractionAgent
from ..agents.outline_protocol_agent import ProtocolAgent
from ..llm import configured_model_name
from ..models import (
    Hit,
    HitStatus,
    MethodsSource,
    Paper,
    PaperAccess,
    SourceKind,
    SourceProtocol,
)
from ..settings import internal_data_enabled
from ..trace import StageClock, Trace, collecting, set_subject

load_dotenv()

# The most text one paper sends to the extraction agent, a bound on a job's
# token cost (#26). Methods sections run well under it; it guards against a
# pathological article.
MAX_METHODS_CHARS = 50_000

# How much of an unresolvable input an error message repeats back.
ECHO_LENGTH = 40

# Why synthesis was skipped (#89). Stored in reports, so keep the wording stable.
NO_SOURCE_PROTOCOLS = (
    "No source protocols were extracted from the literature, so there was nothing to "
    "compare the target against."
)


def _capped(text: str, limit: int, what: str, pdb_id: str) -> str:
    """
    The text cut to `limit` characters at the last line break before it, so a
    paragraph or table row is sent whole or not at all. A single line longer
    than the limit is cut mid-line.
    """
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit + 1)
    capped = text[:cut] if cut > 0 else text[:limit]
    print(
        f"   [Agent] {what} for {pdb_id} is {len(text)} characters; sending the first {len(capped)}"
    )
    return capped


def _echo(text: str) -> str:
    """User input quoted in an error message, shortened so a pasted sequence stays readable."""
    text = " ".join(text.split())
    return text if len(text) <= ECHO_LENGTH else text[: ECHO_LENGTH - 1] + "…"


@dataclass
class AgentResult:
    success: bool
    purifications: List[SourceProtocol] = field(default_factory=list)
    comprehensive_protocol: Optional[str] = None
    raw_plan: Optional[str] = None
    similar_proteins: List[Hit] = field(default_factory=list)
    # The papers the hits cite, in the order found. Hits point to them by PMID.
    papers: List[Paper] = field(default_factory=list)
    error_message: Optional[str] = None
    # Set when the run succeeded but final synthesis failed. The BLAST hits and
    # extracted source protocols are still valid, so this is a partial result
    # rather than a failure.
    synthesis_error: Optional[str] = None
    # Why synthesis was not attempted (#89). A skip is an expected outcome, not
    # a failure, so it is kept apart from synthesis_error.
    synthesis_skipped: Optional[str] = None
    # Every LLM call and each stage's time (#77), for successful and failed runs.
    trace: Optional[Trace] = None


class ProteinPurificationAgent:
    def run(
        self,
        protein_name,
        min_pident,
        min_qcov,
        max_evalue,
        max_hits,
        max_protocols,
        failed_purification_text: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        trace: Optional[Trace] = None,
    ) -> AgentResult:
        """
        Run the pipeline, recording its LLM calls and stage times into `trace`.
        A caller that passes its own trace keeps what was recorded even if the
        run raises.
        """
        trace = trace if trace is not None else Trace(model=configured_model_name())
        with collecting(trace):
            result = self._run(
                protein_name,
                min_pident,
                min_qcov,
                max_evalue,
                max_hits,
                max_protocols,
                failed_purification_text,
                status_callback,
            )
        result.trace = trace
        return result

    def _run(
        self,
        protein_name,
        min_pident,
        min_qcov,
        max_evalue,
        max_hits,
        max_protocols,
        failed_purification_text,
        status_callback,
    ) -> AgentResult:
        def update_status(msg):
            if status_callback:
                status_callback(msg)

        clock = StageClock()
        raw_input = protein_name.strip()
        protein_name_clean = raw_input

        if ">" in raw_input:
            header = raw_input.split("\n")[0]
            protein_name_clean = header.replace(">", "").strip().split()[0]

        grounding_tool = GroundingTool()
        target_metadata = grounding_tool.get_uniprot_metadata(protein_name_clean)

        # ID Processing & CTTdb Lookup
        result = self._resolve_input(raw_input, update_status)
        if isinstance(result, AgentResult):
            return result
        fasta_id, subject_taxonomy_id, ssgcid_protocol = result

        if not fasta_id.startswith(">"):
            update_status(f"Fetching full sequence for UniProt ID: {fasta_id}...")
            full_fasta = self._get_fasta_from_uniprot(fasta_id)

            if not full_fasta:
                return AgentResult(
                    success=False,
                    error_message=(
                        f"Failed to retrieve sequence for UniProt ID {_echo(fasta_id)!r}. "
                        "Please provide a full FASTA sequence."
                    ),
                )
            fasta_id = full_fasta

        # The planner's pI heuristic works from computed values rather than its own
        # estimate (#81). They travel with the UniProt metadata, which may be absent.
        properties = sequence_properties(fasta_id)
        if properties:
            target_metadata = {**(target_metadata or {}), **properties}

        # User-provided failed purification overrides CTTdb protocol
        if failed_purification_text and failed_purification_text.strip():
            print("   [Agent] Using user-provided failed purification (overriding CTTdb).")
            update_status("Analyzing user-provided failed purification...")
            set_subject("User-provided failed protocol", "user")
            try:
                protocol_data = ProtocolAgent().find_protocol(failed_purification_text.strip())
            except Exception as e:
                # The provider's message (a missing key, an exhausted quota) is
                # what the user needs to act on, as for a failed synthesis.
                print(f"   [Agent] Could not tabulate the failed purification: {e}")
                return AgentResult(success=False, error_message=str(e))

            ssgcid_protocol = SourceProtocol(
                purification_text=failed_purification_text.strip(),
                protocol=protocol_data,
                article_title="User-Provided Failed Protocol",
                article_link=None,
                organism_name="User Input",
                uniprot_id=protein_name_clean,
                source=SourceKind.USER,
            )

        clock.done("Input resolution")

        # Adaptive BLAST
        result = self._run_adaptive_blast(
            fasta_id, min_qcov, min_pident, max_evalue, max_hits, update_status
        )
        if isinstance(result, AgentResult):
            return result
        blast_results = result

        # Similarity & Filtering
        update_status(f"Processing {len(blast_results)} BLAST hits...")
        similar_proteins = self._rank_similarities(blast_results, subject_taxonomy_id)
        clock.done("BLAST and ranking")

        # Protocol Extraction
        update_status("Finding matching protocols in PMC...")
        purifications, papers = self._find_protocols(similar_proteins, max_protocols, update_status)
        clock.done("Literature and extraction")

        # LLM Synthesis. It derives the protocol from the differences between the
        # failed attempt and the successful ones, so with no source protocols there
        # is nothing to compare and the step is skipped (#89).
        synthesis_skipped = None
        comprehensive_protocol = raw_plan = synthesis_error = None
        if purifications:
            comprehensive_protocol, raw_plan, synthesis_error = self._synthesize(
                purifications, ssgcid_protocol, target_metadata, update_status
            )
        else:
            print(f"   [Agent] {NO_SOURCE_PROTOCOLS}")
            update_status("No source protocols found. Skipping synthesis...")
            synthesis_skipped = NO_SOURCE_PROTOCOLS
        clock.done("Synthesis")

        print("   [Agent] Run Complete.")

        if ssgcid_protocol:
            purifications.insert(0, ssgcid_protocol)

        return AgentResult(
            success=True,
            purifications=purifications,
            comprehensive_protocol=comprehensive_protocol,
            raw_plan=raw_plan,
            similar_proteins=similar_proteins,
            papers=papers,
            synthesis_error=synthesis_error,
            synthesis_skipped=synthesis_skipped,
        )

    def _resolve_input(self, protein_name, update_status):
        protocol_agent = ProtocolAgent()
        fasta_id = ""
        subject_taxonomy_id = ""
        ssgcid_protocol = None

        if len(protein_name) < 15 and "." in protein_name:
            print(f"   [Agent] Detected SSGCID ID format: {protein_name}")
            if not internal_data_enabled():
                return AgentResult(
                    success=False,
                    error_message=(
                        f"{protein_name!r} looks like an SSGCID ID. SSGCID IDs need the internal "
                        "database, which is not available here, so paste the FASTA sequence instead."
                    ),
                )
            ssgcid_id = protein_name
            update_status(f"Retrieving internal data for {ssgcid_id}...")

            try:
                ssgcid_text, sequence, taxonomy_information = get_cttdb_info(ssgcid_id)

                if ssgcid_text and sequence:
                    print("   [Agent] CTTdb info found.")
                    try:
                        dna_seq = Seq(sequence.upper())
                        start_index = dna_seq.find("ATG")
                        if start_index != -1:
                            protein_seq = dna_seq[start_index:].translate(to_stop=True)
                            seq_record = SeqRecord(protein_seq, id=ssgcid_id, description="")
                            fasta_id = seq_record.format("fasta")
                        else:
                            print("   [Agent] Warning: No start codon in CTTdb sequence.")
                            return AgentResult(
                                success=False,
                                error_message="No start codon found in internal sequence.",
                            )
                    except Exception as e:
                        print(f"   [Agent] Sequence processing error: {e}")
                        return AgentResult(success=False, error_message=f"Sequence Error: {e}")

                    if taxonomy_information:
                        subject_taxonomy_id = taxonomy_information[3]

                        set_subject(f"{ssgcid_id} failed protocol (CTTdb)", "internal")
                        ssgcid_protocol = SourceProtocol(
                            purification_text=ssgcid_text,
                            protocol=protocol_agent.find_protocol(ssgcid_text),
                            article_title=f"{ssgcid_id} Failed Protocol ({taxonomy_information[0]} {taxonomy_information[1]})",
                            article_link=f"https://targetstatus.ssgcid.org/Target/{protein_name}",
                            organism_name=f"{taxonomy_information[0]} {taxonomy_information[1]}",
                            uniprot_id=protein_name,
                            source=SourceKind.INTERNAL,
                        )
                else:
                    print("   [Agent] No CTTdb entry found. Continuing with name as fasta...")
                    fasta_id = protein_name
            except Exception as e:
                print(f"   [Agent] CTTdb Lookup failed: {e}")
                fasta_id = protein_name
        else:
            fasta_id = protein_name

        return fasta_id, subject_taxonomy_id, ssgcid_protocol

    def _run_adaptive_blast(
        self, fasta_id, min_qcov, min_pident, max_evalue, max_hits, update_status
    ):
        db_path = os.getenv("BLAST_DB_PATH")
        if not db_path:
            return AgentResult(
                success=False,
                error_message="BLAST database not configured. See README for setup instructions.",
            )

        update_status(f"Running BLAST (Strict: {min_qcov}% Cov)...")
        try:
            blast_results = run_blastp(
                fasta_id,
                min_qcov,
                min_pident,
                max_evalue,
                max_hits,
                db_path,
                extra_args=["-seg", "no"],
            )
        except BlastError as e:
            return AgentResult(success=False, error_message=f"BLAST Error: {e}")
        except Exception as e:
            print(f"   [Agent] BLAST failed: {type(e).__name__}: {e}")
            return AgentResult(success=False, error_message="BLAST Error: the search failed.")

        # Fallback: relax parameters if strict search got nothing
        if not blast_results:
            print(
                "   [Agent] Strict BLAST returned 0 hits. Attempting Rescue Strategy (Relaxed Params)..."
            )
            update_status("Strict search failed. Relaxing parameters (Rescue Mode)...")

            RELAXED_COV = 20.0
            RELAXED_IDENT = 20.0

            try:
                blast_results = run_blastp(
                    fasta_id,
                    RELAXED_COV,
                    RELAXED_IDENT,
                    max_evalue,
                    max_hits,
                    db_path,
                    extra_args=["-seg", "no"],
                )
                print(f"   [Agent] Rescue Strategy found {len(blast_results)} hits.")
            except BlastError as e:
                return AgentResult(success=False, error_message=f"BLAST Rescue Error: {e}")
            except Exception as e:
                print(f"   [Agent] Rescue BLAST failed: {type(e).__name__}: {e}")
                return AgentResult(
                    success=False, error_message="BLAST Rescue Error: the search failed."
                )

        if not blast_results:
            print("   [Agent] No hits found even after relaxation.")
            return AgentResult(
                success=False,
                error_message="No BLAST results found. The protein might be unique or requires 'nr' database search.",
            )

        return blast_results

    def _rank_similarities(self, blast_results, subject_taxonomy_id):
        protein_similarity_tool = ProteinSimilarityTool()

        if not subject_taxonomy_id:
            print("   [Agent] Warning: No Taxonomy ID found. Ranking by identity only.")

        return protein_similarity_tool.calculate_similarity(subject_taxonomy_id, blast_results)

    def _find_protocols(
        self, similar_proteins: List[Hit], max_protocols, update_status
    ) -> Tuple[List[SourceProtocol], List[Paper]]:
        grounding_tool = GroundingTool()
        methods_tool = MethodsTool()
        extraction_agent = ExtractionAgent()
        protocol_agent = ProtocolAgent()

        purifications: List[SourceProtocol] = []
        # Keyed by PMID, so hits citing one paper are found as duplicates whether
        # or not it is in PMC (#46). Several hits often share a paper.
        papers: Dict[str, Paper] = {}

        listing_rest = False
        for i, hit in enumerate(similar_proteins):
            pdb_id = hit.pdb_id

            # Past the protocol stop, each hit's citation is still resolved from
            # RCSB alone, with no full text or LLM calls, so the manual-review list
            # covers every hit. A PMC paper
            # stays Not Analyzed: only its full text says whether it is open.
            if len(purifications) >= max_protocols:
                if not listing_rest:
                    update_status(
                        f"Listing papers for the remaining {len(similar_proteins) - i} hits..."
                    )
                    listing_rest = True
                try:
                    self._resolve_citation(hit, papers, grounding_tool, fetching=False)
                except Exception as e:
                    hit.status = HitStatus.CITATION_LOOKUP_FAILED
                    print(f"   [Agent] Error listing the paper for {pdb_id}: {e}")
                continue

            update_status(
                f"Analyzing Match {i + 1}: {pdb_id} ({hit.organism_name or 'Unknown'})..."
            )
            set_subject(f"PDB {pdb_id}", pdb_id)

            try:
                # PDB -> citation -> PMC article -> extract purification -> tabulate
                paper = self._resolve_citation(hit, papers, grounding_tool)
                if paper is None:
                    continue

                raw_article, fetch_status = grounding_tool.search_pmc(paper.pmcid)
                if not raw_article:
                    paper.access = (
                        PaperAccess.FETCH_FAILED
                        if fetch_status == HitStatus.PMC_RETRIEVAL_FAILED
                        else PaperAccess.PMC_RESTRICTED
                    )
                    hit.status = fetch_status
                    continue
                paper.access = PaperAccess.OPEN

                structured_article = methods_tool.parse_article(raw_article)
                paper.title = structured_article["title"] or paper.title
                protein_name = hit.protein_name or pdb_id

                # The methods sections only. There is no whole-article fallback:
                # an article without them yields no protocol.
                purification_text = None
                methods = structured_article.get("methods")
                if methods:
                    methods = _capped(methods, MAX_METHODS_CHARS, "Methods text", pdb_id)
                    purification_text = extraction_agent.run(methods, protein_name)
                    if purification_text:
                        paper.methods_source = MethodsSource.SECTIONS
                # Nothing to tabulate. The raw methods text used to go to the table
                # agent here, which could tabulate off-target buffers.
                if not purification_text:
                    hit.status = HitStatus.NO_PROTOCOL_FOUND
                    continue

                tabular_protocol = protocol_agent.find_protocol(purification_text)

                if tabular_protocol:
                    hit.status = HitStatus.PROTOCOL_FOUND
                    print(f"   [Agent] Protocol found for {pdb_id}")
                    purifications.append(
                        SourceProtocol(
                            purification_text=purification_text,
                            protocol=tabular_protocol,
                            article_title=paper.title,
                            article_link=paper.link,
                            organism_name=hit.organism_name or "N/A",
                            uniprot_id=hit.uniprot_id or "N/A",
                            source=SourceKind.PAPER,
                            pdb_id=pdb_id,
                        )
                    )
                else:
                    hit.status = HitStatus.NO_PROTOCOL_FOUND

            except Exception as e:
                hit.status = HitStatus.ERROR_PROCESSING
                print(f"   [Agent] Error processing {pdb_id}: {e}")
                continue

        # The title, journal and year of each paper the pipeline couldn't read, for
        # the manual-review list (#47), from the first hit that cites it.
        detailed = set()
        for hit in similar_proteins:
            paper = papers.get(hit.pmid) if hit.pmid else None
            if paper and paper.for_manual_review and paper.pmid not in detailed:
                detailed.add(paper.pmid)
                grounding_tool.add_citation_details(paper, hit.pdb_id)

        return purifications, list(papers.values())

    def _resolve_citation(
        self,
        hit: Hit,
        papers: Dict[str, Paper],
        grounding_tool: GroundingTool,
        fetching: bool = True,
    ) -> Optional[Paper]:
        """
        Look up the hit's paper from RCSB and record it. Returns the paper when it
        is in PMC and its full text is worth fetching; otherwise sets the hit's
        final status and returns None.

        A paper an earlier hit couldn't fetch is fetched again for this one while
        `fetching`: a failed fetch says nothing about the paper, so the later hit
        isn't "Paper Already Found" for a paper nobody read. A paper is still read,
        and its LLM calls made, at most once.
        """
        paper, lookup_status = grounding_tool.lookup_citation(hit.pdb_id)
        if paper is None:
            hit.status = lookup_status
            return None
        hit.pmid = paper.pmid
        known = papers.get(paper.pmid)
        if known is not None:
            if fetching and known.access == PaperAccess.FETCH_FAILED:
                return known
            hit.status = HitStatus.PAPER_ALREADY_FOUND
            return None
        papers[paper.pmid] = paper
        if not paper.pmcid:
            hit.status = HitStatus.NO_PMC_PRIMARY_CITATION
            return None
        return paper

    def _synthesize(self, purifications, ssgcid_protocol, target_metadata, update_status):
        """
        Derive a protocol from the source protocols. `purifications` must not be
        empty: `run` skips this step instead of calling it with nothing to
        compare against (#89).
        """
        suggestion_agent = SuggestedProtocolAgent()

        print("   [Agent] Generating final report...")
        update_status("Synthesizing final protocol with LLM...")

        set_subject("Synthesis")
        try:
            comprehensive_protocol, raw_plan = suggestion_agent.run(
                purifications, ssgcid_protocol, target_metadata
            )
        except Exception as e:
            print(f"   [Agent] LLM Error: {e}")
            return None, None, f"{type(e).__name__}: {e}"

        return comprehensive_protocol, raw_plan, None

    def _get_fasta_from_uniprot(self, uniprot_id: str) -> Optional[str]:
        """
        Fetches the full FASTA sequence for a given UniProt ID using the UniProt REST API.
        Returns the FASTA string if successful, or None if the ID is invalid/fails.
        """
        uniprot_id = uniprot_id.strip()
        # The ID is user input, so it can only ever name one UniProt entry.
        url = f"https://rest.uniprot.org/uniprotkb/{urllib.parse.quote(uniprot_id, safe='')}.fasta"

        try:
            # Fetch the data
            with urllib.request.urlopen(url, timeout=10) as response:
                fasta_data = response.read().decode("utf-8")
                return fasta_data

        except urllib.error.HTTPError as e:
            print(
                f"  [UniProt API] HTTP Error {e.code}: Could not fetch sequence for ID {uniprot_id}."
            )
            return None
        except urllib.error.URLError as e:
            print(f"  [UniProt API] Connection Error: {e.reason}")
            return None
        except Exception as e:
            print(f"  [UniProt API] An unexpected error occurred: {e}")
            return None
