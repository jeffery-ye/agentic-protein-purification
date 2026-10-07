# ADR-0002: Two-stage planner/formatter synthesis

**Status:** Accepted

## Context

The final protocol has to be the product of reasoning across several reference protocols, a failed attempt and the target's metadata, and it has to arrive in a fixed structure a bench scientist can follow. One call asked to do both tends to sacrifice one: a strict template suppresses the reasoning, and an open-ended prompt produces prose that is hard to render or compare across runs.

## Decision

`SuggestedProtocolAgent` makes two sequential LLM calls:

1. **Planner.** Receives all inputs plus the default heuristics ([ADR-0004](0004-default-purification-heuristics.md)) and writes its reasoning and a raw technical draft, without regard to format. Its prompt includes a mental-simulation step for buffer mismatches, tag interference and reagent clashes.
2. **Formatter.** Receives only the planner's output and restructures it into fixed Steps 0–5: construct design, expression, lysis, capture, polishing, storage.

Both are stored: `raw_plan` from the planner and `comprehensive_protocol` from the formatter. Synthesis is skipped when no source protocols were extracted, since there is nothing to compare the failed attempt against.

## Consequences

The formatted protocol is consistent enough to render as Markdown and compare between runs, while the planner reasons freely. `raw_plan` makes that reasoning auditable: the report's Job Info tab shows why a buffer pH was chosen, not just what it was.

The costs:

- Two calls instead of one, on the longest contexts in the pipeline.
- The formatter sees only what the planner emitted. If the planner omits a step, the formatter either drops it or invents one to fill the template, and nothing checks which happened.
- The formatter is also asked to check the plan for inconsistencies, which is a weak guarantee: a model reviewing output from its own family is not an independent check.
