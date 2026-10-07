"""
Typed pipeline state (#46): BLAST hits, the papers they cite, and source protocols.

These are built where the data first appears (run_blastp, the citation lookup,
the per-hit loop) and carried unchanged through AgentResult into the stored
report, so FastAPI's OpenAPI schema, and the frontend types generated from it,
describe the real result.

Stored reports are never migrated. A field added after reports were
stored is optional with a None default, and enum values keep the exact strings
older reports hold.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field


class HitStatus(str, Enum):
    """
    Where a hit's search for a protocol ended. The value is also its display
    label, so this is the one place a status is worded. The frontend matches
    some of them exactly (BlastResults.getStatusIcon).
    """

    NOT_ANALYZED = "Not Analyzed"  # the loop stopped before reaching it
    NO_RCSB_METADATA = "No RCSB Metadata"  # ranking had no organism for it
    CITATION_LOOKUP_FAILED = "Citation Lookup Failed"  # RCSB errored; access unknown
    NO_PMC_PRIMARY_CITATION = "No PMC Primary Citation"  # no publication, or not in PMC
    PAPER_ALREADY_FOUND = "Paper Already Found"  # an earlier hit cites the same paper
    NO_OPEN_ACCESS = "No Open Access"  # in PMC, but the full text is withheld
    PMC_RETRIEVAL_FAILED = "PMC Retrieval Failed"  # every fetch failed; access unknown
    NO_PROTOCOL_FOUND = "No Protocol Found in Paper"
    PROTOCOL_FOUND = "Protocol Found"
    ERROR_PROCESSING = "Error Processing"


class PaperAccess(str, Enum):
    """How much of a paper the pipeline could read. Unset until checked."""

    OPEN = "open"
    PMC_RESTRICTED = "pmc_restricted"  # in PMC, not in the open-access subset
    NOT_IN_PMC = "not_in_pmc"  # a PubMed record with no PMC copy
    FETCH_FAILED = "fetch_failed"  # every PMC fetch failed


class MethodsSource(str, Enum):
    """Which text the extraction agent found a paper's purification text in (#29)."""

    SECTIONS = "sections"  # the sections whose titles name methods
    # The whole-article fallback, since removed. Kept so stored reports validate.
    FULL_TEXT = "full_text"


class SourceKind(str, Enum):
    PAPER = "paper"
    INTERNAL = "internal"  # the SSGCID/CTTdb failed protocol
    USER = "user"  # the failed protocol the user pasted


class ExtractedStep(BaseModel):
    """
    One buffer step as the table agent extracts it. These fields are what the
    model is asked for; anything the pipeline knows itself is on BufferStep.
    """

    purification_step: Optional[str] = Field(
        ...,
        description=(
            "A highly specific name for the purification step. "
            "Combine the exact technique or resin name from the text with the action (e.g., Lysis, Wash, Elution). "
            "Examples: 'Ni-NTA Affinity Chromatography - Wash', 'Cell Extraction'."
        ),
    )
    buffer_name: Optional[str] = Field(
        None,
        description="The specific name given to the buffer in the text, if any (e.g., 'Buffer A', 'Lysis Buffer').",
    )

    buffer_composition: Optional[str] = Field(
        None,
        description="List buffering agents and their concentrations (e.g., '50 mM Tris', '20 mM HEPES'). If not specified, leave as null.",
    )

    ph: Optional[float] = Field(
        None,
        description="The pH of the buffer, as a number (e.g., 8.0). If not mentioned, leave as null.",
    )

    # The name is kept so stored reports still validate; it holds concentrations
    # too.
    salt_type: Optional[str] = Field(
        None,
        description=(
            "List ALL salts with their concentrations as the text states them, "
            "for example '300 mM NaCl, 5 mM MgCl2'. Give a salt's name alone only "
            "when the text gives no concentration for it."
        ),
    )

    buffer_supplement: Optional[str] = Field(
        None,
        description="List additives present in the buffer like reducing agents (DTT), nucleotides (ATP), detergents, or cryoprotectants (glycerol). For example: '2 mM DTT, 30mM imidazole'.",
    )


class BufferStep(ExtractedStep):
    """A buffer step in the report: what the model extracted, and where it sits."""

    # 1-based position within its source protocol, set in code from the order
    # the text gives the steps in. The step's source protocol is
    # the SourceProtocol whose `protocol` list holds it.
    step_number: Optional[int] = None


class Hit(BaseModel):
    """One BLAST hit against pdbaa, ranked and then followed to its paper."""

    protein_name: str
    pdb_id: str
    length: int
    e_value: float
    pident: float
    query_coverage: float
    query_start: int
    query_end: int
    subject_start: int
    subject_end: int
    # Set by ranking: BLAST identity, blended with taxonomic distance when known.
    similarity_score: Optional[float] = None
    uniprot_id: Optional[str] = None
    organism_name: Optional[str] = None
    taxonomy_id: Optional[int] = None
    status: HitStatus = HitStatus.NOT_ANALYZED
    # The paper RCSB lists as this entry's primary citation, keyed by PMID.
    pmid: Optional[str] = None


class Paper(BaseModel):
    """A hit's primary citation, from RCSB, and what the pipeline could read of it."""

    pmid: str
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[int] = None
    abstract: Optional[str] = None
    access: Optional[PaperAccess] = None
    methods_source: Optional[MethodsSource] = None

    @computed_field
    @property
    def for_manual_review(self) -> bool:
        """
        A paper the pipeline couldn't read because it isn't open access, listed
        with its title, abstract and link so the user can read it (#47).
        """
        return self.access in (PaperAccess.PMC_RESTRICTED, PaperAccess.NOT_IN_PMC)

    @computed_field
    @property
    def link(self) -> str:
        """The PMC page, else the DOI, else PubMed."""
        if self.pmcid:
            return f"https://pmc.ncbi.nlm.nih.gov/articles/{self.pmcid}"
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}"


class SourceProtocol(BaseModel):
    """A protocol the planner works from: a paper's, or the failed one."""

    purification_text: Optional[str] = None
    protocol: Optional[List[BufferStep]] = None
    article_title: Optional[str] = None
    article_link: Optional[str] = None
    organism_name: Optional[str] = None
    uniprot_id: Optional[str] = None
    source: Optional[SourceKind] = None
    # The hit a paper's protocol came from; its paper is that hit's pmid.
    pdb_id: Optional[str] = None
