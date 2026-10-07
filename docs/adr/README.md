# Architecture Decision Records

Decisions that shape what the pipeline produces, with their reasoning and tradeoffs. Each record has **Status**, **Context**, **Decision** and **Consequences**. A decision that reverses an existing one gets a new record, and the old one is marked superseded.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-blast-against-pdbaa.md) | Search pdbaa rather than nr | Accepted |
| [0002](0002-two-stage-synthesis.md) | Two-stage planner/formatter synthesis | Accepted |
| [0003](0003-schema-constrained-extraction.md) | Schema-constrained tabular extraction | Accepted |
| [0004](0004-default-purification-heuristics.md) | Default purification heuristics | Accepted |
| [0005](0005-optional-internal-integrations.md) | Internal integrations are optional | Accepted |

A record belongs here when the decision was contested, has consequences a contributor would otherwise rediscover, or constrains what the system can become. Implementation and operational detail belongs in the [architecture reference](../agent_reference/architecture.md) and the [runbooks](../system_runbooks/README.md).
