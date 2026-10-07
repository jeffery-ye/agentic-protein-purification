# Agent Reference: Style

Coding conventions, formatting standards, and styling guidelines. These describe the conventions the existing code actually follows; where the codebase is inconsistent, the intended convention is stated and the deviation noted.

Python formatting and import order are enforced by `ruff` and checked in CI, so that part is not a matter of taste — run `uv run ruff check --fix .` and `uv run ruff format .` before committing. Everything below that `ruff` does not enforce is a convention.

## Python

**Layout.** `ruff format` with a 100-character line length. Import order is enforced by `ruff`'s `I` rules — stdlib, third-party, then local — and `agent_engine` modules use explicit relative imports (`from ..agent_tools.blast import run_blastp`). The lint set is deliberately conservative (`E4`, `E7`, `E9`, `F`, `I`); `UP` and `B` are worth enabling once the codebase settles.

**Naming.** `snake_case` for functions and variables, `PascalCase` for classes and Pydantic models, `SCREAMING_SNAKE_CASE` for module-level constants read from the environment (`DB_SERVER`, `NEO4J_URI`, `PURIFICATION_PROCESS_IDS`). Private helpers on the orchestrator are prefixed with a single underscore (`_run_adaptive_blast`, `_rank_similarities`).

**Class shape.** Tools and agents are thin classes instantiated per use, holding configuration in `__init__` and exposing one or two public methods. Agents own exactly one `pydantic_ai.Agent` plus one public entry point (`run`, `find_protocol`). Stateless deterministic work is a module-level function instead (`run_blastp`, `get_cttdb_info`).

**Return contracts.** Prefer a typed result object over raising across a module boundary. `AgentResult` is a dataclass with a `success` flag, and orchestrator helpers signal failure by returning an `AgentResult` that the caller detects with `isinstance`. Tools return `None` or `[]` for "not found" and reserve exceptions for genuinely broken preconditions.

**Type hints.** Applied to public signatures and Pydantic/dataclass fields; frequently omitted on private helpers. New code should annotate both.

**Docstrings.** Triple-quoted, one short paragraph, describing intent and return shape rather than restating the signature. Modules whose purpose is not obvious from the filename carry a module-level docstring.

**Logging.** The codebase logs with `print()` using a bracketed subsystem tag so stdout can be read as an execution trace:

```python
print(
    f"--- [BLAST Tool] Completed. Raw Hits: {raw_hit_count} | Passing Filters: {len(filtered_results)} ---"
)
print(f"   [Agent] Protocol found for {pdb_id}")
```

Tags in use are `[Agent]`, `[BLAST Tool]`, `[SimilarityTool]`, `[GroundingTool]`, `[CTTdb]`, `[Neo4j]`, and `[UniProt API]`. Top-level stage transitions use the `--- [Tag] message ---` form; nested detail is indented with three spaces. Keep this convention for new tools — the terminal transcript is the primary debugging surface. Note that these logs are server-side only and are entirely separate from the user-facing status strings sent through `status_callback`.

**User-facing status strings.** Progress messages passed to `status_callback` are short present-participle phrases ending in an ellipsis (`'Running BLAST (Strict: 90.0% Cov)...'`, `'Synthesizing final protocol with LLM...'`). They are rendered verbatim in the browser, so they must stay free of stack traces, file paths, and internal identifiers. The sentinel values `COMPLETED` and `ERROR` are reserved and uppercase.

**Per-hit status values.** Statuses are `HitStatus` members in `agent_engine/models.py`, whose values are title-cased human phrases (`"Protocol Found"`, `"No Open Access"`) and are also the display labels. Stored reports hold these strings and the frontend matches some exactly in `BlastResults.getStatusIcon`, so never reword one; add a new member instead.

**Prompts.** Each agent's instructions live inline in its `__init__` as a triple-quoted string. Structure them with Markdown headings and numbered directives, state hard constraints as explicit rules, and give a worked correct/incorrect example where the desired granularity is ambiguous (see `ProtocolAgent`). Where an agent must be able to report "nothing here", define a machine-checkable sentinel (`ERROR::NO_PROTOCOL_FOUND`) rather than relying on prose detection.

**Secrets and configuration.** Read through `os.getenv` after a module-level `load_dotenv()`. Never commit credentials; `.env` is gitignored and `.env.example` is the documented template. A required variable should fail fast at startup, through a `settings.py` function the app calls at import or in its lifespan hook (a missing `ENTREZ_EMAIL` stops the server), not by raising in an agent module at import, which would make it untestable without the variable. The LLM credential is the exception: `llm.py` checks it on first use so the deployed server can boot without it. An optional variable should log a downgrade notice and return a null object (`ProteinSimilarityTool.create_driver` returns `None`).

**SQL.** Always parameterized with `?` placeholders through `pyodbc`; never string-interpolated. Queries are module-level constants with the parameter tuple assembled at the call site.

**Tests.** `pytest`, run with `uv run pytest` from the repository root. Tests live in `tests/`, named `test_<module>.py`, with shared fixtures in `conftest.py` and sample payloads in `tests/fixtures/`.

No test may touch the network, a database, BLAST+, or an LLM. External calls are stubbed by monkeypatching the seam closest to the boundary — `subprocess.run` for BLAST, the `http_session` attribute for RCSB, the module-level `get_cttdb_info` and agent classes for everything else. `conftest.py` sets dummy values for the environment variables the app validates at startup and the pipeline at run time.

`tests/test_pipeline_snapshot.py` runs a whole job with only those boundaries stubbed and compares the stored report with `tests/fixtures/pipeline_snapshot.json`; after a deliberate change, regenerate it with `UPDATE_SNAPSHOTS=1` and review the diff.

Name a test for the behavior it pins rather than the function it calls, and give it one assertion target. Where a test documents a known defect rather than desired behavior, say so in the docstring and prefix the name with `test_known_gap_` so a future fix shows up as a deliberate change.

## TypeScript / Svelte

**Language level.** Svelte 5 runes only; stores stay on `svelte/store` until rewritten. Every `<script>` block has `lang="ts"`. Props come from `$props()` with a typed `Props` interface, local state is `$state`, computed values are `$derived`, side effects are `$effect`, events are attributes (`onclick`, `onsubmit`), and content is passed as snippets (`{@render children?.()}`). The legacy forms (`export let`, `$:`, `on:` directives, `<slot>`, `afterUpdate`, `svelte/legacy` helpers) are not used. The stores in `src/lib/stores/` are still `svelte/store` writables, read in components through `$store` auto-subscription.

**Layout.** 2-space indentation, single quotes in `.ts` files, double quotes in Svelte markup attributes.

**Naming.** `camelCase` for variables and functions, `PascalCase` for components and interfaces. API payload fields keep the backend's `snake_case` verbatim (`fasta_id`, `min_percent_identity`, `comprehensive_protocol`) so the wire format is greppable across both languages. Do not rename them at the boundary.

**Types.** Components import wire types from `src/lib/types/api.ts`. Most are aliases of `openapi.d.ts`, generated from the backend's schema (`npm run gen:api`); a response without a backend model is typed by hand there, with optional fields as `?: T | null`. Don't reach for `any`.

**Stores.** State lives in custom store factories in `src/lib/stores/job.ts` that close over `writable()` and expose only `subscribe` plus intent-named mutators (`initiate`, `setStatus`, `complete`, `fail`, `reset`). Components never receive the raw writable and never call `set` directly.

**Side effects.** Network and timer work belongs in `src/lib/api/`, not in components. Components call into it from `onMount` and tear it down in `onDestroy`. Any module-level interval or subscription must have a matching stop function (`stopPolling`) and an idempotency guard.

**Component boundaries.** Components are presentational and receive data through props with a safe default (`let { results = [] }: Props = $props()`). Every list renders an explicit empty state. Formatting helpers (`truncate`, `getIdentityColor`, `getStatusIcon`) stay local to the component that uses them.

**HTML injection.** Markdown from the LLM is rendered with `{@html}` only after `DOMPurify.sanitize(marked.parse(...))`. Never introduce an unsanitized `{@html}`.

## Visual design

Tailwind 4 utility classes inline in markup. A `<style>` block is used only for print rules and keyframe animations that Tailwind cannot express. Tailwind 4 has no `tailwind.config.js`: the theme and plugins (including `@tailwindcss/typography`, which styles the protocol Markdown) are declared in `src/app.css`.

**Component library.** [shadcn-svelte](https://shadcn-svelte.com), built on Bits UI. Its components are copied into the repo under `src/lib/components/ui/` and imported through the `$lib` alias (`import { Badge } from "$lib/components/ui/badge"`). Installed so far: Table, Checkbox, Button, Badge, AlertDialog, DropdownMenu, Tooltip, and the Data Table helpers (`data-table/`, on `@tanstack/table-core`). Add more with `npx shadcn-svelte@latest add <name>` from `purification-rescue-frontend/`; `components.json` holds the aliases and style. The generated files are ours to edit, but keep changes small so a later `add` or update stays easy to diff. Reach for these before hand-building a dialog, menu, table or form control.

**Theme tokens.** The palette is defined once as CSS variables in `src/app.css` (`:root`) using shadcn-svelte's names, and exposed as Tailwind colors (`bg-primary`, `text-muted-foreground`, `border-border`):

| Token | Value | Use |
| --- | --- | --- |
| `primary` / `primary-foreground` | `#333366` / white | Nav bar, headings, links, focus rings, table rules, primary buttons |
| `primary-hover` | `#444488` | Primary hover |
| `foreground` / `background` | `#3C4649` / white | Body text and page background |
| `muted` / `muted-foreground` | gray-50 / gray-500 | Panels and zebra rows / secondary text |
| `secondary`, `accent` | gray-100, indigo-50 (both with `#333366` text) | Secondary buttons, highlighted menu items, the status pill |
| `border` / `input` | gray-200 / gray-300 | Borders / form field borders |
| `ring` | `#333366` | Focus rings |
| `destructive` | red-600 | Destructive actions |

`green-600` / `yellow-600` / `red-600` still mark the identity thresholds (≥90 / ≥50 / below). New code uses the tokens. The components written before the tokens still use the equivalent hex literals (`text-[#333366]`); convert them when you are already editing the markup rather than in a sweep. The app is light-only: `dark:` variants apply only under a `.dark` class, which is never set.

The aesthetic is deliberately institutional and dense: square corners on forms and tables, bold small-caps-weight labels, generous use of `border-b-2 border-[#333366]` as a table header rule. The one exception is `TerminalLog`, which is intentionally styled as a dark terminal chrome. Prefer the existing tokens over introducing new hues.

**Print styles.** Print support is a first-class requirement — the report is meant to be saved as a PDF. Any new report component must also render in the hidden print tree; see [frontend.md](../system_runbooks/frontend.md#report-and-pdf-export).

## Prefer existing components

Use an established, maintained package or managed service before writing our own. This covers UI components (dialogs, tabs, tables, form controls), authentication and session handling (e.g. Cognito's hosted sign-in rather than a custom login flow), and infrastructure concerns such as retries, validation, and parsing.

Pre-built components carry accessibility, edge cases, and security fixes we would otherwise have to rediscover. Custom code is justified only when no suitable package exists or the existing ones clearly don't fit; say why in the PR. Never build your own authentication, cryptography, or session management.

When choosing a package, prefer one that is actively maintained, widely used, and compatible with the current stack (Svelte 5, Tailwind 4, shadcn-svelte and Bits UI, pydantic-ai), and declare it through the normal dependency workflow below.

## Repository conventions

- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`).
- **Documentation:** brief and high-signal; prose over bullet fragments where a reader needs the "why". Link to source with repo-relative paths.
- **Dependencies:** declared in `pyproject.toml` and locked by the committed `uv.lock`; caret ranges in `package.json`. Add or change a Python dependency by editing `pyproject.toml` and running `uv lock` — never hand-edit the lockfile, and commit it with the change.
- **Read the relevant runbook** in [`docs/system_runbooks/`](../system_runbooks/README.md) before modifying a core system.
