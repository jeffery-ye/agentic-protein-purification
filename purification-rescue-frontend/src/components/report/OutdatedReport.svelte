<script lang="ts">
  import * as Alert from "$lib/components/ui/alert";
  import type { JobState, PurificationRequest } from "../../lib/types/api";
  import { STATE_LABELS } from "../../lib/progress";

  interface Props {
    state: JobState;
    inputs?: PurificationRequest | null;
  }

  let { state, inputs = null }: Props = $props();

  let rows = $derived(
    inputs
      ? [
          ['Target protein', inputs.fasta_id],
          ['Failed purification', inputs.failed_purification_text],
          ['Min identity (%)', inputs.min_percent_identity],
          ['Min coverage (%)', inputs.min_query_coverage],
          ['Max E-value', inputs.max_evalue],
          ['Max hits', inputs.max_hits],
          ['Max protocols', inputs.max_protocols]
        ]
      : []
  );
</script>

<div class="max-w-3xl mx-auto space-y-6">
  <Alert.Root class="border-yellow-300 bg-yellow-50 text-yellow-900">
    <Alert.Title class="font-bold">This report came from an older version</Alert.Title>
    <Alert.Description class="text-yellow-900">
      The job finished, but its report was saved in a format this version of the site can no longer display. Its inputs are below; submit them again for a current report.
    </Alert.Description>
  </Alert.Root>

  <table class="w-full text-sm text-left border-collapse">
    <thead>
      <tr class="border-b-2 border-primary">
        <th class="py-2 px-4 font-bold text-primary">Input</th>
        <th class="py-2 px-4 font-bold text-primary">Value</th>
      </tr>
    </thead>
    <tbody>
      <tr class="border-b border-gray-200">
        <td class="py-2 px-4 font-medium">Status</td>
        <td class="py-2 px-4">{STATE_LABELS[state]}</td>
      </tr>
      {#each rows as [label, value]}
        <tr class="border-b border-gray-200">
          <td class="py-2 px-4 font-medium align-top">{label}</td>
          <td class="py-2 px-4 font-mono whitespace-pre-wrap break-all">{value ?? '-'}</td>
        </tr>
      {:else}
        <tr><td colspan="2" class="py-4 text-center text-muted-foreground">The job's inputs are not available.</td></tr>
      {/each}
    </tbody>
  </table>
</div>
