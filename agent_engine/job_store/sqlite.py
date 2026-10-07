"""
The SQLite job repository, used in dev and deploy.

Every operation opens its own short-lived connection, so the request handlers
on the event loop and the BackgroundTasks worker threads never share one. WAL
mode lets readers proceed while a writer commits, writes take the lock up
front with BEGIN IMMEDIATE, and the busy timeout makes a contended writer wait
rather than fail.

The schema is built from the numbered SQL files in migrations/, applied at
startup and tracked with PRAGMA user_version.
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .repository import (
    ACTIVE_STATES,
    INTERRUPTED_ERROR,
    DeleteOutcome,
    JobCapReached,
    JobCaps,
    JobRecord,
    JobRepository,
    JobSummary,
    Owner,
    ProgressEntry,
    utc_day,
    utc_now,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# How long a writer waits for another writer's lock before giving up.
BUSY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """The migrations in `directory`, numbered 0001 upwards with no gaps."""
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise ValueError(f"Migration file {path.name} must be named NNNN_description.sql")
        migrations.append(
            Migration(int(match.group(1)), path.name, path.read_text(encoding="utf-8"))
        )

    versions = [m.version for m in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise ValueError(f"Migrations must be numbered 1..N without gaps; found {versions}")
    return migrations


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(conn: sqlite3.Connection, migrations: list[Migration]) -> int:
    """
    Apply every migration newer than the database's user_version, each in its
    own transaction together with the version bump. Returns the final version.
    """
    latest = migrations[-1].version if migrations else 0
    current = schema_version(conn)
    if current > latest:
        raise RuntimeError(
            f"The job database is at schema version {current}, newer than this code "
            f"knows ({latest}). Deploy the newer code or restore a matching backup."
        )

    for migration in migrations:
        if migration.version <= schema_version(conn):
            continue
        print(f"--- [JobStore] Applying migration {migration.name} ---")
        # executescript runs outside Python's transaction handling, so the
        # script carries its own BEGIN/COMMIT. user_version lives in the
        # database header and rolls back with the rest on failure.
        script = (
            f"BEGIN IMMEDIATE;\n{migration.sql}\n"
            f"PRAGMA user_version = {migration.version};\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return schema_version(conn)


class SqliteJobRepository(JobRepository):
    def __init__(self, path: Path, migrations: list[Migration] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            # WAL is a property of the database file, so setting it once sticks.
            conn.execute("PRAGMA journal_mode = WAL")
            apply_migrations(conn, load_migrations() if migrations is None else migrations)

    # --- Connections ---------------------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None leaves transactions to the explicit BEGIN in _write.
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # Durable across application crashes in WAL mode; only a power loss
            # can drop the last commits.
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # --- Reads ---------------------------------------------------------------

    def get(self, job_id: str) -> JobRecord | None:
        # Every column but the trace, which only /trace reads.
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, owner_sub, owner_username, name, inputs, state, progress, result,
                       error, model, app_version, created_at, started_at, finished_at
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return _to_record(row) if row else None

    def history(self, job_id: str) -> list[ProgressEntry]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT at, message FROM job_progress WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [ProgressEntry(at=row["at"], message=row["message"]) for row in rows]

    def list_by_owner(self, owner_sub: str) -> list[JobSummary]:
        # Leaves out the result, the one large column. rowid breaks created_at
        # ties newest first. Served by the jobs_by_owner index.
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, owner_sub, owner_username, name, inputs, state, progress, error,
                       created_at, started_at, finished_at
                FROM jobs WHERE owner_sub = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (owner_sub,),
            ).fetchall()
        return [_to_summary(row) for row in rows]

    # --- Writes --------------------------------------------------------------

    def delete(self, job_id: str, owner_sub: str) -> DeleteOutcome:
        with self._write() as conn:
            row = conn.execute(
                "SELECT state FROM jobs WHERE id = ? AND owner_sub = ?", (job_id, owner_sub)
            ).fetchone()
            if row is None:
                return "not_found"
            if row["state"] in ACTIVE_STATES:
                return "active"
            # Its job_progress rows go too, through ON DELETE CASCADE.
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return "deleted"

    def create(
        self,
        job_id: str,
        inputs: dict[str, Any],
        owner: Owner,
        model: str | None,
        app_version: str | None,
        caps: JobCaps | None = None,
    ) -> JobRecord:
        now = utc_now()
        day = utc_day(now)
        with self._write() as conn:
            if caps is not None:
                _check_caps(conn, caps, day)
            conn.execute(
                """
                INSERT INTO jobs (id, owner_sub, owner_username, inputs, state,
                                  model, app_version, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    owner.sub,
                    owner.username,
                    json.dumps(inputs),
                    model,
                    app_version,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO job_counts (day, created) VALUES (?, 1)
                ON CONFLICT (day) DO UPDATE SET created = created + 1
                """,
                (day,),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _to_record(row)

    def rename(self, job_id: str, owner_sub: str, name: str | None) -> bool:
        return self._update(
            "UPDATE jobs SET name = ? WHERE id = ? AND owner_sub = ?", (name, job_id, owner_sub)
        )

    def mark_running(self, job_id: str) -> bool:
        return self._update(
            "UPDATE jobs SET state = 'running', started_at = ? WHERE id = ? AND state = 'queued'",
            (utc_now(), job_id),
        )

    def record_progress(self, job_id: str, message: str) -> bool:
        now = utc_now()
        with self._write() as conn:
            updated = conn.execute(
                "UPDATE jobs SET progress = ? WHERE id = ? AND state = 'running'",
                (message, job_id),
            ).rowcount
            if updated:
                conn.execute(
                    "INSERT INTO job_progress (job_id, at, message) VALUES (?, ?, ?)",
                    (job_id, now, message),
                )
        return bool(updated)

    def complete(self, job_id: str, result: dict[str, Any]) -> bool:
        return self._update(
            """
            UPDATE jobs SET state = 'completed', result = ?, finished_at = ?
            WHERE id = ? AND state = 'running'
            """,
            (json.dumps(result), utc_now(), job_id),
        )

    def fail(self, job_id: str, error: str) -> bool:
        return self._update(
            """
            UPDATE jobs SET state = 'failed', error = ?, finished_at = ?
            WHERE id = ? AND state IN (?, ?)
            """,
            (error, utc_now(), job_id, *ACTIVE_STATES),
        )

    def save_trace(self, job_id: str, trace: dict[str, Any]) -> bool:
        return self._update("UPDATE jobs SET trace = ? WHERE id = ?", (json.dumps(trace), job_id))

    def get_trace(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT trace FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return json.loads(row["trace"]) if row and row["trace"] is not None else None

    def mark_interrupted(self) -> int:
        with self._write() as conn:
            return conn.execute(
                """
                UPDATE jobs SET state = 'interrupted', error = ?, finished_at = ?
                WHERE state IN (?, ?)
                """,
                (INTERRUPTED_ERROR, utc_now(), *ACTIVE_STATES),
            ).rowcount

    def fail_stale(self, before: str, error: str) -> int:
        # utc_now() timestamps share one fixed format, so they compare as text.
        with self._write() as conn:
            return conn.execute(
                """
                UPDATE jobs SET state = 'failed', error = ?, finished_at = ?
                WHERE state IN (?, ?) AND COALESCE(started_at, created_at) < ?
                """,
                (error, utc_now(), *ACTIVE_STATES, before),
            ).rowcount

    def _update(self, sql: str, params: tuple) -> bool:
        with self._write() as conn:
            return conn.execute(sql, params).rowcount > 0


def _check_caps(conn: sqlite3.Connection, caps: JobCaps, day: str) -> None:
    """Raise JobCapReached if a cap is full. Runs inside create's transaction."""
    if caps.max_active is not None:
        (active,) = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'running')"
        ).fetchone()
        if active >= caps.max_active:
            raise JobCapReached("active", caps.max_active)
    if caps.max_per_day is not None:
        row = conn.execute("SELECT created FROM job_counts WHERE day = ?", (day,)).fetchone()
        if (row["created"] if row else 0) >= caps.max_per_day:
            raise JobCapReached("daily", caps.max_per_day)


def _to_summary(row: sqlite3.Row) -> JobSummary:
    return JobSummary(
        id=row["id"],
        owner=Owner(sub=row["owner_sub"], username=row["owner_username"]),
        inputs=json.loads(row["inputs"]),
        state=row["state"],
        progress=row["progress"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        name=row["name"],
    )


def _to_record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        owner=Owner(sub=row["owner_sub"], username=row["owner_username"]),
        inputs=json.loads(row["inputs"]),
        state=row["state"],
        progress=row["progress"],
        result=json.loads(row["result"]) if row["result"] is not None else None,
        error=row["error"],
        model=row["model"],
        app_version=row["app_version"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        name=row["name"],
    )
