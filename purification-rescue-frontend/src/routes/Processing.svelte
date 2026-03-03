<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { push } from "svelte-spa-router";
  import { logStore, jobStore } from "../lib/stores/job";
  import { startPolling, stopPolling } from "../lib/api/poller";
  
  import TerminalLog from "../components/processing/TerminalLog.svelte";
  import ProgressTracker from "../components/processing/ProgressTracker.svelte";

  export let params: { jobId: string } = { jobId: '' };

  const unsubscribeJob = jobStore.subscribe(state => {
      if (state.status === 'COMPLETED' && state.result) {

          setTimeout(() => push(`/report/${state.jobId}`), 1000);
      }
  });

  onMount(() => {
    const id = params.jobId;
    if (id) {
        logStore.clear(); 
        startPolling(id).catch(err => console.error('Polling failed:', err));
    }
  });

  onDestroy(() => {
    stopPolling();
    unsubscribeJob();
  });
</script>

<div class="min-h-screen bg-white flex flex-col items-center">
    <div class="w-full max-w-4xl space-y-6 mt-1">
        <div class="text-center">
            <h2 class="text-3xl font-bold tracking-tight text-[#3C4649]">Analysis in Progress</h2>
            <p class="mt-2 text-sm text-gray-600">Job ID: <span class="font-mono bg-gray-50 px-2 py-1 rounded border border-gray-200">{params.jobId}</span></p>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg shadow-sm p-6">
            <ProgressTracker />
        </div>

        <div class="h-[500px]">
             <TerminalLog />
        </div>
    </div>
</div>