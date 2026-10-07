"""
Tests for the job endpoints and the job lifecycle in main.py (#24).

Most tests use the in-memory repository and drive its state directly. The
pipeline is replaced by a stub agent, so a job runs synchronously inside
TestClient's background-task handling without touching the network or an LLM.
The restart and interrupted tests use SQLite files under tmp.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from agent_engine import settings
from agent_engine.agents.agent_body import AgentResult
from agent_engine.auth import DEV_USER
from agent_engine.job_store import InMemoryJobRepository, JobCaps, SqliteJobRepository

REQUEST = {"fasta_id": "P9WQA3"}


class StubAgent:
    """Reports two progress messages, then returns `outcome`."""

    outcome = AgentResult(success=True, raw_plan="plan", comprehensive_protocol="protocol")

    def run(self, status_callback, **_kwargs):
        status_callback("Running BLAST (Strict: 90.0% Cov)...")
        status_callback("Synthesizing final protocol with LLM...")
        return self.outcome


@pytest.fixture
def repo(monkeypatch):
    repo = InMemoryJobRepository()
    monkeypatch.setattr(main, "_job_repo", repo)
    return repo


@pytest.fixture
def client(repo, monkeypatch):
    """A client whose jobs stay queued: the background task is a no-op."""
    monkeypatch.setattr(main, "run_agent_task", lambda repo, job_id, request: None)
    return TestClient(main.app)


@pytest.fixture
def running_client(repo, monkeypatch):
    """A client whose jobs run to the end through the stub agent."""
    monkeypatch.setattr(main, "ProteinPurificationAgent", StubAgent)
    return TestClient(main.app)


def submit(client) -> str:
    return client.post("/analyze", json=REQUEST).json()["job_id"]


# --- /analyze and /status ----------------------------------------------------


def test_status_straight_after_analyze_is_queued(client):
    response = client.post("/analyze", json=REQUEST)
    job_id = response.json()["job_id"]

    status = client.get(f"/status/{job_id}")

    assert response.json()["state"] == "queued"
    assert status.status_code == 200
    body = status.json()
    assert {k: body[k] for k in ["job_id", "state", "progress", "history"]} == {
        "job_id": job_id,
        "state": "queued",
        "progress": None,
        "history": [],
    }
    assert "error" not in body


def test_status_includes_the_inputs(client):
    job_id = submit(client)

    assert client.get(f"/status/{job_id}").json()["inputs"]["fasta_id"] == "P9WQA3"


def test_analyze_records_the_model_and_app_version(client, repo, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "0123abc")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.5")

    job = repo.get(submit(client))

    assert (job.model, job.app_version, job.owner) == ("openai:gpt-5.5", "0123abc", DEV_USER.owner)


def test_a_running_job_reports_its_latest_progress_and_history(client, repo):
    job_id = submit(client)
    repo.mark_running(job_id)
    repo.record_progress(job_id, "Initializing Agent...")
    repo.record_progress(job_id, "Running BLAST (Strict: 90.0% Cov)...")

    body = client.get(f"/status/{job_id}").json()

    assert (body["state"], body["progress"]) == (
        "running",
        "Running BLAST (Strict: 90.0% Cov)...",
    )
    assert "status" not in body
    assert [e["message"] for e in body["history"]] == [
        "Initializing Agent...",
        "Running BLAST (Strict: 90.0% Cov)...",
    ]


def test_unknown_job_is_not_found(client):
    assert client.get("/status/no-such-job").status_code == 404


# --- /result -----------------------------------------------------------------


def test_result_of_a_queued_job_is_not_ready(client):
    assert client.get(f"/result/{submit(client)}").status_code == 202


def test_result_of_a_running_job_is_not_ready(client, repo):
    job_id = submit(client)
    repo.mark_running(job_id)

    assert client.get(f"/result/{job_id}").status_code == 202


def test_result_of_an_unknown_job_is_not_found(client):
    assert client.get("/result/no-such-job").status_code == 404


def test_result_of_a_failed_job_is_a_bad_request(client, repo):
    job_id = submit(client)
    repo.fail(job_id, "BLAST failed")

    response = client.get(f"/result/{job_id}")

    assert response.status_code == 400
    assert "BLAST failed" in response.json()["detail"]


def test_a_stored_result_that_no_longer_validates_is_returned_as_stored(client, repo):
    """An older result shape must not turn into a 500."""
    job_id = submit(client)
    repo.mark_running(job_id)
    repo.complete(job_id, {"purifications": "an older shape"})

    response = client.get(f"/result/{job_id}")

    assert response.status_code == 200
    assert response.json() == {"purifications": "an older shape"}


# --- The job lifecycle -------------------------------------------------------


def test_a_successful_run_completes_with_its_result_and_full_history(running_client):
    job_id = submit(running_client)

    status = running_client.get(f"/status/{job_id}").json()
    result = running_client.get(f"/result/{job_id}")

    assert status["state"] == "completed"
    assert [e["message"] for e in status["history"]] == [
        "Initializing Agent...",
        "Running BLAST (Strict: 90.0% Cov)...",
        "Synthesizing final protocol with LLM...",
    ]
    assert result.status_code == 200
    assert result.json()["comprehensive_protocol"] == "protocol"


def test_an_unsuccessful_run_fails_with_its_error(running_client, monkeypatch):
    monkeypatch.setattr(
        StubAgent, "outcome", AgentResult(success=False, error_message="No BLAST results found")
    )
    job_id = submit(running_client)

    body = running_client.get(f"/status/{job_id}").json()

    assert (body["state"], body["error"]) == ("failed", "No BLAST results found")


def test_an_exception_in_the_pipeline_fails_the_job(running_client, monkeypatch):
    def explode(self, status_callback, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(StubAgent, "run", explode)
    job_id = submit(running_client)

    body = running_client.get(f"/status/{job_id}").json()

    assert (body["state"], body["error"]) == ("failed", main.UNEXPECTED_ERROR)
    assert "boom" not in body["error"]


def test_a_progress_write_failure_does_not_fail_the_job(running_client, repo, monkeypatch):
    def broken(job_id, message):
        raise OSError("disk full")

    monkeypatch.setattr(repo, "record_progress", broken)
    job_id = submit(running_client)

    assert running_client.get(f"/status/{job_id}").json()["state"] == "completed"


# --- Final writes and the reaper (#95) ---------------------------------------


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(main, "FINAL_WRITE_BACKOFF_SECONDS", 0)


def flaky(write, failures: int):
    """`write`, raising a locked-database error for its first `failures` calls."""
    calls = []

    def wrapped(*args):
        calls.append(args)
        if len(calls) <= failures:
            raise sqlite3.OperationalError("database is locked")
        return write(*args)

    return wrapped


def later(minutes: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def test_a_result_write_that_fails_briefly_is_retried(
    running_client, repo, monkeypatch, no_backoff
):
    monkeypatch.setattr(repo, "complete", flaky(repo.complete, failures=2))
    job_id = submit(running_client)

    assert running_client.get(f"/status/{job_id}").json()["state"] == "completed"


def test_a_result_the_store_refuses_is_logged_and_the_job_fails(
    running_client, repo, monkeypatch, no_backoff, capsys
):
    monkeypatch.setattr(repo, "complete", flaky(repo.complete, failures=99))
    job_id = submit(running_client)

    body = running_client.get(f"/status/{job_id}").json()

    assert (body["state"], body["error"]) == ("failed", main.UNEXPECTED_ERROR)
    assert '"comprehensive_protocol": "protocol"' in capsys.readouterr().out


def test_a_failure_write_that_fails_briefly_is_retried(
    running_client, repo, monkeypatch, no_backoff
):
    monkeypatch.setattr(
        StubAgent, "outcome", AgentResult(success=False, error_message="No BLAST results found")
    )
    monkeypatch.setattr(repo, "fail", flaky(repo.fail, failures=2))
    job_id = submit(running_client)

    assert running_client.get(f"/status/{job_id}").json()["state"] == "failed"


def test_a_failure_write_that_keeps_failing_leaves_the_job_to_the_reaper(
    running_client, repo, monkeypatch, no_backoff
):
    def explode(self, status_callback, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(StubAgent, "run", explode)
    monkeypatch.setattr(repo, "fail", flaky(repo.fail, failures=99))
    job_id = submit(running_client)

    main.reap_stale_jobs(repo, now=later(settings.DEFAULT_JOB_DEADLINE_MINUTES + 1))

    assert running_client.get(f"/status/{job_id}").json()["state"] == "failed"


def test_a_job_past_its_deadline_frees_its_active_slot(client, repo, monkeypatch):
    monkeypatch.setattr(main, "JOB_CAPS", JobCaps(max_active=1))
    job_id = submit(client)
    assert client.post("/analyze", json=REQUEST).status_code == 429

    assert main.reap_stale_jobs(repo, now=later(settings.DEFAULT_JOB_DEADLINE_MINUTES + 1)) == 1

    body = client.get(f"/status/{job_id}").json()
    assert (body["state"], body["error"]) == ("failed", main.deadline_error(60))
    assert client.post("/analyze", json=REQUEST).status_code == 200


def test_a_job_within_its_deadline_is_left_alone(client, repo, monkeypatch):
    monkeypatch.setenv("JOB_DEADLINE_MINUTES", "5")
    job_id = submit(client)

    main.reap_stale_jobs(repo, now=later(4))

    assert client.get(f"/status/{job_id}").json()["state"] == "queued"


def test_the_reaper_keeps_sweeping_after_a_store_error(monkeypatch):
    calls = []

    def reap(repo):
        calls.append(repo)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        if len(calls) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "reap_stale_jobs", reap)
    monkeypatch.setattr(main, "REAP_INTERVAL_SECONDS", 0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.reap_periodically(InMemoryJobRepository()))
    assert len(calls) == 3


# --- Restarts (SQLite) -------------------------------------------------------


def test_a_completed_job_survives_a_restart(data_dir, monkeypatch):
    monkeypatch.setattr(main, "ProteinPurificationAgent", StubAgent)
    with TestClient(main.app) as client:
        job_id = submit(client)

    monkeypatch.setattr(main, "_job_repo", None)  # a new process
    with TestClient(main.app) as client:
        result = client.get(f"/result/{job_id}")

    assert result.status_code == 200
    assert result.json()["raw_plan"] == "plan"


def test_jobs_cut_off_by_a_restart_are_interrupted(data_dir):
    previous = SqliteJobRepository(data_dir / "jobs.sqlite3")
    previous.create("was-running", {"fasta_id": "P9WQA3"}, DEV_USER.owner, None, None)
    previous.mark_running("was-running")
    previous.record_progress("was-running", "Running BLAST...")

    with TestClient(main.app) as client:
        status = client.get("/status/was-running").json()
        result = client.get("/result/was-running")

    assert (status["state"], status["progress"]) == ("interrupted", "Running BLAST...")
    assert "restarted" in status["error"]
    assert result.status_code == 400
