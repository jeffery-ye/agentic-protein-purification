# Agent Reference: Architecture

High-level architecture, module boundaries, data pipelines, and service interactions for Agentic Protein Purification.

## Topology

The system is two deployable units plus external services:

```
Browser (Svelte SPA, :5173)
      │  /auth/login, /auth/callback ◄──► Cognito managed login (deploy, AUTH_ENABLED)
      │  GET  /auth/me
      │  POST /analyze ──► job_id
      │  GET  /status/{job_id}   (polled every 2s)
      │  GET  /result/{job_id}, GET /trace/{job_id}
      │  GET  /jobs, DELETE /jobs/{job_id}, POST /jobs/delete
      ▼
FastAPI backend (:8000, main.py) ──► SQLite job store (DATA_DIR/jobs.sqlite3)
      │  BackgroundTasks → run_agent_task (worker thread)
      ▼
ProteinPurificationAgent (agent_engine/agents/agent_body.py)
      ├── local BLAST+ subprocess (blastp against pdbaa)
      ├── UniProt REST          (sequence, metadata, lineage)
      ├── RCSB PDB REST         (UniProt xref, primary citation)
      ├── NCBI Entrez / PMC     (full-text article XML)
      ├── LLM provider          (4 pydantic-ai agents; Gemini by default)
      ├── Neo4j        [optional] (taxonomic distance)
      └── SQL Server   [optional] (SSGCID / CTTdb protocols)
```

Application state is one SQLite file holding the jobs and their progress history. SQLite assumes one writer process, so the backend is single-instance by construction.

## Module boundaries

| Module | Responsibility |
| --- | --- |
| [`main.py`](../../main.py) | HTTP surface, job lifecycle, ownership checks, CORS. Contains no domain logic and no SQL. |
| [`agent_engine/auth.py`](../../agent_engine/auth.py) | The current user per auth mode, the session middleware and `/auth/*`, on Authlib and Starlette's `SessionMiddleware`. |
| [`agent_engine/job_store/`](../../agent_engine/job_store) | `JobRepository` interface, the SQLite implementation and its migrations, and an in-memory one for tests. |
| [`schemas.py`](../../schemas.py) | Wire contracts (`PurificationRequest`, `ProtocolResult`). The frontend's types are generated from the OpenAPI schema they produce. |
| [`agent_engine/models.py`](../../agent_engine/models.py) | Typed pipeline state: `Hit`, `Paper`, `SourceProtocol`, `BufferStep` and the `HitStatus` enum, from creation to the stored report. |
| `agent_engine/agents/agent_body.py` | Pipeline orchestrator. The only module that knows the full sequence of stages. |
| `agent_engine/agents/*` | LLM-backed agents. Each owns one prompt and one output schema; none call each other. |
| `agent_engine/agent_tools/*` | Deterministic I/O adapters (subprocess, HTTP, ODBC, Bolt). No LLM calls. |
| `agent_engine/llm.py` | Single shared model instance. The one place a provider swap happens. |

Every agent call goes through `run_traced` in `agent_engine/trace.py`, which records the call's reasoning summary, output, tokens, time and estimated cost into the job's trace; `llm.py` asks OpenAI's Responses API and Gemini for reasoning summaries.

The dependency direction is strictly `main → agent_body → {agents, agent_tools} → llm`. Tools never import agents. Any module may import `agent_engine/models.py`, which imports nothing from the app.

## Request lifecycle

1. `POST /analyze` mints a UUID, inserts the job as `queued` (with its owner, inputs, the configured model and the app version), registers a `BackgroundTasks` callback, and returns `{job_id, state: "queued"}`. The job exists before the response, so an immediate `GET /status` never 404s.
2. `run_agent_task` marks the job `running`, instantiates the agent, and passes a `status_callback` that records each progress message, with a timestamp, through the repository. It ends by storing the result JSON (`completed`) or the error (`failed`).
3. The frontend polls `GET /status/{job_id}` every 2 seconds and syncs the job's `history` into an on-screen terminal log, so no message is missed between polls and a reopened job replays its progress.
4. On `state == "completed"` the frontend calls `GET /result/{job_id}` once and renders the report.

`/status` returns `state` (`queued`, `running`, `completed`, `failed`, `interrupted`), `progress` (the latest message) and `history` (every message so far); see [backend.md](../system_runbooks/backend.md#status-values).

## Pipeline stages

All stages run sequentially inside `ProteinPurificationAgent.run()`.

### 1. Input resolution

Three input forms are accepted on the same field, discriminated heuristically:

| Input | Detection | Handling |
| --- | --- | --- |
| SSGCID ID (e.g. `MytuD.00516.a`) | `len < 15` and contains `.` | CTTdb lookup for the failed protocol, nucleotide sequence, and taxonomy |
| FASTA | contains `>` | Used directly as the BLAST query |
| UniProt accession / gene name | everything else | Sequence fetched from `rest.uniprot.org/.../{id}.fasta` |

Independently of the branch taken, `GroundingTool.get_uniprot_metadata()` runs a UniProt search to collect organism, subcellular location, transmembrane/signal features, and keywords. This metadata is the only input to synthesis when no literature is found.

For the SSGCID branch, the stored nucleotide sequence is translated in-process: find the first `ATG`, translate to the first stop codon, and emit a FASTA record. No start codon is a hard failure.

### 2. Failed-protocol source

The "failed" reference protocol comes from one of two places, with the user winning:

- `failed_purification_text` in the request (free text pasted in the UI), or
- the CTTdb protocol text for an SSGCID ID.

Either way it is tabulated by `ProtocolAgent` and stored as an entry with `article_title: "User-Provided Failed Protocol"` or `"{id} Failed Protocol ({genus} {species})"`. It is prepended to `purifications` at index 0 of the final result, so the frontend's "Similar Protocols" tab shows the failure first.

### 3. Adaptive BLAST

`blastp` runs as a subprocess against the local `pdbaa` database with a 30-second timeout and `-seg no`. Output is XML (`-outfmt 5`) parsed by Biopython's `NCBIXML`.

Filtering is split between BLAST and Python: `-qcov_hsp_perc` and `-evalue` are passed to the binary, while percent identity is computed per-HSP (`identities / align_length`) and filtered in `blast.py`. Every passing HSP becomes a row, so one alignment can yield multiple rows.

If the strict search returns zero rows, the agent retries once at 20% identity / 20% coverage ("Rescue Mode") while keeping the caller's e-value and max-hits. The relaxed values are hardcoded and not derived from the request, so a caller who already submitted 20% thresholds gets an identical second search. Zero rows after relaxation is a hard failure.

Relaxation is reported only as a transient status string; the report itself does not record that it happened, and the identity column of the BLAST table is the only durable evidence. Because a rescue-mode protocol is grounded in distant homologs that may share little relevant bulk behavior with the target, **any evaluation reporting success rates must separate strict-mode from rescue-mode runs** — they are different claims about the system's reach.

PDB IDs are derived from the accession: the segment after the first `|`, else the first four characters.

### 4. Similarity ranking

For each hit, `ProteinSimilarityTool` queries `data.rcsb.org/rest/v1/core/uniprot/{pdb_id}/1` to attach `uniprot_id`, `organism_name`, and `taxonomy_id`, sleeping 1 second every 10 requests.

Scoring has two modes:

- **Identity only** (no Neo4j, or no target taxonomy ID): `score = pident / 100`.
- **Blended**: `score = 0.5 * (pident / 100) + 0.5 * tax_distance_score`.

The taxonomic term walks a Neo4j `(:Taxon)-[:BELONGS_TO*0..]-(:Taxon)` graph for the shortest path between the target and hit taxa. Rank-weighted penalties (species=1 … domain=8) are summed and normalized against twice the maximum weight; if the direct path is missing, the hit's UniProt lineage is walked upward with an additional penalty per level. Unreachable or erroring lookups return a neutral `0.5`.

Every hit is given the identity-only score first, so a hit with no RCSB metadata or source organism keeps that score and is marked `No RCSB Metadata`. Results are sorted by `similarity_score` descending.

### 5. Literature grounding and extraction

Ranked hits are walked in order until `max_protocols` protocols are collected: the request's value, 1 to 5, default 3. After that, each remaining hit's citation is still looked up from RCSB alone, with no full text or LLM calls, so the report's closed-access list and the per-hit statuses cover every hit; a PMC paper past that point stays `Not Analyzed`, since only its full text says whether it is open. Per hit, before the stop:

1. `GroundingTool.lookup_citation` reads `RCSB /core/pubmed/{pdb_id}` into a `Paper` (PMID, PMC ID, DOI, abstract). No publication, a paper outside PMC, a failed lookup, or a PMID already seen in this run short-circuits with its status.
2. An efetch (`db=pmc`) retrieves article XML, with a 30-second read timeout, retrying up to 3 times on HTTP 400, 429 or 5xx or a non-HTTP error. Articles whose payload says downloading is not allowed are skipped.
3. `MethodsTool.parse_article` selects every section whose title contains `method`, `experimental`, `protocol`, `purification`, or `expression`, or whose `sec-type` names methods, at any depth in `<body>` or `<back>`, each with its subsections and their tables, as tab-separated rows.
4. `ExtractionAgent` pulls verbatim purification prose from the methods sections, returning `None` on its `ERROR::NO_PROTOCOL_FOUND` sentinel. There is no whole-article fallback: when there are no methods sections or they hold no purification text, the hit is `No Protocol Found in Paper`. The paper's `methods_source` is `sections` when they did; `full_text` appears only in reports stored while the fallback existed.
5. `ProtocolAgent` converts that prose into a validated `list[BufferStep]`, with salts given with their concentrations. The model lists the steps in procedure order, and the code numbers them (`step_number`); the model is never asked for a position.

Papers that are in PMC but not open access, or only in PubMed, get their title, journal and year from the entry's `rcsb_primary_citation` and are marked `for_manual_review`. The report lists them under *Closed Access Papers*, in hit order, with no ranking.

Methods text is cut at 50,000 characters (`MAX_METHODS_CHARS`) before it reaches the extraction agent, which bounds a job's token cost. The cut falls at the last line break under the cap, so a paragraph or table row goes whole or not at all.

Each hit records a per-hit outcome in `Hit.status`, surfaced in the BLAST Analysis table: `No RCSB Metadata` (set during ranking), `Not Analyzed`, `Citation Lookup Failed` (RCSB errored, so access is unknown), `No PMC Primary Citation` (no publication, or not in PMC), `Paper Already Found` (an earlier hit cites the same PMID; a paper whose fetch failed is fetched again for the next hit that cites it), `No Open Access` (PMC withholds the full text), `PMC Retrieval Failed` (every fetch failed, so access is unknown), `No Protocol Found in Paper`, `Protocol Found`, `Error Processing`. Exceptions inside the loop are caught per-hit so one bad article cannot abort the run.

### 6. Synthesis

`SuggestedProtocolAgent` runs two LLM calls in series:

- **Planner** receives target metadata (UniProt features plus the theoretical pI and molecular weight computed from the input sequence), the failed protocol, and the successful reference protocols, plus six default heuristics it may depart from (pI clearance, redox state, SEC load volume, nuclease in lysis buffer, imidazole/protease incompatibility, orthogonal column ordering; [ADR-0004](../adr/0004-default-purification-heuristics.md)). It emits free-form chain-of-thought and a raw technical draft.
- **Formatter** receives only the planner's output and restructures it into fixed Steps 0–5 (construct design → expression → lysis → capture → polishing → storage).

Both outputs are returned: `raw_plan` (planner) and `comprehensive_protocol` (formatter).

**The step is skipped when no source protocols were extracted.** It derives the protocol from the differences between the failed attempt and the successful ones, so with none of the latter there is nothing to derive one from. `comprehensive_protocol` and `raw_plan` stay `null`, the reason lands in `ProtocolResult.synthesis_skipped`, and the report shows a "No protocol synthesized" notice. The run reports the gap rather than generating a protocol from metadata alone.

A synthesis exception does not fail the job. `comprehensive_protocol` and `raw_plan` come back `null`, the exception type and message land in `ProtocolResult.error_message`, and the job still reports `COMPLETED` — the BLAST hits and extracted source protocols are unaffected and worth rendering. The report surfaces this as a "Protocol synthesis failed" banner in both the screen and print views.

## Frontend architecture

Hash-routed SPA (`svelte-spa-router`).

| Route | Component | Role |
| --- | --- | --- |
| `/` | `routes/Home.svelte` → `AnalysisForm` | Target input, optional failed protocol, four BLAST parameters |
| `/processing/:jobId` | `routes/Processing.svelte` | `ProgressTracker` + `TerminalLog`, drives the poller |
| `/report/:jobId` | `routes/Report.svelte` → `ResultDashboard` | Tabbed report, print/PDF export |
| `/jobs` | `routes/MyJobs.svelte` → `JobsTable` | The user's jobs: open, follow, delete |
| `/profile` | `routes/Profile.svelte` | Username, email, sign-out, change password |

Stores, polling, and the CSS-only PDF export are described in [frontend.md](../system_runbooks/frontend.md).

## Cross-cutting concerns

**Concurrency.** The synchronous agent runs in Starlette's bounded threadpool. The job store opens a short-lived SQLite connection per operation, in WAL mode with a busy timeout, so worker threads and request handlers never share a connection. Jobs survive a restart; any left `queued` or `running` are marked `interrupted` at startup, and a sweep fails any job still active past `JOB_DEADLINE_MINUTES`. There is no cancellation or eviction.

**Failure philosophy.** Required integrations fail loudly: `ENTREZ_EMAIL` is checked at server startup and when a run starts, the LLM credential on the first LLM call, and BLAST failures end the job. Neo4j degrades to identity-only scoring. CTTdb is used only when internal data is enabled; an unreachable database makes an SSGCID input fail with a misleading UniProt error. See [ADR-0005](../adr/0005-optional-internal-integrations.md).

**Identity and ownership.** Every job endpoint takes the current user from one dependency, `current_user` in `main.py`: `dev@local` in `dev`, a shared placeholder in `deploy` only with an explicit `AUTH_ENABLED=false` (for local image checks), and otherwise the Cognito user from the signed session cookie or a 401. Jobs are stamped with the user's `sub`, and another user's job is a 404.

**Trust boundaries.** Sign-in is the only one. `/analyze` enforces global job caps and input bounds, but there is no per-user quota or rate limit. Errors shown to users never carry exception text; the traceback goes to the server log. See [backend.md](../system_runbooks/backend.md#security-posture). Outbound calls verify TLS and carry a 10-second timeout.

**Contract.** The frontend's wire types are generated from the backend's OpenAPI schema, except for `/status` and `/auth/me`, which have no response model. The form's parameter defaults also differ from the API's; see [backend.md](../system_runbooks/backend.md#request-body). Stored reports are never migrated, so report changes are additive; see [Changing the report](../system_runbooks/backend.md#changing-the-report).
