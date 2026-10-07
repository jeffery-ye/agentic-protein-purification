<script lang="ts">
  import { charactersLeft } from "../../lib/charLimit";

  interface Props {
    /** For the textarea's aria-describedby. */
    id: string;
    text: string | null | undefined;
    max: number;
  }

  let { id, text, max }: Props = $props();

  const left = $derived(charactersLeft(text, max));
</script>

<p {id} class="mt-1 text-xs text-right {left < 0 ? 'text-red-600 font-bold' : 'text-gray-500'}">
  {#if left < 0}
    {(-left).toLocaleString()} characters over the {max.toLocaleString()} limit
  {:else}
    {left.toLocaleString()} characters left
  {/if}
</p>
