"""
Tests for ownership and the My Jobs API in main.py (#25): GET /jobs,
PATCH /jobs/{id}, DELETE /jobs/{id} and POST /jobs/delete, plus the 404s on
/status and /result for another user's job.

The app runs in dev mode, where every request is dev@local. A second user is
swapped in through FastAPI's dependency_overrides on main.current_user.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from agent_engine.auth import DEV_USER, User
from agent_engine.job_store import InMemoryJobRepository, JobSummary, Owner

BOB = User(sub="bob-sub", username="bob", email="bob@example.org")


@pytest.fixture
def repo(monkeypatch):
    repo = InMemoryJobRepository()
    monkeypatch.setattr(main, "_job_repo", repo)
    monkeypatch.setattr(main, "run_agent_task", lambda repo, job_id, request: None)
    return repo


@pytest.fixture
def client(repo):
    return TestClient(main.app)


@pytest.fixture
def as_user():
    """Run the following requests as `user` instead of dev@local."""

    def _as(user: User):
        main.app.dependency_overrides[main.current_user] = lambda: user

    yield _as
    main.app.dependency_overrides.pop(main.current_user, None)


def add_job(repo, job_id, owner=DEV_USER.owner, state="completed", fasta_id="P9WQA3"):
    repo.create(job_id, {"fasta_id": fasta_id}, owner, None, None)
    if state in ("running", "completed"):
        repo.mark_running(job_id)
    if state == "completed":
        repo.complete(job_id, {"raw_plan": "plan"})
    if state == "failed":
        repo.fail(job_id, "BLAST failed")
    return job_id


# --- Ownership ---------------------------------------------------------------


def test_analyze_stamps_the_job_with_the_current_user(client, repo, as_user):
    as_user(BOB)

    job_id = client.post("/analyze", json={"fasta_id": "P9WQA3"}).json()["job_id"]

    assert repo.get(job_id).owner == Owner(sub="bob-sub", username="bob")


@pytest.mark.parametrize("path", ["/status/{id}", "/result/{id}", "/trace/{id}"])
def test_another_users_job_is_not_found(client, repo, as_user, path):
    job_id = add_job(repo, "bobs-job", owner=BOB.owner)

    response = client.get(path.format(id=job_id))

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_the_owner_can_read_their_job(client, repo, as_user):
    job_id = add_job(repo, "bobs-job", owner=BOB.owner)
    as_user(BOB)

    assert client.get(f"/status/{job_id}").status_code == 200
    assert client.get(f"/result/{job_id}").status_code == 200


def test_a_job_from_before_sign_in_is_nobodys(client, repo):
    add_job(repo, "unowned", owner=Owner())

    assert client.get("/status/unowned").status_code == 404


# --- GET /jobs ---------------------------------------------------------------


def test_jobs_lists_only_the_current_users_jobs_newest_first(client, repo):
    add_job(repo, "mine-1")
    add_job(repo, "bobs", owner=BOB.owner)
    add_job(repo, "mine-2", state="running")
    add_job(repo, "mine-3", state="queued")

    ids = [job["id"] for job in client.get("/jobs").json()]

    assert ids == ["mine-3", "mine-2", "mine-1"]


def test_a_listed_job_has_its_summary_state_progress_and_times(client, repo):
    add_job(repo, "running", state="running", fasta_id=">sp|P9WQA3|ABC_MYCTU Some protein\nMKV")
    repo.record_progress("running", "Running BLAST...")

    (job,) = client.get("/jobs").json()

    assert {k: job[k] for k in ["id", "input_summary", "state", "progress", "error"]} == {
        "id": "running",
        "input_summary": "sp|P9WQA3|ABC_MYCTU Some protein",
        "state": "running",
        "progress": "Running BLAST...",
        "error": None,
    }
    assert job["created_at"] and job["started_at"] and job["finished_at"] is None
    assert job["runtime_seconds"] >= 0


def test_a_listed_failed_job_carries_its_error(client, repo):
    add_job(repo, "failed", state="failed")

    (job,) = client.get("/jobs").json()

    assert (job["state"], job["error"], job["runtime_seconds"]) == ("failed", "BLAST failed", None)


def test_jobs_is_empty_for_a_new_user(client, repo, as_user):
    add_job(repo, "mine")
    as_user(BOB)

    assert client.get("/jobs").json() == []


@pytest.mark.parametrize(
    "fasta_id, expected",
    [
        ("P9WQA3", "P9WQA3"),
        ("  MytuD.00516.a  ", "MytuD.00516.a"),
        (">sp|P9WQA3 header\nMKVLAAGIVG\nKLLA", "sp|P9WQA3 header"),
        (">\nMKVLAAGIVG", "FASTA sequence"),
        ("MKVLAAGIVG\nKLLA", "MKVLAAGIVG"),
        ("", ""),
        ("x" * 200, "x" * 79 + "…"),
    ],
    ids=["accession", "ssgcid", "fasta", "fasta-no-header", "bare-sequence", "empty", "long"],
)
def test_the_input_summary(fasta_id, expected):
    assert main.input_summary({"fasta_id": fasta_id}) == expected


def summary(started_at, finished_at):
    return JobSummary(
        id="j",
        owner=Owner(),
        inputs={},
        state="completed",
        progress=None,
        error=None,
        created_at="2026-09-23T10:00:00.000+00:00",
        started_at=started_at,
        finished_at=finished_at,
    )


def test_runtime_is_start_to_finish():
    job = summary("2026-09-23T10:00:00.000+00:00", "2026-09-23T10:03:30.250+00:00")

    assert main.runtime_seconds(job) == 210.2


def test_a_running_jobs_runtime_is_start_to_now():
    job = summary("2026-09-23T10:00:00.000+00:00", None)
    now = datetime(2026, 9, 23, 10, 1, tzinfo=timezone.utc)

    assert main.runtime_seconds(job, now=now) == 60.0


def test_a_job_that_never_started_has_no_runtime():
    assert main.runtime_seconds(summary(None, None)) is None


# --- DELETE /jobs/{id} -------------------------------------------------------


@pytest.mark.parametrize("state", ["completed", "failed"])
def test_deleting_a_finished_job_removes_it_and_its_urls(client, repo, state):
    job_id = add_job(repo, "mine", state=state)

    response = client.delete(f"/jobs/{job_id}")

    assert response.status_code == 204
    assert client.get(f"/status/{job_id}").status_code == 404
    assert client.get(f"/result/{job_id}").status_code == 404
    assert client.get("/jobs").json() == []


def test_deleting_an_interrupted_job_is_allowed(client, repo):
    add_job(repo, "mine", state="running")
    repo.mark_interrupted()

    assert client.delete("/jobs/mine").status_code == 204


@pytest.mark.parametrize("state", ["queued", "running"])
def test_an_active_job_cannot_be_deleted(client, repo, state):
    add_job(repo, "mine", state=state)

    response = client.delete("/jobs/mine")

    assert response.status_code == 409
    assert repo.get("mine") is not None


def test_another_users_job_cannot_be_deleted(client, repo):
    add_job(repo, "bobs", owner=BOB.owner)

    response = client.delete("/jobs/bobs")

    assert response.status_code == 404
    assert repo.get("bobs") is not None


def test_deleting_an_unknown_job_is_not_found(client, repo):
    assert client.delete("/jobs/no-such-job").status_code == 404


# --- PATCH /jobs/{id} --------------------------------------------------------


@pytest.mark.parametrize("state", ["running", "completed"])
def test_renaming_a_job_shows_in_the_list(client, repo, state):
    add_job(repo, "mine", state=state)

    response = client.patch("/jobs/mine", json={"name": "  Kinase, second try  "})

    assert response.status_code == 200
    assert response.json()["name"] == "Kinase, second try"
    (job,) = client.get("/jobs").json()
    assert (job["name"], job["input_summary"]) == ("Kinase, second try", "P9WQA3")


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_blank_name_clears_it(client, repo, name):
    add_job(repo, "mine")
    client.patch("/jobs/mine", json={"name": "Kinase"})

    assert client.patch("/jobs/mine", json={"name": name}).json()["name"] is None
    assert client.get("/jobs").json()[0]["name"] is None


def test_another_users_job_cannot_be_renamed(client, repo):
    add_job(repo, "bobs", owner=BOB.owner)

    assert client.patch("/jobs/bobs", json={"name": "Mine"}).status_code == 404
    assert client.patch("/jobs/no-such-job", json={"name": "Mine"}).status_code == 404
    assert repo.get("bobs").name is None


def test_an_overlong_name_is_rejected(client, repo):
    add_job(repo, "mine")

    assert client.patch("/jobs/mine", json={"name": "x" * 201}).status_code == 422


# --- POST /jobs/delete -------------------------------------------------------


def test_bulk_delete_deletes_what_it_can_and_reports_the_rest(client, repo):
    add_job(repo, "done-1")
    add_job(repo, "done-2", state="failed")
    add_job(repo, "running", state="running")
    add_job(repo, "queued", state="queued")
    add_job(repo, "bobs", owner=BOB.owner)

    response = client.post(
        "/jobs/delete",
        json={"ids": ["done-1", "running", "bobs", "done-2", "queued", "missing", "done-1"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "deleted": ["done-1", "done-2"],
        "skipped": [
            {"id": "running", "reason": "running"},
            {"id": "bobs", "reason": "not_found"},
            {"id": "queued", "reason": "running"},
            {"id": "missing", "reason": "not_found"},
        ],
    }
    assert [job["id"] for job in client.get("/jobs").json()] == ["queued", "running"]
    assert repo.get("bobs") is not None


@pytest.mark.parametrize("body", [{"ids": []}, {}, {"ids": ["x"] * 501}])
def test_bulk_delete_rejects_an_empty_or_oversized_request(client, repo, body):
    assert client.post("/jobs/delete", json=body).status_code == 422
