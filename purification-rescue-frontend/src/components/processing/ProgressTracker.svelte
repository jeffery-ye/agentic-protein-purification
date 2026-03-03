<script lang="ts">
  import { jobStore } from "../../lib/stores/job";
</script>

<div class="space-y-2">
    <div class="flex items-center justify-between">
        <h3 class="text-lg font-medium text-[#3C4649]">Analysis Status</h3>
        <span class="inline-flex items-center rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-[#333366] ring-1 ring-inset ring-[#333366]/20">
            {$jobStore.status}
        </span>
    </div>

    <div class="h-2 w-full bg-gray-100 rounded-full overflow-hidden">
        {#if $jobStore.status !== 'ERROR' && $jobStore.status !== 'COMPLETED'}
            <div class="h-full bg-[#333366] animate-progress-indeterminate rounded-full"></div>
        {:else if $jobStore.status === 'COMPLETED'}
             <div class="h-full bg-green-600 w-full rounded-full"></div>
        {:else}
             <div class="h-full bg-red-600 w-full rounded-full"></div>
        {/if}
    </div>
    
    <p class="text-sm text-gray-500">
        {#if $jobStore.status === 'Running BLAST'}
            Searching protein databases for homologs...
        {:else if $jobStore.status === 'Initializing'}
            Preparing environment and validating inputs...
        {:else if $jobStore.status === 'COMPLETED'}
            Analysis complete. Redirecting...
        {:else if $jobStore.status === 'ERROR'}
            Analysis failed.
        {:else}
            Processing...
        {/if}
    </p>
</div>

<style>
  @keyframes progress-indeterminate {
    0% {
      width: 0%;
      margin-left: 0%;
    }
    50% {
      width: 70%;
      margin-left: 30%;
    }
    100% {
      width: 0%;
      margin-left: 100%;
    }
  }
  .animate-progress-indeterminate {
    animation: progress-indeterminate 1.5s infinite ease-in-out;
  }
</style>
