<script lang="ts">
  import { get } from 'svelte/store';
  import { replace } from "svelte-spa-router";
  import { jobStore } from "../lib/stores/job";
  import { api, JobNotFoundError, OutdatedResultError, ResultNotReadyError } from "../lib/api/client";
  import ResultDashboard from "../components/report/ResultDashboard.svelte";
  import OutdatedReport from "../components/report/OutdatedReport.svelte";
  import JobStoppedNotice from "../components/common/JobStoppedNotice.svelte";
  import BackToJobs from "../components/common/BackToJobs.svelte";

  interface Props {
    params?: { jobId: string };
  }

  let { params = { jobId: '' } }: Props = $props();

  // The router keeps this component when only the job ID changes, so the job
  // loads per ID rather than once on mount. A load for an ID the URL has since
  // left is dropped.
  $effect(() => {
    const id = params.jobId;
    if (!id) return;
    let left = false;
    void load(id, () => left);
    return () => {
      left = true;
    };
  });

  // A direct visit or a reload starts with an empty store, so load the job:
  // its status first (state, inputs, error), then the report if it has one.
  async function load(id: string, stale: () => boolean) {
    const current = get(jobStore);
    if (current.jobId === id && (current.result || current.outdatedResult)) return;

    jobStore.initiate(id);
    try {
        const status = await api.checkStatus(id);
        if (stale()) return;
        jobStore.setStatus(status);
        if (status.state === 'queued' || status.state === 'running') {
            // A running job has no report yet, so show its progress instead.
            replace(`/processing/${id}`);
            return;
        }
        if (status.state === 'failed' || status.state === 'interrupted') {
            jobStore.fail(status.error || 'Unknown error occurred during analysis', status.state);
            return;
        }
        const result = await api.getResult(id);
        if (stale()) return;
        jobStore.complete(result);
    } catch (e) {
        if (stale()) return;
        if (e instanceof ResultNotReadyError) {
            replace(`/processing/${id}`);
        } else if (e instanceof OutdatedResultError) {
            jobStore.markOutdated();
        } else if (e instanceof JobNotFoundError) {
            jobStore.fail('This job was not found.');
        } else {
            jobStore.fail('Could not load the report.');
        }
    }
  }
</script>

<div class="report-wrapper min-h-screen bg-white">
  <div class="report-header max-w-6xl mx-auto mb-6 space-y-2">
     <BackToJobs />
     <h2 class="text-2xl font-bold text-foreground">Analysis Result</h2>
  </div>

  {#if $jobStore.state === 'completed' && $jobStore.result}
    <ResultDashboard result={$jobStore.result} jobId={params.jobId} />

  {:else if $jobStore.state === 'completed' && $jobStore.outdatedResult}
    <OutdatedReport state={$jobStore.state} inputs={$jobStore.inputs} />

  {:else if $jobStore.state === 'failed' || $jobStore.state === 'interrupted'}
    <div class="max-w-xl mx-auto mt-20">
        <JobStoppedNotice state={$jobStore.state} error={$jobStore.error} />
    </div>
  {:else}
    <div class="flex flex-col items-center justify-center h-64">
        <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
        <p class="mt-4 text-gray-500">Loading result data...</p>
    </div>
  {/if}
</div>

<style>
  @media print {
    .report-header {
      display: none !important;
    }
    .report-wrapper {
      min-height: 0 !important;
    }
  }
</style>
