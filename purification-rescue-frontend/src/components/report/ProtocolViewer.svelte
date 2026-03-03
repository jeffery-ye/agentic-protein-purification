<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  
  export let comprehensive_protocol: string | null = null;

  $: htmlContent = comprehensive_protocol ? DOMPurify.sanitize(marked.parse(comprehensive_protocol) as string) : '';
</script>

<div class="prose prose-slate max-w-none">
  {#if comprehensive_protocol}
    {@html htmlContent}
  {:else}
    <p class="text-gray-500 italic">No protocol generated.</p>
  {/if}
</div>