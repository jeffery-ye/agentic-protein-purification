<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "$lib/components/ui/button";
  import JobsTable from "../components/common/JobsTable.svelte";
  import { jobsStore } from "../lib/stores/jobs";

  // Layout keeps the list refreshing; refresh at once so the page opens current.
  onMount(() => {
    void jobsStore.refresh();
  });
</script>

<div class="w-full space-y-6">
  <div class="flex items-center justify-between border-b border-gray-200 pb-2">
    <h2 class="text-2xl font-normal text-foreground">My Jobs</h2>
    <Button href="/#/">New analysis</Button>
  </div>

  <p class="text-sm text-muted-foreground">
    Jobs and their inputs are kept until you delete them. A running job can't be deleted.
  </p>

  {#if $jobsStore.error}
    <p class="text-sm text-destructive">Could not load your jobs: {$jobsStore.error}</p>
  {/if}

  {#if $jobsStore.loaded}
    <JobsTable />
  {:else if !$jobsStore.error}
    <p class="text-sm text-muted-foreground">Loading your jobs...</p>
  {/if}
</div>
