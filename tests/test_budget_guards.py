"""
Tests for the spending guards (#26): the job caps in both repositories, their
settings, and what /analyze returns when a cap or an input bound turns a
request away.
"""

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

import main
from agent_engine import settings
from agent_engine.job_store import (
    InMemoryJobRepository,
    JobCapReached,
    JobCaps,
    Owner,
    SqliteJobRepository,
)
from agent_engine.job_store.repository import utc_now
from agent_engine.job_store.sqlite import apply_migrations, load_migrations
from schemas import (
    MAX_FAILED_PURIFICATION_LENGTH,
    MAX_FASTA_LENGTH,
    MAX_HITS,
    MAX_PROTOCOLS,
    PurificationRequest,
)

INPUTS = {"fasta_id": "P9WQA3"}
OWNER = Owner(sub="user-sub", username="alice")


@pytest.fixture(params=["sqlite", "memory"])
def repo(request, tmp_path):
    if request.param == "sqlite":
        return SqliteJobRepository(tmp_path / "jobs.sqlite3")
    return InMemoryJobRepository()


def create(repo, job_id, caps=None):
    return repo.create(job_id, INPUTS, OWNER, model=None, app_version=None, caps=caps)


# --- Caps in the repositories ------------------------------------------------


def test_the_active_cap_counts_queued_and_running_jobs(repo):
    caps = JobCaps(max_active=2)
    create(repo, "a", caps)
    create(repo, "b", caps)
    repo.mark_running("a")

    with pytest.raises(JobCapReached) as refused:
        create(repo, "c", caps)

    assert (refused.value.cap, refused.value.limit) == ("active", 2)
    assert repo.get("c") is None


@pytest.mark.parametrize("finish", ["complete", "fail"])
def test_a_finished_job_frees_its_active_slot(repo, finish):
    caps = JobCaps(max_active=1)
    create(repo, "a", caps)
    repo.mark_running("a")
    if finish == "complete":
        repo.complete("a", {})
    else:
        repo.fail("a", "boom")

    create(repo, "b", caps)

    assert repo.get("b").state == "queued"


def test_the_daily_cap_counts_deleted_jobs(repo):
    caps = JobCaps(max_per_day=2)
    for job_id in ["a", "b"]:
        create(repo, job_id, caps)
        repo.mark_running(job_id)
        repo.complete(job_id, {})
        assert repo.delete(job_id, OWNER.sub) == "deleted"

    with pytest.raises(JobCapReached) as refused:
        create(repo, "c", caps)

    assert (refused.value.cap, refused.value.limit) == ("daily", 2)


def test_jobs_created_without_caps_still_count_toward_the_day(repo):
    create(repo, "a")

    with pytest.raises(JobCapReached):
        create(repo, "b", JobCaps(max_per_day=1))


def test_no_caps_means_no_limit(repo):
    for i in range(5):
        create(repo, f"job-{i}", JobCaps())


def test_a_burst_never_gets_past_the_active_cap(repo):
    caps = JobCaps(max_active=2)
    refused = []
    start = threading.Barrier(10)

    def submit(job_id):
        start.wait()
        try:
            create(repo, job_id, caps)
        except JobCapReached:
            refused.append(job_id)

    threads = [threading.Thread(target=submit, args=(f"job-{i}",)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(refused) == 8
    assert sum(repo.get(f"job-{i}") is not None for i in range(10)) == 2


def test_the_migration_counts_the_jobs_already_created_today(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first, *_ = load_migrations()
    with sqlite3.connect(path, isolation_level=None) as conn:
        apply_migrations(conn, [first])
        conn.execute(
            "INSERT INTO jobs (id, inputs, state, created_at) VALUES (?, '{}', 'completed', ?)",
            ("old", utc_now()),
        )

    repo = SqliteJobRepository(path)

    with pytest.raises(JobCapReached):
        create(repo, "new", JobCaps(max_per_day=1))


# --- Settings ----------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    for name in ["APP_ENV", "DAILY_JOB_CAP", "MAX_ACTIVE_JOBS"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_dev_has_no_daily_cap_but_two_active_jobs_by_default(env):
    assert (settings.daily_job_cap(), settings.max_active_jobs()) == (None, 2)


def test_deploy_requires_a_daily_cap(env):
    env.setenv("APP_ENV", "deploy")

    with pytest.raises(ValueError, match="DAILY_JOB_CAP must be set"):
        settings.daily_job_cap()


def test_deploy_defaults_to_two_active_jobs(env):
    env.setenv("APP_ENV", "deploy")

    assert settings.max_active_jobs() == 2


def test_the_caps_are_read_from_the_environment(env):
    env.setenv("APP_ENV", "deploy")
    env.setenv("DAILY_JOB_CAP", "20")
    env.setenv("MAX_ACTIVE_JOBS", "1")

    assert (settings.daily_job_cap(), settings.max_active_jobs()) == (20, 1)


@pytest.mark.parametrize("value", ["0", "-3", "ten", "2.5"])
def test_a_cap_must_be_a_positive_whole_number(env, value):
    env.setenv("DAILY_JOB_CAP", value)

    with pytest.raises(ValueError, match="DAILY_JOB_CAP"):
        settings.daily_job_cap()


# --- /analyze ----------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_job_repo", InMemoryJobRepository())
    # Jobs stay queued, so they keep holding their active slot.
    monkeypatch.setattr(main, "run_agent_task", lambda repo, job_id, request: None)
    return TestClient(main.app)


def test_analyze_past_the_active_cap_is_a_429_with_a_clear_message(client, monkeypatch):
    monkeypatch.setattr(main, "JOB_CAPS", JobCaps(max_active=1))
    assert client.post("/analyze", json=INPUTS).status_code == 200

    response = client.post("/analyze", json=INPUTS)

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "The service is already running 1 job, its limit at once. "
        "Please try again in a few minutes."
    )


def test_analyze_past_the_daily_cap_is_a_429_with_a_clear_message(client, monkeypatch):
    monkeypatch.setattr(main, "JOB_CAPS", JobCaps(max_per_day=1))
    assert client.post("/analyze", json=INPUTS).status_code == 200

    response = client.post("/analyze", json=INPUTS)

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "The service has reached its limit of 1 jobs for today (UTC). Please try again tomorrow."
    )


@pytest.mark.parametrize(
    "request_body",
    [
        {"fasta_id": ""},
        {"fasta_id": "A" * (MAX_FASTA_LENGTH + 1)},
        {
            "fasta_id": "P9WQA3",
            "failed_purification_text": "x" * (MAX_FAILED_PURIFICATION_LENGTH + 1),
        },
        {"fasta_id": "P9WQA3", "max_hits": MAX_HITS + 1},
        {"fasta_id": "P9WQA3", "max_evalue": 0},
        {"fasta_id": "P9WQA3", "max_protocols": MAX_PROTOCOLS + 1},
        {"fasta_id": "P9WQA3", "max_protocols": 0},
    ],
    ids=[
        "empty input",
        "long FASTA",
        "long failed text",
        "too many hits",
        "zero e-value",
        "too many protocols",
        "no protocols",
    ],
)
def test_out_of_bounds_input_is_rejected(client, request_body):
    response = client.post("/analyze", json=request_body)

    assert response.status_code == 422


def test_input_at_the_bounds_is_accepted(client):
    response = client.post(
        "/analyze",
        json={
            "fasta_id": "A" * MAX_FASTA_LENGTH,
            "failed_purification_text": "x" * MAX_FAILED_PURIFICATION_LENGTH,
            "max_hits": MAX_HITS,
            "max_protocols": MAX_PROTOCOLS,
        },
    )

    assert response.status_code == 200


def test_the_search_stops_at_three_protocols_by_default():
    assert PurificationRequest(fasta_id="P9WQA3").max_protocols == 3
    # Inputs stored before the field existed still validate.
    assert (
        PurificationRequest.model_validate({"fasta_id": "P9WQA3", "max_hits": 10}).max_protocols
        == 3
    )
