<script lang="ts">
  import { push } from "svelte-spa-router";  import { api } from "../../lib/api/client";
  import { jobStore } from "../../lib/stores/job";
  import type { PurificationRequest } from "../../lib/types/api";

  let loading = false;
  let errorMsg = '';
  let failedPurificationOpen = false;

  let formData: PurificationRequest = {
    fasta_id: '',
    failed_purification_text: null,
    min_percent_identity: 60.0,
    min_query_coverage: 60.0,
    max_evalue: 1e-3,
    max_hits: 50
  };

  $: isFastaValid = formData.fasta_id.trim().length > 0;
  
  async function handleSubmit() {
    if (!isFastaValid || loading) return;
    
    loading = true;
    errorMsg = '';
    
    try {
      const { job_id } = await api.startAnalysis(formData);
      jobStore.initiate(job_id);
      push(`/processing/${job_id}`);
    } catch (e: any) {
      errorMsg = e.message || 'Failed to start analysis';
      loading = false;
    }
  }
</script>

<div class="mt-8">
    {#if errorMsg}
        <div class="mb-4 p-4 bg-red-50 text-red-700 border border-red-200 text-sm">
            {errorMsg}
        </div>
    {/if}

    <form on:submit|preventDefault={handleSubmit}>
        <div class="mb-6">
            <label for="fasta" class="block text-sm font-bold text-[#3C4649] mb-2">Target Protein (UniProt ID, FASTA, or SSGCID ID)</label>
            <textarea
                id="fasta"
                bind:value={formData.fasta_id}
                rows="5"
                class="w-full border border-gray-300 p-2 text-sm focus:border-[#333366] focus:ring-1 focus:ring-[#333366] outline-none"
                placeholder=">Protein_A&#10;MKAW..."
            ></textarea>
        </div>

        <div class="mb-6 border border-gray-200">
            <button
                type="button"
                on:click={() => failedPurificationOpen = !failedPurificationOpen}
                class="w-full flex items-center justify-between px-4 py-3 text-sm font-bold text-[#3C4649] bg-gray-50 hover:bg-gray-100 transition-colors"
            >
                <span>Add Failed Purification (Optional)</span>
                <svg class="w-4 h-4 transition-transform {failedPurificationOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>
            {#if failedPurificationOpen}
                <div class="px-4 py-3 border-t border-gray-200">
                    <p class="text-xs text-gray-500 mb-2">Enter your failed purification protocol (if applicable)</p>
                    <textarea
                        id="failed-purification"
                        bind:value={formData.failed_purification_text}
                        rows="6"
                        class="w-full border border-gray-300 p-2 text-sm focus:border-[#333366] focus:ring-1 focus:ring-[#333366] outline-none"
                        placeholder="Paste your failed purification protocol here..."
                    ></textarea>
                </div>
            {/if}
        </div>

        <div class="bg-gray-50 border border-gray-200 p-4 mb-6">
            <h3 class="text-sm font-bold text-[#333366] mb-4 border-b border-gray-200 pb-2">Search Parameters</h3>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div>
                    <label for="pident" class="block text-xs font-bold text-gray-500 mb-1">Min Identity (%)</label>
                    <input
                        type="number"
                        id="pident"
                        bind:value={formData.min_percent_identity}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="cov" class="block text-xs font-bold text-gray-500 mb-1">Min Coverage (%)</label>
                    <input
                        type="number"
                        id="cov"
                        bind:value={formData.min_query_coverage}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="evalue" class="block text-xs font-bold text-gray-500 mb-1">Max E-Value</label>
                    <input
                        type="number"
                        id="evalue"
                        step="any" 
                        bind:value={formData.max_evalue}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="hits" class="block text-xs font-bold text-gray-500 mb-1">Max Hits</label>
                    <input
                        type="number"
                        id="hits"
                        bind:value={formData.max_hits}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
            </div>
        </div>

        <button
            type="submit"
            disabled={!isFastaValid || loading}
            class="px-6 py-2 bg-[#333366] text-white text-sm font-bold hover:bg-[#444488] disabled:opacity-50 transition-colors"
        >
            {loading ? 'Starting...' : 'Start Rescue Analysis'}
        </button>
    </form>
</div>
