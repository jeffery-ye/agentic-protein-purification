# Runbook: Frontend

Svelte single-page application serving the input form, live progress view, and report. Source: [`purification-rescue-frontend/`](../../purification-rescue-frontend).

## Running

```bash
cd purification-rescue-frontend
npm install
npm run dev
```

Vite serves on `http://localhost:5173`. To run this alongside the backend with one command, use `npm run dev` from the repository root instead.

| Script | Purpose |
| --- | --- |
| `npm run dev` | Dev server with HMR |
| `npm run build` | Production bundle to `dist/` |
| `npm run preview` | Serve the built bundle |
| `npm run check` | `svelte-check` template/type diagnostics plus `tsc` over the Vite config |
| `npm run check:prose` | After a build, fails if the built CSS lacks the `@tailwindcss/typography` (`prose`) styles |

`npm run check`, `npm run build` and `npm run check:prose` run in CI on every pull request, so a type error blocks the merge rather than being found later. The prose check exists because losing the typography plugin still builds cleanly but leaves the protocol Markdown unstyled, which is what broke the first Svelte 5 attempt in February 2026.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | `http://localhost:8000` | Backend origin |

Vite only exposes `VITE_`-prefixed variables. The repo-root `.env` is read because Vite loads from its own working directory upward — if a variable is not taking effect, put it in `purification-rescue-frontend/.env`.

### Tests

```bash
npm test
```

Vitest with jsdom and Testing Library, configured in `vite.config.ts` and run in CI. Tests sit next to the code as `*.test.ts`. They cover the API client, the poller, the stores and the progress stages; views are checked by hand. Vitest 5 needs Node 22.12 or later.

## Stack

Svelte 5 (runes) + Vite 8 + Tailwind 4, with shadcn-svelte (on Bits UI) as the component library. Routing is `svelte-spa-router` 5 (**hash-based** — URLs look like `/#/report/<job_id>`), Markdown rendering is `marked` styled by `@tailwindcss/typography`, and sanitization is `DOMPurify`. Tailwind runs as a Vite plugin (`@tailwindcss/vite`) and is configured entirely in `src/app.css`; there is no `tailwind.config.js` or PostCSS config. See [style.md](../agent_reference/style.md#visual-design) for the component library and theme tokens.

## Structure

```
src/
  main.ts                       mount()s App into #app
  App.svelte                    Layout + Router
  app.css                       Tailwind and typography plugin, theme tokens, base styles, global print rules
  routes/
    Home.svelte                 /
    Processing.svelte           /processing/:jobId
    Report.svelte               /report/:jobId
    MyJobs.svelte               /jobs
    Profile.svelte              /profile
  components/
    common/Layout.svelte        header, nav, main (children snippet), SSGCID/NIAID funding footer
    common/JobStoppedNotice.svelte      failed or interrupted job
    common/JobsTable.svelte             My Jobs data table: pinned running jobs, selection, bulk delete
    common/JobStateBadge.svelte         a job's state as a badge
    common/ActiveJobsIndicator.svelte   header menu of running jobs
    common/BackToJobs.svelte            "← My Jobs" link atop the report and progress views
    home/AnalysisForm.svelte    target input, failed-protocol accordion, 5 search parameters
    home/CharactersLeft.svelte  characters-left counter under a textarea; red when over the limit
    processing/ProgressTracker.svelte   state badge, pipeline stages, progress bar
    processing/TerminalLog.svelte       autoscrolling log pane
    report/ResultDashboard.svelte       tab bar, PDF button, hidden print tree
    report/ProtocolViewer.svelte        sanitized Markdown protocol
    report/PurificationTable.svelte     per-source buffer-step tables
    report/BlastResults.svelte          hit table with per-hit status
    report/SynthesisWarning.svelte      "Protocol synthesis failed" banner
    report/OutdatedReport.svelte        a stored report this version can't render: inputs and state
  lib/                          also importable as $lib
    components/ui/              shadcn-svelte components (generated; see components.json)
    utils.ts                    cn() class merging and helper types for the ui components
    api/client.ts               fetch wrapper (a 401 goes to /auth/login)
    api/poller.ts               2s polling loop
    api/resultShape.ts          checks a stored report still fits ProtocolResult
    stores/job.ts               jobStore + logStore
    stores/user.ts              userStore: /auth/me
    stores/jobs.ts              jobsStore + activeJobs: /jobs, auto-refresh, delete
    charLimit.ts                characters left, counted in code points as the backend's max_length does
    progress.ts                 pipeline stages recognized from progress messages
    types/api.ts                wire types: aliases of the generated openapi.d.ts, plus the few kept by hand
    types/openapi.d.ts          generated from openapi.json (npm run gen:api); never edit
```

## State and data flow

The stores in `src/lib/stores/` are created by factories that expose `subscribe` plus intent-named mutators. Components never touch the raw writable.

- **`jobStore`** — `{jobId, state, progress, history, inputs, result, outdatedResult, error}`. Mutators: `initiate`, `setStatus` (takes a `/status` response), `complete`, `markOutdated`, `fail` (as `failed` or `interrupted`), `reset`.
- **`userStore`** — the signed-in user from `/auth/me`, loaded once by `App.svelte` before the router renders, or `null` for a signed-out visitor. Signed-out visitors can browse the home page: the header shows **Sign in**, the nav hides My Jobs and Profile, and submitting is disabled. The job, report, My Jobs and profile routes are guarded by `userStore.requireSignIn`, which sends a signed-out visitor to `/auth/login?next=/#/<route>`; the backend accepts only that hash-route shape and returns there after sign-in. Any other API call that gets a 401 does the same. If `/auth/me` fails otherwise (a 502 during a deploy), the guard only blocks the route and `App.svelte` shows a Retry.
- **`jobsStore`** — the user's jobs from `/jobs`, for My Jobs and the header's running-jobs indicator (`activeJobs`). `App.svelte` keeps it refreshing: every 3 seconds while a job is queued or running, every 30 otherwise. Refreshes carry a sequence number, and a response older than the latest refresh or local delete/rename is dropped.
- **`logStore`** — an array of `[HH:MM:SS] message` strings. `syncHistory` takes a job's full progress history on every poll and appends only the entries not shown yet, so opening a running job replays everything so far. `addLog` is for the client's own messages and drops one identical to the previous.

The flow:

1. `AnalysisForm.handleSubmit` posts the form, calls `jobStore.initiate(job_id)`, and pushes `/processing/{job_id}`.
2. `Processing.svelte` clears the log and calls `startPolling(id)` whenever the job ID in the URL changes, since the router keeps the component when only the ID does. `poller.ts` checks at once, then schedules one check at a time every 2 seconds, with an `isPolling` guard so only one poller can exist.
3. Each check calls `/status`, writes `jobStore.setStatus`, and syncs the history into the log. It switches on `state`: on `completed` it stops, fetches `/result`, and calls `jobStore.complete` (or `markOutdated` if the stored report no longer fits); on `failed` or `interrupted` it stops and calls `jobStore.fail`. A network or server error never fails the job: checks back off to one every 30 seconds and the tracker shows "Connection lost, retrying" until one succeeds. Only a 404 or 401 is final. The result fetch runs while the poller is still active, so switching jobs mid-fetch drops it, and `setStatus` for a different job ID starts from a clean view.
4. An `$effect` in `Processing.svelte` sees `completed` with a result and pushes `/report/{jobId}` after a 1-second delay.
5. `Report.svelte` renders from the store. If the store holds a different job — a direct visit or a page reload — it loads `/status` and then `/result`, again per job ID, dropping a load the URL has since left. A running job redirects to its progress view, and a failed or interrupted one shows its error. Jobs are stored in SQLite, so a report URL keeps working across restarts.

The effect's cleanup in `Processing.svelte` stops the poller, so navigating away mid-run cleans up the client. It does **not** cancel the backend job, which runs to completion regardless.

## Report and PDF export

`ResultDashboard.svelte` renders the content **twice**: a tabbed view for screen (`.screen-only`) and a hidden `.print-report` div with all three sections expanded. The `@media print` block hides the tabs and reveals the print tree, so "Save as PDF" is just `window.print()` — there is no PDF library and no server-side rendering.

Two consequences for anyone touching the report:

- A new report section must be added to **both** trees or it will be missing from the PDF.
- The **Job Info** tab is the exception: it is screen-only, fetched from `/trace/{job_id}` when opened, and shown only when the dashboard has a `jobId`. Everything in it comes from papers or model output, so it is rendered as plain text.
- Any scrolling container needs an `overflow: visible` print override or Chrome and Safari clip its contents. The global rule in `src/app.css` handles this broadly (`body * { overflow: visible !important; }`), alongside forcing link underlines and allowing tables to break across pages.

Print rules are also defined locally in `Layout.svelte` (hides header, nav, footer), `Report.svelte` (hides the page heading), and `ResultDashboard.svelte`. When debugging a print layout, check all four places.

## Known frontend issues

**Wire types are generated.** `openapi-typescript` turns the backend's `openapi.json` into `types/openapi.d.ts`, and `types/api.ts` re-exports them under the names the components use. `/status` and `/auth/me` have no response model, so their types are still kept by hand in `api.ts`. After a backend schema change, run `uv run python scripts/export_openapi.py` and `npm run gen:api`; CI fails while either file is stale.

**Client-side validation is minimal.** The parameter inputs carry the API's bounds as `min`/`max`, and the browser checks them on submit. Anything else the API rejects comes back as a 422 whose field errors `errorMessage` lists above the form.
