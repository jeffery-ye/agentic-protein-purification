"""An in-process job repository for tests. Nothing survives the process."""

from dataclasses import replace
from threading import Lock
from typing import Any

from .repository import (
    ACTIVE_STATES,
    INTERRUPTED_ERROR,
    DeleteOutcome,
    JobCapReached,
    JobCaps,
    JobRecord,
    JobRepository,
    JobState,
    JobSummary,
    Owner,
    ProgressEntry,
    summarize,
    utc_day,
    utc_now,
)


class InMemoryJobRepository(JobRepository):
    def __init__(self):
        self._jobs: dict[str, JobRecord] = {}
        self._history: dict[str, list[ProgressEntry]] = {}
        self._traces: dict[str, dict[str, Any]] = {}
        self._created_per_day: dict[str, int] = {}
        self._lock = Lock()

    def create(
        self,
        job_id: str,
        inputs: dict[str, Any],
        owner: Owner,
        model: str | None,
        app_version: str | None,
        caps: JobCaps | None = None,
    ) -> JobRecord:
        job = JobRecord(
            id=job_id,
            owner=owner,
            inputs=dict(inputs),
            state="queued",
            progress=None,
            result=None,
            error=None,
            model=model,
            app_version=app_version,
            created_at=utc_now(),
            started_at=None,
            finished_at=None,
        )
        day = utc_day(job.created_at)
        with self._lock:
            if job_id in self._jobs:
                raise ValueError(f"Job {job_id} already exists")
            if caps is not None and caps.max_active is not None:
                active = sum(j.state in ACTIVE_STATES for j in self._jobs.values())
                if active >= caps.max_active:
                    raise JobCapReached("active", caps.max_active)
            if caps is not None and caps.max_per_day is not None:
                if self._created_per_day.get(day, 0) >= caps.max_per_day:
                    raise JobCapReached("daily", caps.max_per_day)
            self._jobs[job_id] = job
            self._history[job_id] = []
            self._created_per_day[day] = self._created_per_day.get(day, 0) + 1
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def history(self, job_id: str) -> list[ProgressEntry]:
        with self._lock:
            return list(self._history.get(job_id, []))

    def list_by_owner(self, owner_sub: str) -> list[JobSummary]:
        with self._lock:
            # Newest insertion first, so created_at ties break the way the
            # SQLite store's rowid does; sort() is stable.
            jobs = [job for job in reversed(self._jobs.values()) if job.owner.sub == owner_sub]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return [summarize(job) for job in jobs]

    def delete(self, job_id: str, owner_sub: str) -> DeleteOutcome:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner.sub != owner_sub:
                return "not_found"
            if job.state in ACTIVE_STATES:
                return "active"
            del self._jobs[job_id]
            del self._history[job_id]
            self._traces.pop(job_id, None)
            return "deleted"

    def rename(self, job_id: str, owner_sub: str, name: str | None) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner.sub != owner_sub:
                return False
            self._jobs[job_id] = replace(job, name=name)
            return True

    def _transition(self, job_id: str, allowed: tuple[JobState, ...], **changes) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state not in allowed:
                return False
            self._jobs[job_id] = replace(job, **changes)
            return True

    def mark_running(self, job_id: str) -> bool:
        return self._transition(job_id, ("queued",), state="running", started_at=utc_now())

    def record_progress(self, job_id: str, message: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != "running":
                return False
            self._jobs[job_id] = replace(job, progress=message)
            self._history[job_id].append(ProgressEntry(at=utc_now(), message=message))
            return True

    def complete(self, job_id: str, result: dict[str, Any]) -> bool:
        return self._transition(
            job_id, ("running",), state="completed", result=result, finished_at=utc_now()
        )

    def fail(self, job_id: str, error: str) -> bool:
        return self._transition(
            job_id, ACTIVE_STATES, state="failed", error=error, finished_at=utc_now()
        )

    def save_trace(self, job_id: str, trace: dict[str, Any]) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            self._traces[job_id] = trace
            return True

    def get_trace(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._traces.get(job_id)

    def mark_interrupted(self) -> int:
        with self._lock:
            active = [job for job in self._jobs.values() if job.state in ACTIVE_STATES]
            now = utc_now()
            for job in active:
                self._jobs[job.id] = replace(
                    job, state="interrupted", error=INTERRUPTED_ERROR, finished_at=now
                )
            return len(active)

    def fail_stale(self, before: str, error: str) -> int:
        with self._lock:
            stale = [
                job
                for job in self._jobs.values()
                if job.state in ACTIVE_STATES and (job.started_at or job.created_at) < before
            ]
            now = utc_now()
            for job in stale:
                self._jobs[job.id] = replace(job, state="failed", error=error, finished_at=now)
            return len(stale)
