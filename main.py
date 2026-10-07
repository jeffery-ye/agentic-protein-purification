import asyncio
import json
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agent_engine import llm, settings
from agent_engine.agents.agent_body import AgentResult, ProteinPurificationAgent
from agent_engine.auth import Authenticator, User
from agent_engine.job_store import (
    JobCapReached,
    JobCaps,
    JobRecord,
    JobRepository,
    JobSummary,
    Owner,
    SqliteJobRepository,
)
from agent_engine.trace import Trace
from schemas import (
    BulkDeleteRequest,
    BulkDeleteResult,
    JobListItem,
    ProtocolResult,
    PurificationRequest,
    RenameJobRequest,
    SkippedJob,
)

# --- Job store ---------------------------------------------------------------

_job_repo: JobRepository | None = None
_job_repo_lock = Lock()


def get_job_repo() -> JobRepository:
    """
    The process's job repository, opened on first use.

    Opening it applies migrations and marks jobs a previous process left queued
    or running as interrupted. No job of this process can exist yet at that
    point, so everything still active belongs to a dead process. The lifespan
    hook calls this at startup; tests replace `_job_repo` with an in-memory one.
    """
    global _job_repo
    with _job_repo_lock:
        if _job_repo is None:
            repo = SqliteJobRepository(settings.job_db_path())
            interrupted = repo.mark_interrupted()
            if interrupted:
                print(f"--- [JobStore] Marked {interrupted} unfinished job(s) as interrupted ---")
            _job_repo = repo
        return _job_repo


# --- Identity ----------------------------------------------------------------

# Validated at import so an unknown APP_ENV, or a missing or half-applied
# sign-in configuration, stops the server at startup.
APP_ENV = settings.app_env()
AUTH = Authenticator(APP_ENV, settings.cognito_config())

# The spending guards (#26), global because every account is admin-created.
# Read at import like the above, so deploy without DAILY_JOB_CAP won't start.
JOB_CAPS = JobCaps(max_active=settings.max_active_jobs(), max_per_day=settings.daily_job_cap())


def current_user(request: Request) -> User:
    """
    The signed-in user: dev@local in dev, the shared placeholder in deploy
    without sign-in, otherwise the session's user or a 401.
    """
    return AUTH.current_user(request)


def request_owner(user: User = Depends(current_user)) -> Owner:
    """Who is submitting the request, as stamped on the job."""
    return user.owner


def owned_job(repo: JobRepository, job_id: str, user: User) -> JobRecord:
    """The job if it belongs to `user`. Anyone else's is a 404, as if it didn't exist."""
    job = repo.get(job_id)
    if job is None or job.owner.sub != user.sub:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# How much of the input My Jobs shows.
INPUT_SUMMARY_LENGTH = 80


def input_summary(inputs: dict) -> str:
    """The protein name or ID as submitted, or a FASTA input's header line."""
    text = str(inputs.get("fasta_id") or "").strip()
    if ">" in text:
        rest = text[text.index(">") + 1 :]
        text = (rest.splitlines()[0].strip() if rest else "") or "FASTA sequence"
    elif text:
        text = text.splitlines()[0].strip()
    if len(text) > INPUT_SUMMARY_LENGTH:
        text = text[: INPUT_SUMMARY_LENGTH - 1].rstrip() + "\u2026"
    return text


def runtime_seconds(job: JobSummary, now: datetime | None = None) -> float | None:
    """Start to finish, start to now while running, or None if it never started."""
    if job.started_at is None:
        return None
    start = datetime.fromisoformat(job.started_at)
    if job.finished_at is not None:
        end = datetime.fromisoformat(job.finished_at)
    else:
        end = now or datetime.now(timezone.utc)
    return round(max((end - start).total_seconds(), 0.0), 1)


def job_list_item(job: JobSummary) -> JobListItem:
    return JobListItem(
        id=job.id,
        name=job.name,
        input_summary=input_summary(job.inputs),
        state=job.state,
        progress=job.progress,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        runtime_seconds=runtime_seconds(job),
    )


STOPPED_STATES = ("failed", "interrupted")

# What a job that crashed shows the user. The exception itself can carry server
# paths or provider responses, so it goes to the log instead.
UNEXPECTED_ERROR = (
    "The analysis stopped because of an unexpected server error. Please try again; "
    "if it keeps happening, contact the site owner."
)


def cap_message(e: JobCapReached) -> str:
    """What a user sees when a job cap (#26) turns their job away."""
    if e.cap == "active":
        jobs = "job" if e.limit == 1 else "jobs"
        return (
            f"The service is already running {e.limit} {jobs}, its limit at once. "
            "Please try again in a few minutes."
        )
    return (
        f"The service has reached its limit of {e.limit} jobs for today (UTC). "
        "Please try again tomorrow."
    )


def save_trace(repo: JobRepository, job_id: str, trace: Trace) -> None:
    """Best effort, like progress: a store hiccup must not fail the job."""
    try:
        repo.save_trace(job_id, trace.model_dump(mode="json"))
    except Exception as e:
        print(f"   [JobStore] Could not store the trace for {job_id}: {e}")


# A job's final write is retried this many times, waiting 0.5 s, then 1 s, so a
# SQLite lock held past its busy timeout doesn't cost a finished job (#95).
FINAL_WRITE_ATTEMPTS = 3
FINAL_WRITE_BACKOFF_SECONDS = 0.5


def _with_retries(write: Callable[[], bool], what: str) -> bool:
    """Run a final write, retrying with backoff. Raises the last attempt's error."""
    for attempt in range(1, FINAL_WRITE_ATTEMPTS + 1):
        try:
            return write()
        except Exception as e:
            if attempt == FINAL_WRITE_ATTEMPTS:
                raise
            print(f"   [JobStore] Could not {what}, attempt {attempt}/{FINAL_WRITE_ATTEMPTS}: {e}")
            time.sleep(FINAL_WRITE_BACKOFF_SECONDS * 2 ** (attempt - 1))
    return False


def fail_job(repo: JobRepository, job_id: str, error: str) -> None:
    """
    Record the job's failure. If the store still refuses, the job stays running
    until the reaper fails it at its deadline, which frees its active-cap slot.
    """
    try:
        _with_retries(lambda: repo.fail(job_id, error), f"record the failure of {job_id}")
    except Exception:
        print(f"   [JobStore] Could not record the failure of {job_id}; the reaper will fail it")
        traceback.print_exc()


def complete_job(repo: JobRepository, job_id: str, result: dict) -> None:
    """
    Store the job's result. One the store won't take goes to the log, so the
    paid-for work can still be recovered, and the job fails.
    """
    try:
        stored = _with_retries(
            lambda: repo.complete(job_id, result), f"store the result of {job_id}"
        )
    except Exception:
        print(f"   [JobStore] Could not store the result of {job_id}; logging it below")
        traceback.print_exc()
        print(f"   [JobStore] Result of {job_id}: {json.dumps(result)}")
        fail_job(repo, job_id, UNEXPECTED_ERROR)
        return
    if not stored:
        # No longer running: the reaper failed it at its deadline first.
        print(f"   [JobStore] {job_id} was no longer running; logging its result below")
        print(f"   [JobStore] Result of {job_id}: {json.dumps(result)}")


# --- Reaper (#95) ------------------------------------------------------------

# How often the reaper looks for jobs past their deadline.
REAP_INTERVAL_SECONDS = 60


def deadline_error(minutes: int) -> str:
    """What a job the reaper failed shows the user."""
    return (
        f"The analysis did not finish within {minutes} minutes, so it was marked as failed. "
        "Please try again; if it keeps happening, contact the site owner."
    )


def reap_stale_jobs(repo: JobRepository, now: datetime | None = None) -> int:
    """
    Fail every job queued or running for longer than JOB_DEADLINE_MINUTES, so a
    job stuck on a hung call stops holding an active-cap slot. Its thread can't
    be stopped and runs on, but its late result can no longer overwrite the
    failure. Returns how many jobs it failed.
    """
    minutes = settings.job_deadline_minutes()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    reaped = repo.fail_stale(cutoff.isoformat(timespec="milliseconds"), deadline_error(minutes))
    if reaped:
        print(f"--- [JobStore] Failed {reaped} job(s) past the {minutes}-minute deadline ---")
    return reaped


async def reap_periodically(repo: JobRepository) -> None:
    """The lifespan's background sweep. A store error is logged and retried next time."""
    while True:
        try:
            # In a thread, since SQLite's busy timeout can block for seconds.
            await asyncio.to_thread(reap_stale_jobs, repo)
        except Exception:
            print("   [JobStore] Reaping stale jobs failed")
            traceback.print_exc()
        await asyncio.sleep(REAP_INTERVAL_SECONDS)


def run_agent_task(repo: JobRepository, job_id: str, request: PurificationRequest):
    """
    Wraps the synchronous agent in a function compatible with FastAPI BackgroundTasks.
    """

    def status_callback(message: str):
        # Progress is best effort: a store hiccup must not fail the pipeline.
        try:
            repo.record_progress(job_id, message)
        except Exception as e:
            print(f"   [JobStore] Could not record progress for {job_id}: {e}")

    # Created here, not in the pipeline, so a crash still leaves the steps it
    # recorded to store (#77).
    trace = Trace(model=llm.configured_model_name())

    try:
        repo.mark_running(job_id)
        status_callback("Initializing Agent...")

        agent = ProteinPurificationAgent()

        result: AgentResult = agent.run(
            protein_name=request.fasta_id,
            min_pident=request.min_percent_identity,
            min_qcov=request.min_query_coverage,
            max_evalue=request.max_evalue,
            max_hits=request.max_hits,
            max_protocols=request.max_protocols,
            failed_purification_text=request.failed_purification_text,
            status_callback=status_callback,
            trace=trace,
        )

        # Stored before the job finishes, so the Job Info tab never opens on a
        # finished job without it.
        save_trace(repo, job_id, trace)

        if not result.success:
            fail_job(repo, job_id, result.error_message or "Unknown error")
            return

        structured_result = ProtocolResult(
            purifications=result.purifications,
            comprehensive_protocol=result.comprehensive_protocol,
            raw_plan=result.raw_plan,
            blast_results=result.similar_proteins,
            papers=result.papers,
            error_message=result.synthesis_error,
            synthesis_skipped=result.synthesis_skipped,
        ).model_dump(mode="json")

    except Exception:
        print(f"--- [Agent] Job {job_id} crashed ---")
        traceback.print_exc()
        save_trace(repo, job_id, trace)
        fail_job(repo, job_id, UNEXPECTED_ERROR)
        return

    complete_job(repo, job_id, structured_result)


# --- App ---------------------------------------------------------------------

# The built frontend. In deploy mode FastAPI serves it from the same origin; in
# dev the Vite dev server serves it instead.
FRONTEND_DIST = Path(
    os.getenv("FRONTEND_DIST", Path(__file__).parent / "purification-rescue-frontend" / "dist")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the store at boot, so a bad DATA_DIR or migration stops the server
    # and interrupted jobs are marked before the first request. A missing NCBI
    # contact address stops it too, rather than failing the first job. The
    # reaper then fails, once a minute, any job past its deadline (#95).
    settings.entrez_email()
    settings.job_deadline_minutes()
    reaper = asyncio.create_task(reap_periodically(get_job_repo()))
    yield
    reaper.cancel()
    with suppress(asyncio.CancelledError):
        await reaper


app = FastAPI(title="Purification Rescue Agent", lifespan=lifespan)

# The session middleware (when sign-in is on) and /auth/*, which must be real
# paths ahead of the StaticFiles mount because Cognito redirects to them.
AUTH.install(app)

# In deploy mode the frontend shares the API's origin, so no CORS is needed.
# dev allows only the Vite origins, so other pages can't drive the local API.
if APP_ENV == "dev":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.dev_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health():
    """Liveness check for the uptime monitor. Depends on nothing external."""
    return {"status": "ok"}


# The job endpoints below are plain functions, which FastAPI runs in its
# threadpool: each opens SQLite, whose busy timeout can wait for seconds and
# must never stall the event loop (and /health with it).


@app.post("/analyze", response_model=dict)
def start_analysis(
    request: PurificationRequest,
    background_tasks: BackgroundTasks,
    repo: JobRepository = Depends(get_job_repo),
    owner: Owner = Depends(request_owner),
):
    """
    Starts the agent in the background and returns a Job ID immediately.
    """
    job_id = str(uuid.uuid4())

    # Recorded before the task is scheduled, so a /status call straight after
    # this response finds the job rather than a 404, and so the caps count it
    # from this moment.
    try:
        repo.create(
            job_id,
            inputs=request.model_dump(mode="json"),
            owner=owner,
            model=llm.configured_model_name(),
            app_version=settings.app_version(),
            caps=JOB_CAPS,
        )
    except JobCapReached as e:
        raise HTTPException(status_code=429, detail=cap_message(e)) from None

    background_tasks.add_task(run_agent_task, repo, job_id, request)

    return {"job_id": job_id, "state": "queued"}


@app.get("/status/{job_id}")
def get_status(
    job_id: str,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    Polled by the frontend to check progress.

    `state` is the job's lifecycle state and `progress` its latest message;
    `history` replays every message so far. `error` is set once the job has
    failed or been interrupted. Another user's job is a 404.
    """
    job = owned_job(repo, job_id, user)

    response = {
        "job_id": job_id,
        "state": job.state,
        "progress": job.progress,
        "history": [{"at": e.at, "message": e.message} for e in repo.history(job_id)],
        "inputs": job.inputs,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }

    if job.state in STOPPED_STATES:
        response["error"] = job.error

    return response


@app.get("/result/{job_id}", response_model=ProtocolResult)
def get_result(
    job_id: str,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    Retrieves the final detailed report.

    A stored result that no longer fits ProtocolResult (its shape changed after
    the job ran) is returned as stored; the frontend decides whether it can
    render it. Another user's job is a 404.
    """
    job = owned_job(repo, job_id, user)

    if job.state in STOPPED_STATES:
        raise HTTPException(status_code=400, detail=f"Job failed: {job.error}")

    if job.state != "completed":
        raise HTTPException(status_code=202, detail="Result not ready yet")

    try:
        return ProtocolResult.model_validate(job.result)
    except ValidationError:
        print(f"   [JobStore] Stored result for {job_id} no longer matches ProtocolResult")
        return JSONResponse(content=job.result)


@app.get("/trace/{job_id}", response_model=Optional[Trace])
def get_trace(
    job_id: str,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    The job's trace (#77): each LLM call's reasoning summary, output, tokens,
    time and estimated cost, and each stage's time. Kept out of the report so
    /result stays small. null for a job with no trace: one that ran before
    traces existed, or is still running. Another user's job is a 404.
    """
    owned_job(repo, job_id, user)
    stored = repo.get_trace(job_id)
    if stored is None:
        return None
    try:
        return Trace.model_validate(stored)
    except ValidationError:
        print(f"   [JobStore] Stored trace for {job_id} no longer matches Trace")
        return None


@app.get("/jobs", response_model=list[JobListItem])
def list_jobs(
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """The current user's jobs, newest first (My Jobs)."""
    return [job_list_item(job) for job in repo.list_by_owner(user.sub)]


@app.patch("/jobs/{job_id}", response_model=JobListItem)
def rename_job(
    job_id: str,
    request: RenameJobRequest,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    Rename one of the user's jobs, running or not. A blank name clears it, so
    My Jobs shows the input summary again. Another user's job is a 404.
    """
    name = (request.name or "").strip() or None
    if not repo.rename(job_id, user.sub, name):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_list_item(owned_job(repo, job_id, user))


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    Hard-delete one of the user's finished, failed or interrupted jobs. Another
    user's job is a 404; a queued or running one is a 409, since jobs can't be
    cancelled (#49).
    """
    outcome = repo.delete(job_id, user.sub)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    if outcome == "active":
        raise HTTPException(status_code=409, detail="A running job can't be deleted")
    return Response(status_code=204)


@app.post("/jobs/delete", response_model=BulkDeleteResult)
def delete_jobs(
    request: BulkDeleteRequest,
    repo: JobRepository = Depends(get_job_repo),
    user: User = Depends(current_user),
):
    """
    Delete several of the user's jobs. Running jobs and ones that aren't the
    user's are skipped and listed with the reason, rather than failing the rest.
    """
    deleted: list[str] = []
    skipped: list[SkippedJob] = []
    for job_id in dict.fromkeys(request.ids):
        outcome = repo.delete(job_id, user.sub)
        if outcome == "deleted":
            deleted.append(job_id)
        else:
            reason = "running" if outcome == "active" else "not_found"
            skipped.append(SkippedJob(id=job_id, reason=reason))
    return BulkDeleteResult(deleted=deleted, skipped=skipped)


# Mounted last so the API routes above, /docs and /openapi.json take precedence.
# The frontend uses hash routing, so no SPA fallback route is needed.
if APP_ENV == "deploy":
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
