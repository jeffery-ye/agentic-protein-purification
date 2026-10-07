<script lang="ts">
  interface Props {
    message: string;
  }

  let { message }: Props = $props();
</script>

<div class="mb-6 border border-amber-300 bg-amber-50 p-4">
  <h3 class="mb-1 text-sm font-bold text-amber-900">Protocol synthesis failed</h3>
  <p class="mb-2 text-sm text-amber-900">
    The suggested protocol could not be generated. The BLAST analysis and the source protocols
    extracted from the literature are unaffected.
  </p>
  <p class="wrap-break-word font-mono text-xs text-amber-800">{message}</p>
</div>
