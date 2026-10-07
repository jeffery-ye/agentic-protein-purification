# Agent Reference: Context

Background and known limitations of Agentic Protein Purification.

## Background

A research prototype that proposes recombinant protein purification protocols grounded in the literature for homologous proteins. Given a target and, optionally, a failed purification attempt, it finds homologs with solved structures, retrieves their papers' methods sections from PMC, extracts and tabulates the purification steps, and synthesizes a protocol. It accompanies the paper cited in the [README](../../README.md).

The system is a FastAPI backend running a `pydantic-ai` pipeline (UniProt → local BLAST+ → RCSB/PMC → LLM extraction and synthesis) and a Svelte SPA. See [architecture.md](architecture.md). Locally it runs in `dev` mode with no sign-in or cloud services. The [AWS deployment](../system_runbooks/deployment.md) is optional.

## Known limitations

- **Structural coverage.** Only homologs with PDB structures are searched ([ADR-0001](../adr/0001-blast-against-pdbaa.md)), and only open-access PMC papers are read. Closed-access papers are listed for manual review.
- **Single process.** Jobs live in one SQLite file, which assumes one writer process. Jobs can't be cancelled, and nothing is evicted automatically.
- **Global limits only.** `/analyze` enforces a cap on jobs running at once, an optional daily cap and input bounds. There is no per-user quota or rate limit.
- **Stored reports aren't migrated.** A report keeps the shape it had when its job ran; see [Changing the report](../system_runbooks/backend.md#changing-the-report).
- **Internal integrations.** CTTdb and Neo4j work only inside the originating lab, and taxonomic scoring applies only to SSGCID inputs ([ADR-0005](../adr/0005-optional-internal-integrations.md)).
