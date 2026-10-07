# Agent Guidelines

AGENTS.md serves primarily as a router. Keep it thin.

## Documentation Structure
- **Index**: [docs/index.md](docs/index.md)
- **Agent Reference**: [docs/agent_reference/](docs/agent_reference/) ([Context](docs/agent_reference/context.md), [Style](docs/agent_reference/style.md), [Architecture](docs/agent_reference/architecture.md))
- **System Runbooks**: [docs/system_runbooks/](docs/system_runbooks/README.md) ([Backend](docs/system_runbooks/backend.md), [Frontend](docs/system_runbooks/frontend.md), [External Dependencies](docs/system_runbooks/external_dependencies.md), [Deployment](docs/system_runbooks/deployment.md))
- **Architecture Decisions**: [docs/adr/](docs/adr/README.md)
- **Local only**: `docs/plans/` is left out of the public release, and `docs/publication_information/` is gitignored. Never commit the latter or quote it anywhere.

## Core Conventions
- Use Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`).
- Keep documentation brief and high-signal.
- Reference relevant runbooks and specifications before modifying core systems.
