<script lang="ts">
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import { jobTitle, jobsStore } from "../../lib/stores/jobs";
  import { MAX_JOB_NAME_LENGTH, type JobListItem } from "../../lib/types/api";

  // The job's name in My Jobs, renamed in place: hover for the pencil, Enter or
  // leaving the field saves, Escape cancels, and a blank name goes back to the input.

  let { job }: { job: JobListItem } = $props();

  let editing = $state(false);
  let saving = $state(false);
  let draft = $state("");
  let error = $state("");

  function startEditing() {
    draft = jobTitle(job);
    error = "";
    editing = true;
  }

  async function save() {
    if (!editing || saving) return;
    const name = draft.trim();
    if (name === jobTitle(job) || (!name && !job.name)) {
      editing = false;
      return;
    }
    saving = true;
    try {
      await jobsStore.rename(job.id, name);
      editing = false;
    } catch (err) {
      error = err instanceof Error ? err.message : "Could not rename";
    } finally {
      saving = false;
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      event.preventDefault();
      void save();
    } else if (event.key === "Escape") {
      editing = false;
      error = "";
    }
  }

  function focusAndSelect(input: HTMLInputElement) {
    input.focus();
    input.select();
  }
</script>

<!-- Clicks here mustn't reach the row, which opens the job. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="max-w-[320px]" onclick={event => editing && event.stopPropagation()}>
  {#if editing}
    <input
      use:focusAndSelect
      bind:value={draft}
      onkeydown={onKeydown}
      onblur={save}
      disabled={saving}
      maxlength={MAX_JOB_NAME_LENGTH}
      placeholder={job.input_summary}
      aria-label="Job name"
      class="h-7 w-full rounded-md border border-input bg-background px-2 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
    />
    {#if error}
      <p class="mt-1 text-xs text-destructive">{error}</p>
    {/if}
  {:else}
    <div class="group flex items-center gap-1">
      <span class="min-w-0 truncate font-medium" title={jobTitle(job)}>{jobTitle(job) || "-"}</span>
      <button
        type="button"
        class="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
        aria-label="Rename job"
        title="Rename"
        onclick={event => {
          event.stopPropagation();
          startEditing();
        }}
      >
        <PencilIcon class="size-3.5" />
      </button>
    </div>
    {#if job.name}
      <div class="truncate text-xs text-muted-foreground" title={job.input_summary}>{job.input_summary}</div>
    {/if}
  {/if}
</div>
