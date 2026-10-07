from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from agent_engine.models import Hit, Paper, SourceProtocol

# Input bounds (#26). A FASTA record up to 20,000 characters covers all but a
# handful of the largest known proteins. The failed-purification text goes
# straight into the LLM prompts, so its length bounds a job's token cost. The
# home form mirrors both limits in purification-rescue-frontend/src/lib/types/api.ts.
MAX_FASTA_LENGTH = 20_000
MAX_FAILED_PURIFICATION_LENGTH = 20_000
# Each hit can cost RCSB and Entrez calls when no protocol turns up. 50 is the
# home form's default.
MAX_HITS = 50
# The search stops once this many source protocols are extracted. Each costs up
# to two LLM calls and goes into the synthesis prompt, so it bounds a job's
# token cost; 5 was the fixed value before it was configurable.
MAX_PROTOCOLS = 5
DEFAULT_PROTOCOLS = 3


class PurificationRequest(BaseModel):
    fasta_id: str = Field(
        ..., min_length=1, max_length=MAX_FASTA_LENGTH, description="Protein FASTA sequence or ID"
    )
    failed_purification_text: Optional[str] = Field(
        default=None,
        max_length=MAX_FAILED_PURIFICATION_LENGTH,
        description="User-provided failed purification text to override CTTdb lookup",
    )
    min_percent_identity: float = Field(default=90.0, ge=0, le=100)
    min_query_coverage: float = Field(default=90.0, ge=0, le=100)
    max_evalue: float = Field(default=1e-5, gt=0, le=1000)
    max_hits: int = Field(default=10, ge=1, le=MAX_HITS)
    max_protocols: int = Field(
        default=DEFAULT_PROTOCOLS,
        ge=1,
        le=MAX_PROTOCOLS,
        description="Source protocols to extract before the literature search stops",
    )


class JobListItem(BaseModel):
    """One row of GET /jobs (My Jobs)."""

    id: str
    name: Optional[str] = Field(default=None, description="The owner's name for the job, if set")
    input_summary: str = Field(..., description="The protein name, ID or FASTA header")
    state: Literal["queued", "running", "completed", "failed", "interrupted"]
    progress: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runtime_seconds: Optional[float] = Field(
        default=None, description="Start to finish, or start to now while running"
    )


# Mirrored by MAX_JOB_NAME_LENGTH in purification-rescue-frontend/src/lib/types/api.ts.
MAX_JOB_NAME_LENGTH = 200


class RenameJobRequest(BaseModel):
    # Blank or null clears the name, so My Jobs shows the input summary again.
    name: Optional[str] = Field(default=None, max_length=MAX_JOB_NAME_LENGTH)


# Enough for a page of My Jobs many times over; bounds one request's work.
MAX_BULK_DELETE = 500


class BulkDeleteRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=MAX_BULK_DELETE)


class SkippedJob(BaseModel):
    id: str
    # running covers queued jobs too. not_found covers other users' jobs, so
    # the response never reveals that someone else's job exists.
    reason: Literal["running", "not_found"]


class BulkDeleteResult(BaseModel):
    deleted: List[str]
    skipped: List[SkippedJob]


class ProtocolResult(BaseModel):
    """
    A job's report, as stored and as /result serves it. Stored reports are never
    migrated, so fields added later are optional; see "Changing the
    report" in docs/system_runbooks/backend.md.
    """

    purifications: Optional[List[SourceProtocol]] = None
    comprehensive_protocol: Optional[str] = None
    raw_plan: Optional[str] = None
    blast_results: Optional[List[Hit]] = None
    papers: Optional[List[Paper]] = None
    error_message: Optional[str] = None
    # Why synthesis was not attempted (#89): set when no source protocols were
    # found, which leaves the comparison the step performs with no input.
    synthesis_skipped: Optional[str] = None
