<script lang="ts">
  interface Props {
    reason: string;
  }

  let { reason }: Props = $props();
</script>

<div class="mb-6 border border-slate-300 bg-slate-50 p-4">
  <h3 class="mb-1 text-sm font-bold text-slate-900">No protocol synthesized</h3>
  <p class="mb-2 text-sm text-slate-800">
    The suggested protocol is derived from the differences between the failed attempt and the
    successful protocols found in the literature, so this run had nothing to derive one from. The
    BLAST analysis and the papers listed for manual review are unaffected.
  </p>
  <p class="wrap-break-word text-xs text-slate-700">{reason}</p>
</div>
