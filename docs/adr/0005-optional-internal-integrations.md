# ADR-0005: Internal integrations are optional

**Status:** Accepted

## Context

Two data sources are specific to the originating lab. The CTTdb/SSGCID SQL Server holds the lab's failed-purification records and authenticates through Windows integrated auth on a domain-joined machine. The Neo4j taxonomy graph must be populated and served locally. Neither is available to an external user or to CI, and requiring them would make the public repository unusable outside the lab.

## Decision

Both are optional:

- **Neo4j.** `create_driver()` returns `None` when its three environment variables aren't all set or the connection fails, and similarity falls back to BLAST identity alone.
- **CTTdb.** Used only for SSGCID IDs, and only when internal data is enabled (`INTERNAL_DATA`, on by default in `dev`, off in `deploy`). With it off, an SSGCID ID fails at once and asks for the FASTA sequence. FASTA and UniProt inputs never touch it, and a user can paste a failed protocol instead.

Required dependencies, BLAST and the LLM credential, fail loudly instead.

## Consequences

Clone, install, set a few environment variables, and the system runs end to end. Homolog search, literature mining and protocol synthesis need neither integration.

The limits:

- **Taxonomic scoring applies only to SSGCID inputs.** The CTTdb taxonomy row is the only source of a target taxonomy ID, so FASTA and UniProt inputs are scored by identity alone even with Neo4j running.
- **A CTTdb failure is misreported.** With internal data on but the database unreachable, the SSGCID ID falls through to UniProt resolution, and the job fails with a UniProt error rather than a database one.
- **Results are not labelled by mode.** Runs of the same target with and without the internal databases can rank hits differently, and nothing in the report distinguishes them.
