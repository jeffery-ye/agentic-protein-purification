<script lang="ts">
  import type { ProtocolResult } from '../../lib/types/api';
  import ProtocolViewer from './ProtocolViewer.svelte';
  import PurificationTable from './PurificationTable.svelte';
  import BlastResults from './BlastResults.svelte';

  export let result: ProtocolResult;
  let activeTab: 'protocol' | 'data' | 'blast' = 'protocol';

  function exportPDF() {
    window.print();
  }
</script>

<div class="w-full">
    <div class="tab-bar flex justify-between items-center border-b border-gray-300 mb-6">
      <div class="flex">
        <button 
          class="px-6 py-2 text-sm font-bold border-b-2 transition-colors {activeTab === 'protocol' ? 'border-[#333366] text-[#333366]' : 'border-transparent text-gray-500 hover:text-[#333366]'}"
          on:click={() => activeTab = 'protocol'}>
          Suggested Protocol
        </button>
        <button 
          class="px-6 py-2 text-sm font-bold border-b-2 transition-colors {activeTab === 'data' ? 'border-[#333366] text-[#333366]' : 'border-transparent text-gray-500 hover:text-[#333366]'}"
          on:click={() => activeTab = 'data'}>
          Similar Protocols
        </button>
        <button 
          class="px-6 py-2 text-sm font-bold border-b-2 transition-colors {activeTab === 'blast' ? 'border-[#333366] text-[#333366]' : 'border-transparent text-gray-500 hover:text-[#333366]'}"
          on:click={() => activeTab = 'blast'}>
          BLAST Analysis
        </button>
      </div>
      
      <div class="flex space-x-2 pb-2">
        <button 
          on:click={exportPDF}
          class="px-3 py-1 text-xs font-medium text-[#333366] border border-[#333366] hover:bg-[#333366] hover:text-white transition-colors">
          Save as PDF
        </button>
      </div>
    </div>

    <!-- Screen: tabbed view -->
    <div class="bg-white screen-only">
        {#if activeTab === 'protocol'}
            <ProtocolViewer comprehensive_protocol={result.comprehensive_protocol || ''} />
        {:else if activeTab === 'data'}
            <PurificationTable purifications={result.purifications || []} />
        {:else if activeTab === 'blast'}
            <BlastResults results={result.blast_results || []} />
        {/if}
    </div>

    <div class="print-report">
        <h1 class="text-2xl font-bold text-[#333366] mb-1">Purification Rescue Report</h1>
        <hr class="border-[#333366] mb-6" />

        <section class="mb-8">
            <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-4">Suggested Protocol</h2>
            <ProtocolViewer comprehensive_protocol={result.comprehensive_protocol || ''} />
        </section>

        <section class="mb-8">
            <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-4">Similar Protocols</h2>
            <PurificationTable purifications={result.purifications || []} />
        </section>

        <section>
            <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-4">BLAST Analysis</h2>
            <BlastResults results={result.blast_results || []} />
        </section>
    </div>
</div>

<style>
  .print-report {
    display: none;
  }

  @media print {
    .tab-bar,
    .screen-only {
      display: none !important;
    }

    .print-report {
      display: block !important;
    }

    :global(body) {
      background: white !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }

    section h2 {
      break-after: avoid;
    }
  }
</style>
