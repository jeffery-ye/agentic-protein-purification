# ADR-0004: Default purification heuristics

**Status:** Accepted

## Context

An LLM drafting a protocol from reference examples can produce something plausible that violates basic biochemistry: buffering at the target's pI, adding reducing agents to a disulfide-bonded secreted protein, overloading an SEC column. A scientist applies these constraints automatically. A model does not reliably recall them, especially when the reference protocols don't happen to illustrate them.

An earlier version stated six rules as "universal laws" to follow without exception. That had real costs: the rules have legitimate exceptions, the list is incomplete, and the pI rule relied on a pI the model estimated itself.

## Decision

The planner prompt states six **default heuristics**, which it applies unless the reference protocols or the target metadata give a reason to depart:

1. **pI clearance.** Buffer pH at least 1.0–1.5 units from the target's pI.
2. **Redox state.** Reducing agents for cytosolic proteins; none for secreted proteins with native disulfides.
3. **SEC load volume.** No more than 2–5% of column volume, with a concentration step first.
4. **Lysate nuclease.** Lysis buffers include a nuclease and its Mg²⁺ cofactor.
5. **Protease compatibility.** Desalt or dialyze IMAC eluates above 50 mM imidazole before adding protease.
6. **Orthogonal column order.** Don't repeat a separation principle consecutively; follow capture → intermediate → polishing.

The prompt says the list is not complete. The target's theoretical pI and molecular weight are computed from its sequence with Biopython and given to the planner as values for the full input sequence; the planner estimates them only when they can't be computed.

## Consequences

- The rules are inspectable: anyone can read the prompt and see exactly which defaults apply.
- The model can override a default, and the report doesn't say where it did. Nothing checks outputs against the heuristics automatically.
- Computed values are for the whole input sequence. Tags, signal peptides and cleavage shift them, and the prompt tells the planner to allow for that.
