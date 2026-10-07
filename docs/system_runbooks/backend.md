# Runbook: Backend / Agent Engine

FastAPI service and the agent pipeline. Source: [`main.py`](../../main.py), [`schemas.py`](../../schemas.py), [`agent_engine/`](../../agent_engine).

## Running the service

```bash
uv sync
uv run fastapi dev main.py
```

Serves on `http://localhost:8000`; interactive docs at `/docs`. Prerequisites and `.env` setup are in the [README](../../README.md). To run this alongside the frontend with one command, use `npm run dev` from the repository root instead.

Preconditions checked at startup:

- `ENTREZ_EMAIL` must be set. `settings.entrez_email()` raises `ValueError` in the startup hook otherwise, so the server will not start.
- `APP_ENV`, if set, must be `dev` or `deploy`.
- The sign-in settings must be consistent (see [Sign-in](#sign-in)): `AUTH_ENABLED=true` needs every Cognito setting and `APP_ENV=deploy`, and a deploy with Cognito settings but without `AUTH_ENABLED=true` refuses to start.
- The job database opens and migrates at startup (see [Job store](#job-store)). In `deploy`, `DATA_DIR` must be set; the image sets it to `/data`.
- In `deploy`, `DAILY_JOB_CAP` must be set (see [Job caps](#job-caps)), and so must `AUTH_ENABLED`, so a lost parameter can't open the site without sign-in.
- The LLM configuration is **not** checked at startup. `agent_engine/llm.py` resolves the model on the first LLM call, so a missing `API_KEY` surfaces as failed extractions and a failed synthesis. See [external_dependencies.md](external_dependencies.md) for the provider/model table.
- `BLAST_DB_PATH` is checked per-job, not at startup. A missing value produces a failed job rather than a failed boot.

## Runtime modes

`APP_ENV` (read in `agent_engine/settings.py`) switches runtime behaviour only; it never changes infrastructure.

| | `dev` (default) | `deploy` |
| --- | --- | --- |
| CORS | The Vite origins (`DEV_CORS_ORIGINS`) | None; the frontend is same-origin |
| Frontend | Vite dev server | Built assets served by `StaticFiles` at `/` (from `FRONTEND_DIST`), mounted after the API routes |
| LLM route | `LLM_PROVIDER` plus `API_KEY` | A provider key or Bedrock, from Parameter Store |
| Internal SSGCID data | On (`INTERNAL_DATA` default) | Off; an SSGCID-looking ID fails at once and asks for the FASTA sequence |
| Job database | `DATA_DIR`, default `data/` at the repository root (gitignored) | `DATA_DIR` required; the image sets `/data`, where the data volume is mounted |
| Current user | Always `dev@local` | The Cognito session or a 401 (`AUTH_ENABLED=true`); the placeholder `shared` user only with an explicit `AUTH_ENABLED=false` |
| Job caps | `MAX_ACTIVE_JOBS` defaults to 2; no daily cap unless set | `DAILY_JOB_CAP` required; `MAX_ACTIVE_JOBS` defaults to 2 |

`INTERNAL_DATA=true|false` overrides the mode default. `GET /health` returns `{"status": "ok"}` in both modes and touches nothing external.

The deploy image is built from the root [`Dockerfile`](../../Dockerfile), which bakes in the built frontend, `ncbi-blast+` and `pdbaa`, and runs one Uvicorn worker. To check it locally:

```bash
docker build -t ppr-app .
```

```bash
docker run --rm -p 8000:8000 -e ENTREZ_EMAIL=you@example.org -e DAILY_JOB_CAP=5 -e AUTH_ENABLED=false ppr-app
```

`fastapi dev` enables autoreload. Completed jobs survive the reload, but **a code edit during a run cuts that job off**, and it shows as `interrupted`. Use `fastapi run main.py` for demos.

## Endpoints

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/analyze` | Mints a UUID, records the job as `queued` and owned by the current user, schedules the background job, returns `{job_id, state: "queued"}` immediately. Never blocks. |
| `GET` | `/status/{job_id}` | Returns `job_id`, `state`, `progress`, `history` (`[{at, message}]`, oldest first), `inputs` and `created_at`/`started_at`/`finished_at`; plus `error` when the job `failed` or was `interrupted`. 404 if unknown or another user's. |
| `GET` | `/result/{job_id}` | Returns the full `ProtocolResult`. 404 unknown or another user's, 400 if the job failed or was interrupted, 202 if queued or running. A stored result from an older report shape is returned as stored. |
| `GET` | `/trace/{job_id}` | The job's `Trace`: each LLM call's reasoning summary, output, tokens, time and estimated cost, and each stage's time. `null` for a job that ran before traces existed or is still running. 404 unknown or another user's. Kept out of `/result` so the report stays small. |
| `GET` | `/jobs` | The current user's jobs, newest first: `id`, `name` (set by the user, else `null`), `input_summary` (the ID as submitted or the FASTA header), `state`, `progress`, `error`, `created_at`/`started_at`/`finished_at` and `runtime_seconds` (to now while running). No results. |
| `PATCH` | `/jobs/{job_id}` | Body `{"name": ...}` (up to 200 characters). Renames one of the user's jobs in any state and returns its `/jobs` row; a blank or `null` name clears it. 404 if unknown or another user's. |
| `DELETE` | `/jobs/{job_id}` | Hard-deletes one of the user's finished, failed or interrupted jobs with its history: 204. 404 if unknown or another user's, 409 if queued or running. |
| `POST` | `/jobs/delete` | Body `{"ids": [...]}` (1–500). Deletes what it can and returns `{deleted: [ids], skipped: [{id, reason}]}`, where `reason` is `running` (queued or running) or `not_found` (unknown or another user's). |
| `GET` | `/auth/me` | The current user: `sub`, `username`, `email`, `auth_enabled`, `can_change_password` (false for the `REVIEWER_USERNAME` account and without Cognito) and `change_password_url`. 401 without a session when sign-in is on. |
| `GET` | `/auth/login`, `/auth/callback`, `/auth/logout` | The Cognito sign-in flow; see [Sign-in](#sign-in). Redirect to `/` when sign-in is off. |
| `GET` | `/health` | Static `{"status": "ok"}` for the uptime check. |

With sign-in on, every endpoint except `/health`, `/auth/login`, `/auth/callback`, `/auth/logout`, `/docs` and the static frontend returns 401 without a valid session.

### Request body

`PurificationRequest` — see [`schemas.py`](../../schemas.py).

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `fasta_id` | `str` | required | FASTA, UniProt accession, gene name, or SSGCID ID |
| `failed_purification_text` | `str?` | `null` | Overrides the CTTdb failed-protocol lookup |
| `min_percent_identity` | `float` | `90.0` | 0–100 |
| `min_query_coverage` | `float` | `90.0` | 0–100 |
| `max_evalue` | `float` | `1e-5` | > 0, ≤ 1000 |
| `max_hits` | `int` | `10` | 1–50 |
| `max_protocols` | `int` | `3` | 1–5. The literature search stops once this many source protocols are extracted |

The UI sends its own, more permissive defaults (40 / 40 / 1e-3 / 50) on every request, because those are what actually find homologs. The Pydantic defaults above apply only to direct API callers, so expect markedly different behavior between a browser run and a bare `curl`.

### Status values

`state` is the job's lifecycle: `queued` → `running` → `completed` or `failed`, or `interrupted` when a restart cut it off. `progress` is the latest progress string below, and `history` keeps every one with its timestamp.

Progress strings are free text for display. Switch on `state`, never on `progress`. The frontend recognizes pipeline stages by a message's opening words (`src/lib/progress.ts`), so rewording the start of a message moves the progress tracker.

Progress strings, in pipeline order:

```
Initializing Agent...
Retrieving internal data for {ssgcid_id}...          # SSGCID inputs only
Fetching full sequence for UniProt ID: {id}...       # non-FASTA inputs only
Analyzing user-provided failed purification...       # when failed_purification_text is set
Running BLAST (Strict: {cov}% Cov)...
Strict search failed. Relaxing parameters (Rescue Mode)...
Processing {n} BLAST hits...
Finding matching protocols in PMC...
Analyzing Match {i}: {pdb_id} ({organism})...        # repeats per hit
Synthesizing final protocol with LLM...
   — or —
No source protocols found. Skipping synthesis...     # nothing to compare against
```

### Manual exercise

```bash
curl -s -X POST localhost:8000/analyze -H 'Content-Type: application/json' -d '{"fasta_id":"P9WQA3","min_percent_identity":40,"min_query_coverage":40,"max_evalue":0.001,"max_hits":50}'
```

```bash
curl -s localhost:8000/status/<job_id>
```

## Job store

Jobs live in SQLite at `DATA_DIR/jobs.sqlite3`, behind the `JobRepository` interface in [`agent_engine/job_store/`](../../agent_engine/job_store). `main.py` opens it at startup, which applies pending migrations and marks jobs left `queued` or `running` by a previous process as `interrupted`. **Run a single process only** — never a multi-worker Gunicorn/Uvicorn config. Users can delete their own finished jobs; nothing is evicted automatically or cancellable yet, but a stuck job is failed at its deadline.

- **Tables.** `jobs` (owner, inputs, state, latest progress, result JSON, trace JSON, error, model, app version, timestamps), `job_progress` (one timestamped row per progress message) and `job_counts` (jobs created per UTC day, for the daily cap).
- **Owner.** `owner_sub` and `owner_username` come from the current user through `request_owner()` in `main.py`.
- **Deletion.** `JobRepository.delete` checks the owner and state and deletes in one transaction; `job_progress` rows go with the job (`ON DELETE CASCADE`).
- **Trace.** `run_agent_task` creates the job's `Trace` and passes it to the pipeline, which records into it through `agent_engine/trace.py`. It is saved before the job completes or fails, so a failed or crashed job keeps the steps it recorded. `JobRepository.get` leaves it out; only `/trace` reads it.
- **App version.** `APP_VERSION` (the commit SHA) is stored on each job if set. The model is stored as `provider:model` from `LLM_PROVIDER`/`LLM_MODEL`.
- **Stuck jobs.** A sweep in the lifespan hook runs every minute and fails any job still `queued` or `running` more than `JOB_DEADLINE_MINUTES` (default 60) after it started, or was created if it never started, which frees its `MAX_ACTIVE_JOBS` slot. The worker thread can't be stopped and runs on, but its late result can't overwrite the failure and is logged instead. The final `complete`/`fail` write is retried twice with backoff; a result the store still refuses is logged as `[JobStore] Result of <id>: <json>` and the job fails, and a failure the store refuses is left to the sweep.
- **Migrations.** Add a new numbered file, `agent_engine/job_store/migrations/NNNN_description.sql`, never edit an applied one. It runs at the next startup, in one transaction with the `PRAGMA user_version` bump. The server refuses to start on a database from newer code.
- **Deploy.** The image sets `DATA_DIR=/data`, owned by the `app` user (UID/GID 10001), where the host mounts the EBS data volume; see [deployment.md](deployment.md#restoring-the-job-database) for snapshots and restores.
- **Tests** use `InMemoryJobRepository` or a SQLite file under `tmp_path`; `conftest.py` points `DATA_DIR` at a temp directory for every test.

### Job caps

The spending guards are global, since every account is admin-created. `/analyze` returns **429** with a message saying which limit was hit and when to try again:

- `MAX_ACTIVE_JOBS`: queued plus running jobs at once. Default 2 in both modes; each job holds a threadpool slot for minutes. A job past `JOB_DEADLINE_MINUTES` is failed and stops counting (see [Stuck jobs](#job-store)).
- `DAILY_JOB_CAP`: jobs created per UTC day, counted in `job_counts`, so deleting jobs doesn't free up the allowance and a restart doesn't reset it. Size it as (monthly budget − fixed monthly cost) ÷ 30 ÷ the per-job LLM cost.

`JobRepository.create` checks both inside the insert's `BEGIN IMMEDIATE` transaction, so a burst can't get past them. `PurificationRequest` bounds the input: `fasta_id` up to 20,000 characters, `failed_purification_text` up to 20,000 (it goes straight into the prompts), `max_hits` up to 50, and `max_protocols` up to 5. Anything outside is a 422.

To inspect the dev database:

```bash
sqlite3 data/jobs.sqlite3 "SELECT id, state, progress, created_at FROM jobs ORDER BY created_at DESC LIMIT 10"
```

To start over in dev, stop the server and delete `data/`.

## Pipeline stages and their failure modes

The orchestrator is `ProteinPurificationAgent.run()` in `agent_engine/agents/agent_body.py`. Stages run sequentially; see [architecture.md](../agent_reference/architecture.md) for the full data flow.

| Stage | Module | Hard failure (job → `ERROR`) | Soft degradation |
| --- | --- | --- | --- |
| Input resolution | `agent_body._resolve_input` | No `ATG` in a CTTdb sequence; sequence translation error; CTTdb miss → the raw ID then fails UniProt resolution | — |
| UniProt metadata | `grounding_tool.get_uniprot_metadata` | — | Any error → `None`; synthesis loses compartment and topology context |
| Sequence fetch | `agent_body._get_fasta_from_uniprot` | Unresolvable UniProt ID | — |
| BLAST | `agent_tools/blast.py` | `BLAST_DB_PATH` unset; `blastp` missing or nonzero exit; 30s timeout; zero hits after relaxation | Zero strict hits → one relaxed retry at 20%/20% |
| Similarity ranking | `protein_similarity_tool.py` | — | RCSB error or missing source organism → identity-only score, marked `No RCSB Metadata` → identity-only score for that hit; Neo4j absent → identity-only for all hits |
| Literature grounding | `grounding_tool.py`, `methods_tool.py` | — | Per-hit `try/except` records a status and continues |
| Extraction | `extraction_agent.py` | — | `ERROR::NO_PROTOCOL_FOUND` → hit marked `No Protocol Found in Paper` |
| Synthesis | `comprehensive_protocol_agent.py` | — | Exception → partial result: `error_message` set, protocol `null`, BLAST hits and source protocols preserved |

### Triage

**Job fails instantly with a BLAST error.** The user sees only "BLAST could not search this input"; the command and `blastp`'s stderr are in the log under `[BLAST Tool]`. Check `BLAST_DB_PATH` points at the database *stem*, not a directory and not a specific file extension — e.g. `C:\db\pdbaa\pdbaa`. Verify the binary independently:

```bash
blastp -version
```

**Every run reports "No BLAST results found".** The default UI thresholds (40% identity, 40% coverage) are already permissive, and the rescue retry drops to 20%/20%. Reaching this message means `pdbaa` genuinely has no homolog — or the database is corrupt/incompletely extracted. Confirm the database is readable:

```bash
blastdbcmd -db "$BLAST_DB_PATH" -info
```

**Report shows a "Protocol synthesis failed" banner.** Synthesis threw. This is a deliberate partial result: the job still reports `COMPLETED` because the BLAST analysis and extracted source protocols are valid and worth showing, but `comprehensive_protocol` is `null` and `error_message` carries the exception type and message. Usual causes are an exhausted provider quota, a safety block, or a context-length overflow from many long articles. The full traceback is in server stdout as `[Agent] LLM Error: ...`.

**Report renders with an empty protocol table but prose is present.** The LLM found purification text but `ProtocolAgent` extracted zero `BufferStep`s, typically because the source methods described buffers without compositions — which the prompt deliberately instructs it to discard.

**Many hits show "PMC Retrieval Failed".** Entrez itself failed (rate limiting or an outage), not the papers' access; a re-run usually recovers them. The per-attempt errors are in the log.

**All hits show "No PMC Primary Citation" or "No Open Access".** Expected and common. `pdbaa` structures often have no PMC-indexed primary citation, and many that do are not open-access downloadable.

**Everything is slow.** A full run is minutes, not seconds: up to `max_hits` RCSB round-trips with a 1-second sleep every 10, Entrez article fetches until `max_protocols` protocols are found, and up to 12 LLM calls (see the cost profile in [external_dependencies.md](external_dependencies.md#llm-provider)). Nothing is cached, so an identical re-run repeats every call.

### Reading the logs

The server's stdout is the primary debugging surface, with every line tagged by subsystem (`[Agent]`, `[BLAST Tool]`, …; the tag convention is in [style.md](../agent_reference/style.md)). These logs are much more detailed than the status strings the browser shows.

## Changing the report

Stored reports are the `ProtocolResult` JSON as it was when the job ran, and nothing migrates them. So a report change is additive: a new field is optional with a `null` default, and every component that reads it renders its absence. An old report then keeps validating and rendering, rather than falling back to the "older version" notice. Renaming or removing a field, or changing its type, turns every stored report into that notice; do it only on purpose, and say so in the PR.

A change to the report's fields touches, in one PR:

- the model in [`agent_engine/models.py`](../../agent_engine/models.py) (hits, papers, source protocols, `BufferStep`) or [`schemas.py`](../../schemas.py), and the code that sets the field
- the generated frontend types: `uv run python scripts/export_openapi.py`, then `npm run gen:api` in `purification-rescue-frontend`. `tests/test_result_schema.py` and CI fail while either is stale
- `src/lib/api/resultShape.ts`, but only for a field a component dereferences without a guard
- the report components in both the screen and print trees ([frontend.md](frontend.md#report-and-pdf-export))
- the pipeline snapshot: regenerate it with `UPDATE_SNAPSHOTS=1 uv run pytest tests/test_pipeline_snapshot.py` and review the diff; it should show exactly the intended change

Per-hit statuses are `HitStatus` in `agent_engine/models.py`, and their values are part of this contract too; see [style.md](../agent_reference/style.md).

## Sign-in

The code is in [`agent_engine/auth.py`](../../agent_engine/auth.py); settings are read in `agent_engine/settings.py` at startup.

| Mode | When | Current user |
| --- | --- | --- |
| `dev` | `APP_ENV=dev` | `dev@local`. No login screen and no Cognito. |
| `shared` | `APP_ENV=deploy`, `AUTH_ENABLED=false` | The placeholder `shared` user, with no gate at all: for checking the image locally, never for a public server. Logs `[Auth] WARNING: AUTH_ENABLED is off in deploy` at startup. Unset `AUTH_ENABLED` stops a deploy at startup. |
| `cognito` | `APP_ENV=deploy`, `AUTH_ENABLED=true` | The session's user, or a 401. Never falls back. |

Settings for `cognito` mode, from Parameter Store under `/ppr/` in deploy:

| Variable | Required | Notes |
| --- | --- | --- |
| `AUTH_ENABLED` | yes | `true` turns sign-in on. |
| `COGNITO_DOMAIN` | yes | The managed login domain, e.g. `<prefix>.auth.us-east-1.amazoncognito.com`. |
| `COGNITO_USER_POOL_ID` | yes | e.g. `us-east-1_AbC123`; the issuer and region derive from it. |
| `COGNITO_CLIENT_ID` | yes | The app client. |
| `COGNITO_CLIENT_SECRET` | no | Only for a confidential client (`SecureString`). Without it the client is public and relies on PKCE. |
| `SESSION_SECRET_KEY` | yes | Signs the session cookie; at least 32 characters (`SecureString`). Rotating it signs everyone out. |
| `REVIEWER_USERNAME` | no | A shared account's username; its profile hides change-password. |

The flow: `/auth/login` redirects to Cognito with PKCE, state and nonce; `/auth/callback` exchanges the code, validates the ID token, and stores `sub`, username, email and the sign-in time in the `ppr_session` cookie (HTTP-only, `Secure`, `SameSite=Lax`, 7 days from sign-in); `/auth/logout` clears it and redirects to Cognito's `/logout` with `logout_uri` set to the site's origin. The app client needs the callback URL `https://<site>/auth/callback` and the sign-out URL `https://<site>` (no trailing slash).

To try the real flow locally, run with `APP_ENV=deploy`, `DATA_DIR`, `FRONTEND_DIST` and the settings above, on `http://localhost:8000`, which the app client allows.

**Triage.** A callback that fails validation logs `[Auth] Sign-in failed: <error>` and returns 401; a wrong issuer or client ID in the settings shows up here. A callback with no sign-in pending (for example after a password reset) redirects to `/`. Tests stub only the token exchange; see `tests/test_auth.py`.

## Security posture

- In `dev`, CORS allows only the Vite dev origins (`http://localhost:5173` and `http://127.0.0.1:5173`; override with comma-separated `DEV_CORS_ORIGINS`, never `*`), and every request is `dev@local`. Never expose a `dev` server.
- In `deploy`, Terraform sets `AUTH_ENABLED=true` and the Cognito settings. A deploy without `AUTH_ENABLED` refuses to start; `false` runs every request as one shared placeholder identity with no gate, so a public deploy must never run that way.
- With sign-in on, users see and delete only their own jobs. Cross-site request forgery is blocked by the `SameSite=Lax` session cookie, which relies on every state change being a POST or DELETE. `GET /auth/logout` is the one side effect on a GET, and a cross-site sign-out is harmless.
- Global job caps and input bounds limit LLM spend; there is no per-user quota or rate limit, and each job can make ~12 LLM calls over long contexts.
- A job's error is shown to its user, so it never carries exception text. A crash stores a generic message and prints the traceback to the log; BLAST failures raise `BlastError` with a message written for the user.
- Caddy sets HSTS, a Content-Security-Policy (same-origin scripts only) and the usual `nosniff`, framing and referrer headers ([`Caddyfile`](../../deploy/Caddyfile)).
- No egress restrictions: the service will fetch any PMC article or RCSB record a job leads it to.
