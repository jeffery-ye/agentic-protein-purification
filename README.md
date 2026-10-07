# Agentic Protein Purification

An AI-driven system that creates and optimizes recombinant protein purification protocols, grounded in primary citations for homologous proteins. Given a target protein and, optionally, a failed purification attempt, it finds homologs with solved structures, mines their papers' methods sections, tabulates the purification steps, and synthesizes a protocol with multi-agent LLM workflows and bioinformatics tools.

Developed by Jeffery Ye at Seattle Children's Research Institute (SCRI), part of Seattle Children's Hospital. The associated paper is available as a [preprint](https://doi.org/10.64898/2026.03.03.709341).

## Prerequisites

- **uv:** Install from [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/). It provisions a supported Python automatically, so a system Python is not required.
- **Python:** 3.10, 3.11 or 3.12, declared in `pyproject.toml` and enforced by `uv`. 3.13+ is not supported by the pinned dependencies.
- **Node.js:** 22 or later, with npm, for the frontend and the combined `npm run dev`.
- **BLAST+:** Install the command-line tools from [NCBI](https://blast.ncbi.nlm.nih.gov/doc/blast-help/downloadblastdata.html) and add them to your `PATH`.
- **pdbaa database:** Download the pre-formatted database from the [NCBI FTP site](https://ftp.ncbi.nlm.nih.gov/blast/db/).
- **Internet access:** The pipeline calls the UniProt, RCSB PDB and NCBI Entrez APIs.
- **Optional:** The SSGCID database and a Neo4j taxonomy graph. The application works without them.

## Configuration

1. Create a `.env` file in the root directory, based on [.env.example](.env.example).
2. Set `BLAST_DB_PATH` to the `pdbaa` database stem inside your extracted folder (e.g. `C:\db\pdbaa\pdbaa`), not to a directory.
3. Set `ENTREZ_EMAIL` to a real contact address. NCBI requires one on every Entrez request.
4. Set `LLM_MODEL` to the model to use (there is no default) and `API_KEY` to your provider's credential. The default provider is Gemini; set `LLM_PROVIDER` to `openai`, `anthropic` or `bedrock` to use another, and `API_KEY` always holds that provider's key (`bedrock` uses your AWS credentials instead). See [docs/system_runbooks/external_dependencies.md](docs/system_runbooks/external_dependencies.md).

The backend refuses to start if `ENTREZ_EMAIL` is missing. The provider credential and model are checked on the first LLM call, so a missing one fails jobs rather than the startup.

## Running the Application

First-time setup, once:
```bash
uv sync
npm install
npm install --prefix purification-rescue-frontend
```

Then start the backend and frontend together:
```bash
npm run dev
```

This runs `uv run fastapi dev main.py` and the frontend's `npm run dev` concurrently, each prefixed in the combined log. Stop both with Ctrl+C.

To run them separately, in two terminals:
```bash
uv run fastapi dev main.py
```
```bash
npm run dev --prefix purification-rescue-frontend
```

`uv sync` creates `.venv` from the committed `uv.lock`, downloading a supported Python if needed. Dependencies are declared in `pyproject.toml`; edit that and run `uv lock` to change them.

## Usage

Open `http://localhost:5173` and enter the target as a UniProt ID or a FASTA sequence (SSGCID IDs need the internal database). Optionally paste a failed purification protocol and adjust the search settings: minimum identity and coverage, maximum e-value, hits and protocols. A job takes a few minutes. The report shows the suggested protocol, the similar protocols extracted from the literature with closed-access papers to review by hand, the BLAST hits, and the job's LLM calls, and can be saved as a PDF.

## Tech Stack

- **Backend:** FastAPI, PydanticAI (LLM agents)
- **Frontend:** Svelte + Vite
- **Other:** local BLAST+; the UniProt, RCSB PDB and NCBI Entrez APIs; optional Neo4j (taxonomic scoring) and SQL Server via pyodbc (SSGCID/CTTdb access)

## Tests

```bash
uv run pytest
```

```bash
npm test --prefix purification-rescue-frontend
```

Neither needs a BLAST database, an API key or network access.

## Documentation

Architecture references, runbooks and design decisions are indexed in [docs/index.md](docs/index.md). The repository also includes infrastructure for an optional web deployment on AWS: Terraform in `infra/`, the server configuration in `deploy/`, and a manually triggered deploy workflow. None of it runs unless you apply it; see the [deployment runbook](docs/system_runbooks/deployment.md).

## License

Copyright (c) 2026 Seattle Structural Genomics Center for Infectious Disease (SSGCID). Released under the Artistic License 2.0; see [LICENSE.txt](LICENSE.txt).
