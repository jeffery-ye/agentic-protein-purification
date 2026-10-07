# Documentation Index

- **[Agent Reference](agent_reference/)**: guidance for contributors and AI agents.
  - [Context](agent_reference/context.md): background and known limitations.
  - [Architecture](agent_reference/architecture.md): topology, pipeline stages and data flow.
  - [Style](agent_reference/style.md): coding conventions and visual design.
- **[System Runbooks](system_runbooks/README.md)**: how each part runs and what to check when it misbehaves.
  - [Backend](system_runbooks/backend.md): service, endpoints, job store, pipeline failure triage.
  - [Frontend](system_runbooks/frontend.md): SPA setup, state and polling, print/PDF export.
  - [External Dependencies](system_runbooks/external_dependencies.md): BLAST+, LLM providers, UniProt, RCSB, Entrez, Neo4j, CTTdb.
  - [Deployment](system_runbooks/deployment.md): optional AWS deployment with Terraform.
- **[Architecture Decision Records](adr/README.md)**: the design decisions that shape the pipeline's output.
