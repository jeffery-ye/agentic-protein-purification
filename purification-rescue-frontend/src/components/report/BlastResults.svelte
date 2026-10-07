<script lang="ts">
  import type { BlastResult, HitStatus } from '../../lib/types/api';
  
  interface Props {
    results?: BlastResult[];
  }

  let { results = [] }: Props = $props();

  function getIdentityColor(pident: number) {
      if (pident >= 90) return 'text-green-600 font-bold';
      if (pident >= 50) return 'text-yellow-600 font-medium';
      return 'text-red-600';
  }

  function truncate(text: string, max: number): string {
      if (text.length <= max) return text;
      return text.substring(0, max) + '…';
  }

  // A duplicate paper's outcome is its first hit's, which may not be a protocol,
  // so it gets no icon of its own (#46).
  function getStatusIcon(status: HitStatus | null | undefined): string {
      if (status === 'Protocol Found') return '✓';
      if (status === 'Not Analyzed' || status === 'Paper Already Found') return '';
      return '⚠️';
  }
</script>

<div class="overflow-x-auto print:overflow-visible">
  <table class="min-w-full text-sm text-left border-collapse">
    <thead>
      <tr class="border-b-2 border-[#333366]">
        <th class="py-2 px-4 font-bold text-[#333366]">Organism</th>
        <th class="py-2 px-4 font-bold text-[#333366]">PDB ID</th>
        <th class="py-2 px-4 font-bold text-[#333366]">Identity %</th>
        <th class="py-2 px-4 font-bold text-[#333366]">Query Cov %</th>
        <th class="py-2 px-4 font-bold text-[#333366]">E-Value</th>
        <th class="py-2 px-4 font-bold text-[#333366]">Score</th>
        <th class="py-2 px-4 font-bold text-[#333366]">Status</th>
      </tr>
    </thead>
    <tbody>
      {#if results.length === 0}
        <tr><td colspan="7" class="py-4 text-center text-gray-500">No BLAST hits found.</td></tr>
      {/if}
      
      {#each results as row, i}
        <tr class="border-b border-gray-200 hover:bg-gray-50 {i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}"
            title={row.protein_name}>
          <td class="py-2 px-4 text-[#3C4649] font-medium max-w-[200px] truncate print:whitespace-normal print:break-words">{row.organism_name ?? truncate(row.protein_name, 40)}</td>
          <td class="py-2 px-4 font-mono"><a href="https://www.rcsb.org/structure/{row.pdb_id}" target="_blank" rel="noopener noreferrer" class="text-[#333366] underline hover:text-indigo-500 transition-colors">{row.pdb_id}</a></td>
          <td class="py-2 px-4 {getIdentityColor(row.pident)}">{row.pident.toFixed(1)}%</td>
          <td class="py-2 px-4 text-[#3C4649]">{row.query_coverage.toFixed(1)}%</td>
          <td class="py-2 px-4 text-[#3C4649] whitespace-nowrap">{row.e_value.toExponential(2)}</td>
          <td class="py-2 px-4 text-[#3C4649] font-mono">{row.similarity_score != null ? row.similarity_score.toFixed(3) : '-'}</td>
          <td class="py-2 px-4 text-[#3C4649]">
            {getStatusIcon(row.status)} {row.status || 'Unknown'}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
