"""
Tests for the job repositories and the SQLite migrations (#24).

The contract tests run against both implementations, so the in-memory one the
endpoint tests use can't drift from the SQLite one the app runs on.
"""

import sqlite3
import threading
import time

import pytest

from agent_engine.job_store import (
    INTERRUPTED_ERROR,
    InMemoryJobRepository,
    Owner,
    SqliteJobRepository,
)
from agent_engine.job_store.repository import utc_now
from agent_engine.job_store.sqlite import Migration, apply_migrations, load_migrations

INPUTS = {"fasta_id": "P9WQA3", "max_hits": 10}


@pytest.fixture(params=["sqlite", "memory"])
def repo(request, tmp_path):
    if request.param == "sqlite":
        return SqliteJobRepository(tmp_path / "jobs.sqlite3")
    return InMemoryJobRepository()


def create(repo, job_id="job-1", owner=Owner()):
    return repo.create(job_id, INPUTS, owner, model="gemini:gemini-3.8-flash", app_version="abc123")


def running(repo, job_id="job-1"):
    create(repo, job_id)
    repo.mark_running(job_id)


# --- Contract ----------------------------------------------------------------


def test_a_new_job_is_queued_with_its_inputs_and_metadata(repo):
    create(repo, owner=Owner(sub="user-sub", username="alice"))

    job = repo.get("job-1")

    assert (job.state, job.inputs, job.owner, job.model, job.app_version) == (
        "queued",
        INPUTS,
        Owner(sub="user-sub", username="alice"),
        "gemini:gemini-3.8-flash",
        "abc123",
    )
    assert job.created_at and job.started_at is None and job.finished_at is None


def test_an_unknown_job_is_none(repo):
    assert repo.get("no-such-job") is None


def test_a_duplicate_job_id_is_rejected(repo):
    create(repo)

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        create(repo)


def test_a_job_runs_then_completes_with_its_result(repo):
    create(repo)

    assert repo.mark_running("job-1")
    assert repo.get("job-1").started_at is not None
    assert repo.complete("job-1", {"raw_plan": "plan"})

    job = repo.get("job-1")
    assert (job.state, job.result, job.error) == ("completed", {"raw_plan": "plan"}, None)
    assert job.finished_at is not None


def test_a_failed_job_keeps_its_error(repo):
    running(repo)

    assert repo.fail("job-1", "BLAST failed")

    job = repo.get("job-1")
    assert (job.state, job.error) == ("failed", "BLAST failed")


def test_a_queued_job_can_fail_before_it_starts(repo):
    create(repo)

    assert repo.fail("job-1", "boom")
    assert repo.get("job-1").state == "failed"


@pytest.mark.parametrize(
    "transition",
    [
        lambda r: r.mark_running("job-1"),
        lambda r: r.complete("job-1", {}),
        lambda r: r.fail("job-1", "late"),
        lambda r: r.record_progress("job-1", "late"),
    ],
    ids=["run", "complete", "fail", "progress"],
)
def test_a_finished_job_cannot_change(repo, transition):
    running(repo)
    repo.complete("job-1", {"raw_plan": "plan"})

    assert transition(repo) is False
    assert repo.get("job-1").state == "completed"


def test_a_queued_job_cannot_complete(repo):
    create(repo)

    assert repo.complete("job-1", {}) is False
    assert repo.get("job-1").state == "queued"


def test_progress_updates_the_latest_message_and_the_ordered_history(repo):
    running(repo)
    messages = [f"Step {i}..." for i in range(12)]

    for message in messages:
        repo.record_progress("job-1", message)

    assert repo.get("job-1").progress == "Step 11..."
    assert [entry.message for entry in repo.history("job-1")] == messages
    assert all(entry.at for entry in repo.history("job-1"))


def test_progress_is_ignored_before_the_job_runs(repo):
    create(repo)

    assert repo.record_progress("job-1", "too early") is False
    assert repo.history("job-1") == []


def test_histories_are_kept_per_job(repo):
    running(repo, "job-1")
    running(repo, "job-2")

    repo.record_progress("job-1", "one")
    repo.record_progress("job-2", "two")

    assert [e.message for e in repo.history("job-2")] == ["two"]


def test_interrupted_marking_moves_only_unfinished_jobs(repo):
    create(repo, "queued")
    running(repo, "running")
    running(repo, "done")
    repo.complete("done", {})

    assert repo.mark_interrupted() == 2

    states = {job_id: repo.get(job_id).state for job_id in ["queued", "running", "done"]}
    assert states == {"queued": "interrupted", "running": "interrupted", "done": "completed"}
    assert repo.get("running").error == INTERRUPTED_ERROR
    assert repo.get("running").finished_at is not None


def test_stale_failing_moves_only_active_jobs_older_than_the_cutoff(repo):
    create(repo, "old-queued")
    running(repo, "old-running")
    running(repo, "old-done")
    repo.complete("old-done", {})
    time.sleep(0.01)  # timestamps have millisecond precision
    cutoff = utc_now()
    time.sleep(0.01)
    running(repo, "new-running")

    assert repo.fail_stale(cutoff, "too slow") == 2

    states = {job_id: repo.get(job_id).state for job_id in ["old-queued", "old-running"]}
    assert states == {"old-queued": "failed", "old-running": "failed"}
    assert (repo.get("old-running").error, repo.get("old-done").state) == ("too slow", "completed")
    assert repo.get("new-running").state == "running"


def test_a_long_queued_job_is_judged_by_when_it_started(repo):
    create(repo)
    time.sleep(0.01)
    cutoff = utc_now()
    time.sleep(0.01)
    repo.mark_running("job-1")

    assert repo.fail_stale(cutoff, "too slow") == 0


ALICE = Owner(sub="alice-sub", username="alice")
BOB = Owner(sub="bob-sub", username="bob")


def test_an_owners_jobs_are_listed_newest_first_without_anyone_elses(repo):
    for job_id, owner in [("a1", ALICE), ("b1", BOB), ("a2", ALICE), ("a3", ALICE)]:
        create(repo, job_id, owner)

    jobs = repo.list_by_owner("alice-sub")

    assert [job.id for job in jobs] == ["a3", "a2", "a1"]


def test_a_listed_job_carries_its_state_and_times_but_not_its_result(repo):
    create(repo, "a1", ALICE)
    repo.mark_running("a1")
    repo.record_progress("a1", "Running BLAST...")
    repo.complete("a1", {"raw_plan": "plan"})

    (job,) = repo.list_by_owner("alice-sub")

    assert (job.state, job.progress, job.owner, job.inputs) == (
        "completed",
        "Running BLAST...",
        ALICE,
        INPUTS,
    )
    assert job.created_at and job.started_at and job.finished_at
    assert not hasattr(job, "result")


def test_an_owner_with_no_jobs_lists_nothing(repo):
    create(repo, "b1", BOB)

    assert repo.list_by_owner("alice-sub") == []


def test_a_finished_job_is_deleted_with_its_history(repo):
    create(repo, "a1", ALICE)
    repo.mark_running("a1")
    repo.record_progress("a1", "Running BLAST...")
    repo.fail("a1", "BLAST failed")

    assert repo.delete("a1", "alice-sub") == "deleted"
    assert repo.get("a1") is None
    assert repo.history("a1") == []
    assert repo.list_by_owner("alice-sub") == []


def test_an_interrupted_job_can_be_deleted(repo):
    create(repo, "a1", ALICE)
    repo.mark_running("a1")
    repo.mark_interrupted()

    assert repo.delete("a1", "alice-sub") == "deleted"


def test_a_job_from_before_sign_in_belongs_to_nobody(repo):
    create(repo, "old", Owner())
    repo.fail("old", "boom")

    assert repo.delete("old", "alice-sub") == "not_found"
    assert repo.get("old") is not None


@pytest.mark.parametrize("started", [False, True], ids=["queued", "running"])
def test_an_active_job_is_not_deleted(repo, started):
    create(repo, "a1", ALICE)
    if started:
        repo.mark_running("a1")

    assert repo.delete("a1", "alice-sub") == "active"
    assert repo.get("a1") is not None


def test_another_owners_job_is_not_found_and_kept(repo):
    create(repo, "b1", BOB)
    repo.fail("b1", "boom")

    assert repo.delete("b1", "alice-sub") == "not_found"
    assert repo.get("b1") is not None


def test_deleting_an_unknown_job_is_not_found(repo):
    assert repo.delete("no-such-job", "alice-sub") == "not_found"


def test_a_job_can_be_renamed_while_running_and_cleared(repo):
    create(repo, "a1", ALICE)
    repo.mark_running("a1")

    assert repo.rename("a1", "alice-sub", "My favourite kinase")
    assert repo.get("a1").name == "My favourite kinase"
    assert repo.list_by_owner("alice-sub")[0].name == "My favourite kinase"

    assert repo.rename("a1", "alice-sub", None)
    assert repo.get("a1").name is None


def test_another_owners_job_is_not_renamed(repo):
    create(repo, "b1", BOB)

    assert not repo.rename("b1", "alice-sub", "Mine now")
    assert not repo.rename("no-such-job", "alice-sub", "Nothing")
    assert repo.get("b1").name is None


# --- SQLite specifics --------------------------------------------------------


def test_deleting_a_job_removes_its_progress_rows(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    repo = SqliteJobRepository(path)
    create(repo, "a1", ALICE)
    repo.mark_running("a1")
    repo.record_progress("a1", "Running BLAST...")
    repo.complete("a1", {})

    repo.delete("a1", "alice-sub")

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_progress").fetchone()[0] == 0


def test_a_second_repository_on_the_same_file_sees_the_jobs(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first = SqliteJobRepository(path)
    running(first)
    first.record_progress("job-1", "Running BLAST...")
    first.complete("job-1", {"raw_plan": "plan"})

    second = SqliteJobRepository(path)

    assert second.get("job-1").result == {"raw_plan": "plan"}
    assert [e.message for e in second.history("job-1")] == ["Running BLAST..."]


def test_the_database_uses_wal(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    SqliteJobRepository(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_the_data_directory_is_created(tmp_path):
    SqliteJobRepository(tmp_path / "nested" / "dir" / "jobs.sqlite3")

    assert (tmp_path / "nested" / "dir" / "jobs.sqlite3").exists()


def test_writes_from_worker_threads_are_all_kept(tmp_path):
    """Models several BackgroundTasks threads reporting progress at once."""
    repo = SqliteJobRepository(tmp_path / "jobs.sqlite3")
    job_ids = [f"job-{i}" for i in range(4)]
    for job_id in job_ids:
        running(repo, job_id)

    def report(job_id):
        for i in range(25):
            repo.record_progress(job_id, f"{job_id} step {i}")

    threads = [threading.Thread(target=report, args=(job_id,)) for job_id in job_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for job_id in job_ids:
        assert [e.message for e in repo.history(job_id)] == [
            f"{job_id} step {i}" for i in range(25)
        ]


# --- Migrations --------------------------------------------------------------


def tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    }


def test_a_fresh_database_is_migrated_to_the_latest_version(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    SqliteJobRepository(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(load_migrations())
        assert tables(conn) == {"jobs", "job_progress", "job_counts"}


def test_rerunning_the_migrations_is_a_no_op(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    repo = SqliteJobRepository(path)
    create(repo)

    with sqlite3.connect(path) as conn:
        assert apply_migrations(conn, load_migrations()) == len(load_migrations())
    SqliteJobRepository(path)

    assert repo.get("job-1").state == "queued"


def test_a_failing_migration_leaves_the_version_and_schema_unchanged(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    SqliteJobRepository(path)
    current = len(load_migrations())
    broken = Migration(
        current + 1, f"{current + 1:04d}_broken.sql", "CREATE TABLE extra (id INTEGER);\nNOT SQL;"
    )

    with sqlite3.connect(path, isolation_level=None) as conn:
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(conn, load_migrations() + [broken])

        assert conn.execute("PRAGMA user_version").fetchone()[0] == current
        assert "extra" not in tables(conn)


def test_a_database_newer_than_the_code_is_refused(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    SqliteJobRepository(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="schema version 99"):
        SqliteJobRepository(path)


def test_migration_numbering_must_have_no_gaps(tmp_path):
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0003_third.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="without gaps"):
        load_migrations(tmp_path)
