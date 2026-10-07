# ADR-0003: Schema-constrained tabular extraction

**Status:** Accepted

## Context

Comparing purification protocols across papers needs structure: per step, what buffer, what pH, what salt, what additives. Methods sections describe this inconsistently, with compositions spread across sentences and buffers defined elsewhere in the paper.

## Decision

Extract in two passes with different contracts.

**Pass one** (`ExtractionAgent`) returns the verbatim purification prose from the methods sections, or the sentinel `ERROR::NO_PROTOCOL_FOUND`. The prose is kept in the result as `purification_text` and shown beside the table. When it finds nothing, the hit is recorded as `No Protocol Found in Paper` and nothing is tabulated.

**Pass two** (`ProtocolAgent`) returns a validated list of `BufferStep`: `purification_step`, `buffer_name`, `buffer_composition`, `ph`, `salt_type` (salts with their stated concentrations) and `buffer_supplement`. Every field except the step name is optional, and the prompt requires `null` rather than inference when a detail is absent. The model lists steps in procedure order and the code numbers them (`step_number`). The prompt also discards steps whose buffer composition is not given ("washed with PBS") and asks for specific step names ("M2 anti-FLAG affinity resin - Wash", not "Affinity column wash").

## Consequences

Structured output makes the comparison table possible and gives the planner clean input. With every field optional and inference forbidden, a sparse table reflects what the paper omitted rather than plausible guesses. The verbatim prose lets a reader check the table against the source without opening the paper.

The tradeoffs:

- **Discarding vague steps loses real steps.** A protocol that says "washed with PBS" did perform a wash, but the table cannot distinguish "no wash" from "wash with unstated composition".
- **Two LLM calls per article** instead of one.
