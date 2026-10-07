<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { jobStore } from "../../lib/stores/job";
  import { STAGES, STATE_LABELS, currentStage, describe } from "../../lib/progress";

  let stage = $derived(currentStage($jobStore.state, $jobStore.history));
  let finished = $derived($jobStore.state === 'completed' || $jobStore.state === 'failed' || $jobStore.state === 'interrupted');
</script>

<div class="space-y-3">
    <div class="flex items-center justify-between">
        <h3 class="text-lg font-medium text-foreground">Analysis Status</h3>
        <Badge variant="outline" class="h-auto rounded-md border-primary/20 bg-accent px-2 py-1 text-accent-foreground">
            {STATE_LABELS[$jobStore.state]}
        </Badge>
    </div>

    <ol class="grid grid-cols-4 gap-2 text-xs">
        {#each STAGES as step, i}
            {@const done = i < stage}
            {@const current = i === stage && !finished}
            <li class="border-t-4 pt-1 {done ? 'border-primary text-primary' : current ? 'border-primary/50 text-primary font-bold' : 'border-gray-200 text-muted-foreground'}">
                {i + 1}. {step.label}
            </li>
        {/each}
    </ol>

    <div class="h-2 w-full bg-gray-100 rounded-full overflow-hidden">
        {#if !finished}
            <div class="h-full bg-primary animate-progress-indeterminate rounded-full"></div>
        {:else if $jobStore.state === 'completed'}
            <div class="h-full bg-green-600 w-full rounded-full"></div>
        {:else if $jobStore.state === 'interrupted'}
            <div class="h-full bg-yellow-600 w-full rounded-full"></div>
        {:else}
            <div class="h-full bg-red-600 w-full rounded-full"></div>
        {/if}
    </div>

    <p class="text-sm text-muted-foreground">{describe($jobStore.state, stage)}</p>
    {#if $jobStore.connectionLost && !finished}
        <p role="status" class="text-sm text-yellow-800">Connection lost, retrying… The job keeps running on the server.</p>
    {/if}
    {#if $jobStore.state === 'running' && $jobStore.progress}
        <p class="text-xs font-mono text-muted-foreground">{$jobStore.progress}</p>
    {/if}
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
