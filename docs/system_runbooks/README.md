# System Runbooks

How each part of the system runs, how it behaves, and what to check when it misbehaves.

- **[Backend / Agent Engine](backend.md)**: FastAPI service, job lifecycle, the agent pipeline, endpoints and failure triage.
- **[Frontend](frontend.md)**: Svelte SPA setup, routing, state and polling, and the print/PDF path.
- **[External Dependencies](external_dependencies.md)**: BLAST+ and `pdbaa`, the LLM provider, UniProt, RCSB, NCBI Entrez, and the optional Neo4j and CTTdb integrations.
- **[Deployment](deployment.md)**: optional AWS deployment: bootstrap, Terraform, runtime configuration, logs, backups and teardown.

Setup and run commands are in the [README](../../README.md). `uv run pytest` needs no BLAST database, API key or network. System design is in [architecture.md](../agent_reference/architecture.md).
