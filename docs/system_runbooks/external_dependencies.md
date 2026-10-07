# Runbook: External Dependencies

Every external system the pipeline touches, how to configure it, and how the pipeline behaves when it is unavailable.

## Summary

| Dependency | Required | Configuration | Behavior when absent |
| --- | --- | --- | --- |
| BLAST+ CLI | **Yes** | must be on `PATH` | Job fails: `BLAST Error` |
| `pdbaa` database | **Yes** | `BLAST_DB_PATH` | Job fails: "BLAST database not configured" |
| An LLM provider | **Yes** | `LLM_PROVIDER`, `LLM_MODEL`, `API_KEY` | Server starts; every LLM call fails, so jobs end in a synthesis error |
| UniProt REST | Yes, implicitly | none | Sequence fetch failure is fatal; metadata failure is silent |
| RCSB PDB REST | Yes, implicitly | none | Per-hit degradation to identity-only scoring |
| NCBI Entrez / PMC | **Yes** | `ENTREZ_EMAIL`; optional `NCBI_API_KEY` | Address unset: **server refuses to start**. Service errors: hits marked "PMC Retrieval Failed", run continues |
| Neo4j taxonomy | No | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | Identity-only similarity scoring |
| CTTdb / SSGCID SQL Server | No | `DB_SERVER`, `DB_NAME`, `DB_DRIVER`; `INTERNAL_DATA` | Off: SSGCID IDs are refused. On but unreachable: they fail as unknown UniProt IDs |

`.env` in the repository root is the single configuration source for the backend, loaded by `load_dotenv()` in `llm.py`, `agent_body.py`, `protein_similarity_tool.py`, and `cttdb_protocols.py`. Copy [`.env.example`](../../.env.example) to start. `.env` is gitignored — never commit it.

## BLAST+ and the pdbaa database

The hardest part of first-time setup, and the most common source of "it doesn't work".

**1. Install the CLI.** Download the BLAST+ command-line tools from [NCBI](https://blast.ncbi.nlm.nih.gov/doc/blast-help/downloadblastdata.html) and ensure `blastp` is on `PATH`:

```bash
blastp -version
```

**2. Download the database.** Fetch the pre-formatted `pdbaa` archive from the [NCBI FTP site](https://ftp.ncbi.nlm.nih.gov/blast/db/) and extract it. `pdbaa` is protein sequences from PDB structures — small (tens of MB) compared to `nr`, and deliberate: every downstream stage needs a PDB ID to resolve a primary citation, so searching PDB-derived sequences means every hit is usable.

**3. Point `BLAST_DB_PATH` at the database stem.** This is the path *prefix* shared by the `.phr`/`.pin`/`.psq` files, not a directory and not one of those files:

```
BLAST_DB_PATH = "C:\db\pdbaa\pdbaa"
```

Verify independently of the app:

```bash
blastdbcmd -db "$BLAST_DB_PATH" -info
```

**How it is invoked.** `agent_tools/blast.py` shells out to `blastp` with `-outfmt 5` (XML) writing to a temporary file, plus `-qcov_hsp_perc`, `-evalue`, `-max_target_seqs`, and `-seg no`. Both temp files are removed in a `finally` block. There is a hard **30-second timeout**; on a cold filesystem cache the first query of a session can approach it, and a timeout raises rather than retrying.

Note that only coverage and e-value are enforced by the binary. Percent identity is computed per-HSP in Python (`identities / align_length`) and filtered afterwards, so BLAST's own identity semantics do not apply.

## LLM provider

**Configuration:** `LLM_PROVIDER`, `LLM_MODEL` and `API_KEY`; `bedrock` uses AWS credentials instead of `API_KEY`.

`agent_engine/llm.py` resolves one shared model used by all four agents. `LLM_PROVIDER` picks the backend — `gemini` (default), `openai`, `anthropic` or `bedrock` — and `API_KEY` is the single credential slot for the first three, holding whichever provider's key you're using. `LLM_MODEL` names the model within that provider.

| `LLM_PROVIDER` | pydantic-ai model class |
| --- | --- |
| `gemini` (default) | `GoogleModel` (Generative Language API) |
| `openai` | `OpenAIResponsesModel` (`OpenAIChatModel` with `LLM_BASE_URL`) |
| `anthropic` | `AnthropicModel` |
| `bedrock` | `BedrockConverseModel` (Converse API) |

`LLM_MODEL` is required, by the name the provider's API uses; there is no default model. The paper's original test cases ran on `gemini-2.5-pro`, which Google's Gemini API retires on 2026-10-16.

`bedrock` needs no `API_KEY`. It signs requests with the default AWS credential chain: `AWS_PROFILE` or keys locally, the instance role in deploy. The region comes from `AWS_REGION` or `AWS_DEFAULT_REGION`, defaulting to `us-east-1`. The model must be enabled for the account in the Bedrock console. Until it is, every Converse call returns `Operation not allowed`. A Bedrock API key (`AWS_BEARER_TOKEN_BEDROCK`) is rejected in deploy.

`API_KEY` is passed explicitly into that provider's client — it does not read `GEMINI_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` from the environment, so switching providers is just changing `LLM_PROVIDER` and `API_KEY` together, never juggling multiple credential variables.

The model is resolved on the first LLM call, not at import. The server starts without credentials, and a missing `API_KEY` or an unrecognized `LLM_PROVIDER` fails that job with a clear `ValueError`.

**Custom endpoints.** `LLM_BASE_URL` points the `openai` provider at a self-hosted or proxied OpenAI-compatible server. `OPENAI_BASE_URL` works too, but `LLM_BASE_URL` takes precedence. Because `OPENAI_BASE_URL` is a widely used global, it is ignored for the other providers; `LLM_BASE_URL` belongs to this application, so setting it alongside a non-`openai` provider is reported as an error rather than silently ignored.

Adding another provider means adding its `pydantic-ai-slim` extra in `pyproject.toml` — `[google,openai,anthropic,bedrock]` are installed today — plus a branch in `resolve_model` and an entry in `PROVIDERS`.

**Cost profile per run:** up to 12 calls — one extraction plus one tabulation per collected protocol (max 5 each), plus one planner and one formatter call. The extraction calls carry whole methods sections and the planner carries every collected protocol verbatim, so context is large. The global job caps bound how many runs happen, but there is **no per-call cap, no caching, and no cheaper-model tier** — an identical re-run costs full price again.

Quota exhaustion or a safety block shows up as a "Protocol synthesis failed" banner; see [backend.md](backend.md#triage).

## UniProt REST

**Configuration:** none. No key, no registration.

Used in three places:

| Call | Endpoint | Failure handling |
| --- | --- | --- |
| Sequence fetch | `rest.uniprot.org/uniprotkb/{id}.fasta` | Returns `None` → job fails with a clear message |
| Target metadata | `rest.uniprot.org/uniprotkb/search?query=...&size=1` | Any exception → `None`, logged, run continues with less context |
| Hit lineage | `rest.uniprot.org/uniprotkb/{id}?fields=lineage_ids` | Returns `[]` → taxonomic fallback yields a neutral 0.5 |

Two caveats worth knowing:

- The sequence fetch is the one outbound call that uses `urllib.request.urlopen` rather than the shared `requests` session. It carries a 10-second timeout like everything else; a timeout is caught and surfaces as the "Failed to retrieve sequence" job error.
- The metadata search takes the **first result for a free-text query**, so a gene name or ambiguous identifier can silently resolve to the wrong protein — and the resulting organism and topology then feed synthesis. When a generated protocol references an unexpected organism, check the `[GroundingTool] Match found:` log line first.

## RCSB PDB REST

**Configuration:** none.

| Call | Endpoint | Purpose |
| --- | --- | --- |
| UniProt xref | `data.rcsb.org/rest/v1/core/uniprot/{pdb_id}/1` | Attaches `uniprot_id`, `organism_name`, `taxonomy_id` |
| Primary citation | `data.rcsb.org/rest/v1/core/pubmed/{pdb_id}` | Reads `rcsb_pubmed_central_id` |

Both use a 10-second timeout. The UniProt xref call runs through a `requests` session with a 3-retry backoff adapter for 500/502/504 and sleeps 1 second every 10 hits; the citation call has no retry adapter. Request failures degrade that hit to identity-only scoring rather than failing the run.

Structures with no PMC-indexed primary citation are marked `No PMC Primary Citation` and skipped — common, and a significant source of paper dropout.

## NCBI Entrez / PMC

**Configuration:** `ENTREZ_EMAIL`, validated by `settings.entrez_email()` at server startup and when a run constructs `GroundingTool`, which assigns it to `Entrez.email`. NCBI's usage policy requires a real contact address for whoever is making the requests, so this is deliberately fail-fast: the backend raises at startup rather than letting a job discover it minutes in.

Full text is retrieved by `fetch_pmc_xml` in `grounding_tool.py`, which sends the request `Entrez.efetch(db="pmc", id=..., retmode="xml")` sent and returns the same text, but with a 5-second connect and 30-second read timeout and no retries of its own: Biopython's `urlopen` has no timeout, so a stalled connection hung the job. A lock spaces fetches from concurrent jobs at NCBI's rate limit. Each fetch is tried up to 3 times, with a growing delay, on HTTP 400, 429 or 5xx or a non-HTTP error such as a timeout. Every failed fetch, including any other HTTP error, marks the hit `PMC Retrieval Failed`, not `No Open Access`, since it says nothing about access. So does a reply that isn't an article (an `<error>` for an unknown ID, or unparseable XML). A reply containing "does not allow downloading", or an article with no `<body>`, is withheld, and the hit is marked `No Open Access`.

Retrieved XML is parsed by `methods_tool.py`, which selects sections by keyword match on their titles (`method`, `experimental`, `protocol`, `purification`, `expression`) at any depth in `<body>` and `<back>`, with `sec-type` as a backup, tables within them included. There is no whole-article fallback: an article whose methods sections yield no purification text gives no protocol. PubTator3, BioC and Europe PMC were evaluated and not adopted.

Without `NCBI_API_KEY`, fetches run at NCBI's unauthenticated rate limit (3/second); with it, the key is sent on each fetch and the limit is 10/second.

## Neo4j taxonomy graph (optional)

**Configuration:** `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`. `NEO4J_HOME` is only a shell variable for launching a local server by hand, as below; the application never reads it, which is why it is not in `.env.example`.

Local server, from the comment at the top of `protein_similarity_tool.py`:

```bash
set NEO4J_HOME=C:\PATH\TO\YOUR\DB
%NEO4J_HOME%\bin\neo4j console
```

**Expected schema.** Nodes labelled `Taxon` with properties `taxonId` (matched as a **string**), `name`, and `rank`, connected by `BELONGS_TO` relationships. The query finds the shortest undirected variable-length path between the target and hit taxa.

**Scoring.** Ranks along the path are weighted species=1 through domain=8, summed, and normalized as `1 - (total / (2 * sum(all weights)))`. A perfect score of 1 is halved to avoid a degenerate self-match. Walking up the hit's UniProt lineage to find a match adds 1 penalty per level. Query errors or unreachable taxa return a neutral `0.5`.

With Neo4j configured, similarity is `0.5 * (pident/100) + 0.5 * tax_score`; without it, `pident/100`. Either way the graph only ever **reorders** hits — it never adds or removes them.

**Graceful absence.** `create_driver()` returns `None` when the three variables are not all set, or when `verify_connectivity()` fails, logging `Neo4j not configured, scoring by BLAST identity only`. The taxonomic term is also skipped whenever the target has no taxonomy ID, which is the case for **all non-SSGCID inputs** — only the CTTdb branch supplies one. In practice, a FASTA or UniProt input is always scored by identity alone even with Neo4j running.

**Caveat:** the driver is closed in the `finally` block of `calculate_similarity`, so a `ProteinSimilarityTool` instance is single-use. The pipeline constructs a fresh one per run, so this is latent rather than active.

## CTTdb / SSGCID SQL Server (optional)

**Configuration:** `DB_SERVER`, `DB_NAME` (`SSGCID`), `DB_DRIVER` (default `{ODBC Driver 17 for SQL Server}`). Requires the matching Microsoft ODBC driver installed locally.

Authentication is `Trusted_Connection=yes` — Windows integrated auth. There is no username/password path, so this works only on a domain-joined machine with access to the SSGCID network. **Users outside the originating lab cannot use this integration**; see [ADR-0005](../adr/0005-optional-internal-integrations.md).

`get_cttdb_info(protein_id)` runs three parameterized queries against a `{ID}%` prefix match and returns `(protocol_text, sequence, taxonomy_row)`:

| Query | Tables | Returns |
| --- | --- | --- |
| Protocol | `Protocol` → `RealProcessInstance` → `Construct` | `ProtocolText` for `RealProcessID` in (190, 191, 192, 193, 194, 241, 267, 280, 304) — the purification/refolding workflow steps |
| Sequence | `Construct` | `NTSeq`, the nucleotide sequence |
| Taxonomy | `Organism` | `(Genus, Species, Strain, TaxonomyID)`, matched on the first 5 characters of the ID as the organism code (e.g. `MytuD` from `MytuD.00516.a`) |

The nucleotide sequence is translated in-process: first `ATG` to the first stop codon. **No start codon is a hard job failure.**

Any `pyodbc.Error` returns `(None, None, None)`; `agent_body` catches it and sets `fasta_id` to the raw SSGCID ID. That ID does not start with `>`, so the next step tries to resolve it as a UniProt accession, which fails, and the job ends with **"Failed to retrieve sequence for UniProt ID MytuD.00516.a. Please provide a full FASTA sequence."** The pipeline never reaches BLAST. If SSGCID inputs fail with a UniProt message, suspect the database connection — the error names the wrong subsystem.

Because the taxonomy row is the only source of a target taxonomy ID, losing CTTdb also silently disables Neo4j taxonomic scoring.

## Python and Node dependencies

The Python version and install steps are in the [README](../../README.md). The dependency workflow is in [style.md](../agent_reference/style.md#repository-conventions).

CI (`.github/workflows/ci.yml`) runs on every pull request and push to `main`: `ruff check`, `ruff format --check` and `pytest` for the backend, and `npm run check` plus a production build for the frontend.
