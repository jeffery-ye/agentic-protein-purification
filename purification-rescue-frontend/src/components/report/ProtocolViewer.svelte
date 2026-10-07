<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  
  interface Props {
    comprehensive_protocol?: string | null;
  }

  let { comprehensive_protocol = null }: Props = $props();

  // The protocol is model output that saw paper text, so it is untrusted (#98).
  // No images or other media: any of them could load a tracking URL just by
  // opening the report. Markdown never needs these tags or attributes.
  const SANITIZE = {
    FORBID_TAGS: ['img', 'picture', 'source', 'video', 'audio', 'track', 'svg', 'math'],
    FORBID_ATTR: ['style', 'srcset', 'poster', 'background']
  };

  let htmlContent = $derived(comprehensive_protocol ? DOMPurify.sanitize(marked.parse(comprehensive_protocol) as string, SANITIZE) : '');
</script>

<div class="prose prose-slate max-w-none">
  {#if comprehensive_protocol}
    {@html htmlContent}
  {:else}
    <p class="text-gray-500 italic">No protocol generated.</p>
  {/if}
</div>