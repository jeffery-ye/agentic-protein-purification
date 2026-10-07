# ADR-0001: Search pdbaa rather than nr

**Status:** Accepted

## Context

The pipeline needs homologs of the target whose purification protocols can be recovered from the literature. That takes a chain of identifiers: sequence hit → PDB entry → primary citation → PMC full text.

`nr` is more sensitive, but most of its entries have no PDB structure, and without a PDB ID there is no route to a primary citation through RCSB. A larger candidate list would yield fewer usable protocols and cost far more to download, store and search. `pdbaa` holds exactly the sequences with PDB structures, in tens of megabytes rather than hundreds of gigabytes.

## Decision

Search `pdbaa` locally with the BLAST+ CLI. `BLAST_DB_PATH` points at the database stem, and every hit carries a resolvable PDB ID. BLAST runs as a local subprocess rather than through NCBI's remote API, avoiding network latency and rate limits.

## Consequences

Every hit can enter the PDB → PMC chain, and a search takes seconds.

The ceiling is structural coverage. A protein family with no solved structures returns nothing however permissive the thresholds are, and the automatic relaxation to 20% identity and coverage cannot help. The failure message suggests an `nr` search as a manual fallback, which the system does not implement.

Local BLAST is also the hardest setup step: install the CLI, download and extract the archive, and point an environment variable at a path *prefix* rather than a file.
