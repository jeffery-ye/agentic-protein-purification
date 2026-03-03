<script lang="ts">
  import { onMount } from 'svelte';
  import { link } from "svelte-spa-router";
  import { jobStore } from "../lib/stores/job";
  import { api } from "../lib/api/client";
  import ResultDashboard from "../components/report/ResultDashboard.svelte";

  export let params: { jobId: string } = { jobId: '' };

  onMount(async () => {
    if (!$jobStore.result && params.jobId) {
        try {
            const res = await api.getResult(params.jobId);
            jobStore.complete(res);
        } catch (e) {
            jobStore.fail("Could not load report manually.");
        }
    }
  });
</script>

<div class="report-wrapper min-h-screen bg-white">
  <div class="report-header max-w-6xl mx-auto mb-6">
     <h2 class="text-2xl font-bold text-[#3C4649]">Analysis Result</h2>
  </div>

  {#if $jobStore.status === 'COMPLETED' && $jobStore.result}
    <ResultDashboard result={$jobStore.result} />

  {:else if $jobStore.status === 'ERROR'}
    <div class="max-w-xl mx-auto mt-20 p-6 bg-red-50 border border-red-200 rounded-lg text-center">
        <h2 class="text-xl font-bold text-red-700 mb-2">Analysis Failed</h2>
        <p class="text-red-600">{$jobStore.error}</p>
        <div class="mt-4">
             <a href="/" use:link class="text-[#333366] underline">Try Again</a>
        </div>
    </div>
  {:else}
    <div class="flex flex-col items-center justify-center h-64">
        <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-[#333366]"></div>
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