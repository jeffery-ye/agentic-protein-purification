"""
The job repository interface and the records it returns.

main.py talks to jobs only through JobRepository. SqliteJobRepository is the
store in dev and deploy; InMemoryJobRepository exists for tests.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

JobState = Literal["queued", "running", "completed", "failed", "interrupted"]

# What delete() did: removed the job, found no job of that owner, or refused
# because the job is still queued or running.
DeleteOutcome = Literal["deleted", "not_found", "active"]

ACTIVE_STATES: tuple[JobState, ...] = ("queued", "running")

# Which global limit refused a new job (#26).
JobCap = Literal["active", "daily"]


@dataclass(frozen=True)
class JobCaps:
    """
    Global limits on new jobs (#26); None means no limit. `max_active` counts
    queued and running jobs. `max_per_day` counts every job created that UTC
    day, including ones since deleted, so deleting jobs doesn't free up the
    day's allowance.
    """

    max_active: int | None = None
    max_per_day: int | None = None


class JobCapReached(Exception):
    """create() refused a job because one of its JobCaps is full."""

    def __init__(self, cap: JobCap, limit: int):
        self.cap = cap
        self.limit = limit
        super().__init__(f"The {cap} job cap of {limit} is reached")


# The error recorded on a job that a previous process left unfinished.
INTERRUPTED_ERROR = "The server restarted before this job finished. Please submit it again."


@dataclass(frozen=True)
class Owner:
    """
    Who submitted a job: the signed-in user's `sub`, with the username for
    display. Jobs from before sign-in (#25) have neither.
    """

    sub: str | None = None
    username: str | None = None


@dataclass(frozen=True)
class ProgressEntry:
    at: str
    message: str


@dataclass(frozen=True)
class JobRecord:
    id: str
    owner: Owner
    inputs: dict[str, Any]
    state: JobState
    progress: str | None
    result: dict[str, Any] | None
    error: str | None
    model: str | None
    app_version: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    # What the owner renamed the job to in My Jobs; None until they do.
    name: str | None = None


@dataclass(frozen=True)
class JobSummary:
    """A job without its result, for listing."""

    id: str
    owner: Owner
    inputs: dict[str, Any]
    state: JobState
    progress: str | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    name: str | None = None


def summarize(job: JobRecord) -> JobSummary:
    return JobSummary(
        id=job.id,
        owner=job.owner,
        inputs=job.inputs,
        state=job.state,
        progress=job.progress,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        name=job.name,
    )


def utc_now() -> str:
    """An ISO 8601 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_day(timestamp: str) -> str:
    """The UTC date, YYYY-MM-DD, of a utc_now() timestamp."""
    return timestamp[:10]


class JobRepository(ABC):
    """
    Persistent job state.

    State transitions are guarded: each mutator applies only from the states
    named in its docstring and returns whether it did, so a late callback can
    never move a finished job backwards.
    """

    @abstractmethod
    def create(
        self,
        job_id: str,
        inputs: dict[str, Any],
        owner: Owner,
        model: str | None,
        app_version: str | None,
        caps: JobCaps | None = None,
    ) -> JobRecord:
        """
        Insert a new job in the `queued` state. Raises JobCapReached if `caps`
        is full. The check and the insert are one atomic step, so a burst of
        requests can't get past a cap.
        """

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None:
        """The job, or None if no such job exists."""

    @abstractmethod
    def history(self, job_id: str) -> list[ProgressEntry]:
        """Every progress message recorded for the job, oldest first."""

    @abstractmethod
    def list_by_owner(self, owner_sub: str) -> list[JobSummary]:
        """The owner's jobs, newest first, without their results."""

    @abstractmethod
    def delete(self, job_id: str, owner_sub: str) -> DeleteOutcome:
        """
        Hard-delete a finished, failed or interrupted job with its history.
        A job that doesn't exist or belongs to someone else is not_found; a
        queued or running one is left alone and reported as active.
        """

    @abstractmethod
    def rename(self, job_id: str, owner_sub: str, name: str | None) -> bool:
        """
        Set the job's name, in any state, or clear it with None. False if the
        job doesn't exist or belongs to someone else.
        """

    @abstractmethod
    def mark_running(self, job_id: str) -> bool:
        """queued -> running, stamping started_at."""

    @abstractmethod
    def record_progress(self, job_id: str, message: str) -> bool:
        """Set the latest progress and append it to the history. Running jobs only."""

    @abstractmethod
    def complete(self, job_id: str, result: dict[str, Any]) -> bool:
        """running -> completed, storing the result JSON."""

    @abstractmethod
    def fail(self, job_id: str, error: str) -> bool:
        """queued or running -> failed, storing the error."""

    @abstractmethod
    def save_trace(self, job_id: str, trace: dict[str, Any]) -> bool:
        """Store the job's trace JSON (#77), in any state."""

    @abstractmethod
    def get_trace(self, job_id: str) -> dict[str, Any] | None:
        """The job's trace, or None when it has none."""

    @abstractmethod
    def mark_interrupted(self) -> int:
        """Move every queued or running job to interrupted. Returns how many moved."""

    @abstractmethod
    def fail_stale(self, before: str, error: str) -> int:
        """
        queued or running -> failed, storing the error, for every job that
        started before `before` (a utc_now() timestamp), or was created before
        it if still queued. Returns how many moved (#95).
        """
