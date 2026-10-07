<script lang="ts">
  import { link } from "svelte-spa-router";
  import * as Alert from "$lib/components/ui/alert";

  interface Props {
    state: 'failed' | 'interrupted';
    error?: string | null;
  }

  let { state, error = null }: Props = $props();
</script>

{#if state === 'interrupted'}
  <Alert.Root class="border-yellow-300 bg-yellow-50 text-yellow-900">
    <Alert.Title class="font-bold">Analysis interrupted</Alert.Title>
    <Alert.Description class="text-yellow-900">
      <p>The server restarted while this job was running, so it stopped before finishing. Nothing is wrong with your input; please submit it again.</p>
      <a href="/" use:link class="text-primary underline">Start a new analysis</a>
    </Alert.Description>
  </Alert.Root>
{:else}
  <Alert.Root variant="destructive" class="border-red-200 bg-red-50">
    <Alert.Title class="font-bold">Analysis failed</Alert.Title>
    <Alert.Description>
      <p>{error || 'Unknown error occurred during analysis'}</p>
      <a href="/" use:link class="text-primary underline">Try again</a>
    </Alert.Description>
  </Alert.Root>
{/if}
