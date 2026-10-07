<script lang="ts">
  import { get } from 'svelte/store';
  import { push } from "svelte-spa-router";
  import { logStore, jobStore } from "../lib/stores/job";
  import { startPolling, stopPolling } from "../lib/api/poller";

  import TerminalLog from "../components/processing/TerminalLog.svelte";
  import ProgressTracker from "../components/processing/ProgressTracker.svelte";
  import JobStoppedNotice from "../components/common/JobStoppedNotice.svelte";
  import BackToJobs from "../components/common/BackToJobs.svelte";

  interface Props {
    params?: { jobId: string };
  }

  let { params = { jobId: '' } }: Props = $props();

  // Once the report is in the store (or known to be outdated), move on to it.
  $effect(() => {
    const { state, result, outdatedResult } = $jobStore;
    if (state === 'completed' && (result || outdatedResult)) {
      const jobId = params.jobId;
      const timeout = setTimeout(() => push(`/report/${jobId}`), 1000);
      return () => clearTimeout(timeout);
    }
  });

  // Follows the job in the URL. The router keeps this component when only the
  // job ID changes (the running-jobs menu), so this reruns per ID rather than
  // once on mount, and stops the poller on leaving either way.
  $effect(() => {
    const id = params.jobId;
    if (!id) return;
    // A reload or a new tab starts with an empty store; the first poll
    // fills it and replays the job's progress history into the log.
    if (get(jobStore).jobId !== id) jobStore.initiate(id);
    logStore.clear();
    startPolling(id).catch(err => console.error('Polling ended:', err));
    return () => stopPolling();
  });
</script>

<div class="min-h-screen bg-white flex flex-col items-center">
    <div class="w-full max-w-4xl space-y-6 mt-1">
        <BackToJobs />
        <div class="text-center">
            <h2 class="text-3xl font-bold tracking-tight text-foreground">Analysis in Progress</h2>
            <p class="mt-2 text-sm text-gray-600">Job ID: <span class="font-mono bg-gray-50 px-2 py-1 rounded-sm border border-gray-200">{params.jobId}</span></p>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg shadow-xs p-6">
            <ProgressTracker />
        </div>

        {#if $jobStore.state === 'failed' || $jobStore.state === 'interrupted'}
            <JobStoppedNotice state={$jobStore.state} error={$jobStore.error} />
        {/if}

        <div class="h-[500px]">
             <TerminalLog />
        </div>
    </div>
</div>
