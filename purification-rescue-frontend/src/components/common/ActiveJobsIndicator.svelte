<script lang="ts">
  import { push } from "svelte-spa-router";
  import LoaderIcon from "@lucide/svelte/icons/loader-circle";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu";
  import { Button } from "$lib/components/ui/button";
  import { activeJobs, jobPath, jobTitle } from "../../lib/stores/jobs";
</script>

{#if $activeJobs.length}
  <DropdownMenu.Root>
    <DropdownMenu.Trigger>
      {#snippet child({ props })}
        <Button {...props} variant="secondary" size="sm" aria-label="Running jobs">
          <LoaderIcon class="animate-spin" />
          {$activeJobs.length} running
        </Button>
      {/snippet}
    </DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" class="w-72">
      <DropdownMenu.Label>Running jobs</DropdownMenu.Label>
      <DropdownMenu.Separator />
      {#each $activeJobs as job (job.id)}
        <DropdownMenu.Item onSelect={() => push(jobPath(job))} class="flex flex-col items-start">
          <span class="w-full truncate font-medium">{jobTitle(job) || job.id}</span>
          {#if job.progress}
            <span class="w-full truncate text-xs text-muted-foreground">{job.progress}</span>
          {/if}
        </DropdownMenu.Item>
      {/each}
      <DropdownMenu.Separator />
      <DropdownMenu.Item onSelect={() => push("/jobs")}>All jobs</DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu.Root>
{/if}
