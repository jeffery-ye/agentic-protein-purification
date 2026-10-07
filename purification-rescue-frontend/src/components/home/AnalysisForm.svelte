<script lang="ts">
  import { push } from "svelte-spa-router";
  import { api, signIn } from "../../lib/api/client";
  import { signedIn } from "../../lib/stores/user";
  import { jobStore } from "../../lib/stores/job";
  import { jobsStore } from "../../lib/stores/jobs";
  import {
    DEFAULT_PROTOCOLS,
    MAX_FAILED_PURIFICATION_LENGTH,
    MAX_FASTA_LENGTH,
    MAX_PROTOCOLS,
    type PurificationRequest
  } from "../../lib/types/api";
  import { charactersLeft } from "../../lib/charLimit";
  import CharactersLeft from "./CharactersLeft.svelte";

  let loading = $state(false);
  let errorMsg = $state('');
  let failedPurificationOpen = $state(false);

  let formData: PurificationRequest = $state({
    fasta_id: '',
    failed_purification_text: null,
    min_percent_identity: 40.0,
    min_query_coverage: 40.0,
    max_evalue: 1e-3,
    max_hits: 50,
    max_protocols: DEFAULT_PROTOCOLS
  });

  let isFastaValid = $derived(formData.fasta_id.trim().length > 0);
  // Over-limit text is flagged rather than cut off, since a silently truncated
  // sequence would still run, on the wrong input.
  let overLimit = $derived(
    charactersLeft(formData.fasta_id, MAX_FASTA_LENGTH) < 0 ||
    charactersLeft(formData.failed_purification_text, MAX_FAILED_PURIFICATION_LENGTH) < 0
  );

  async function handleSubmit() {
    if (!isFastaValid || overLimit || loading) return;
    
    loading = true;
    errorMsg = '';
    
    try {
      const { job_id } = await api.startAnalysis(formData);
      jobStore.initiate(job_id);
      void jobsStore.jobStarted();
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

    <form onsubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
        <div class="mb-6">
            <label for="fasta" class="block text-sm font-bold text-[#3C4649] mb-2">Target Protein (UniProt ID, FASTA, or SSGCID ID)</label>
            <textarea
                id="fasta"
                bind:value={formData.fasta_id}
                rows="5"
                class="w-full border border-gray-300 p-2 text-sm focus:border-[#333366] focus:ring-1 focus:ring-[#333366] outline-hidden"
                placeholder=">Protein_A&#10;MKAW..."
                aria-describedby="fasta-left"
            ></textarea>
            <CharactersLeft id="fasta-left" text={formData.fasta_id} max={MAX_FASTA_LENGTH} />
        </div>

        <div class="mb-6 border border-gray-200">
            <button
                type="button"
                onclick={() => failedPurificationOpen = !failedPurificationOpen}
                class="w-full flex items-center justify-between px-4 py-3 text-sm font-bold text-[#3C4649] bg-gray-50 hover:bg-gray-100 transition-colors"
            >
                <span>Add Failed Purification (Optional)</span>
                <svg class="w-4 h-4 transition-transform {failedPurificationOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>
            {#if failedPurificationOpen}
                <div class="px-4 py-3 border-t border-gray-200">
                    <textarea
                        id="failed-purification"
                        bind:value={formData.failed_purification_text}
                        rows="6"
                        class="w-full border border-gray-300 p-2 text-sm focus:border-[#333366] focus:ring-1 focus:ring-[#333366] outline-hidden"
                        placeholder="Paste your failed purification protocol here..."
                        aria-describedby="failed-purification-left"
                    ></textarea>
                    <CharactersLeft id="failed-purification-left" text={formData.failed_purification_text} max={MAX_FAILED_PURIFICATION_LENGTH} />
                </div>
            {/if}
        </div>

        <div class="bg-gray-50 border border-gray-200 p-4 mb-6">
            <h3 class="text-sm font-bold text-[#333366] mb-4 border-b border-gray-200 pb-2">Search Parameters</h3>
            <div class="grid grid-cols-1 sm:grid-cols-3 md:grid-cols-5 gap-4">
                <div>
                    <label for="pident" class="block text-xs font-bold text-gray-500 mb-1">Min Identity (%)</label>
                    <input
                        type="number"
                        id="pident"
                        min="0"
                        max="100"
                        step="any"
                        required
                        bind:value={formData.min_percent_identity}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="cov" class="block text-xs font-bold text-gray-500 mb-1">Min Coverage (%)</label>
                    <input
                        type="number"
                        id="cov"
                        min="0"
                        max="100"
                        step="any"
                        required
                        bind:value={formData.min_query_coverage}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="evalue" class="block text-xs font-bold text-gray-500 mb-1">Max E-Value</label>
                    <input
                        type="number"
                        id="evalue"
                        min="0"
                        max="1000"
                        step="any"
                        required
                        bind:value={formData.max_evalue}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="hits" class="block text-xs font-bold text-gray-500 mb-1">Max Hits</label>
                    <input
                        type="number"
                        id="hits"
                        min="1"
                        max="50"
                        step="1"
                        required
                        bind:value={formData.max_hits}
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
                <div>
                    <label for="protocols" class="block text-xs font-bold text-gray-500 mb-1">Max Protocols</label>
                    <input
                        type="number"
                        id="protocols"
                        min="1"
                        max={MAX_PROTOCOLS}
                        step="1"
                        required
                        bind:value={formData.max_protocols}
                        title="The search stops once this many source protocols are found"
                        class="w-full border border-gray-300 p-1.5 text-sm"
                    />
                </div>
            </div>
        </div>

        <div class="flex items-center gap-3">
            <button
                type="submit"
                disabled={!$signedIn || !isFastaValid || overLimit || loading}
                class="px-6 py-2 bg-[#333366] text-white text-sm font-bold hover:bg-[#444488] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
                {#if !$signedIn}Sign in to submit{:else if loading}Starting...{:else}Start Analysis{/if}
            </button>
            {#if !$signedIn}
                <a href={signIn.url} class="text-sm text-[#333366] underline">Sign in</a>
            {:else if overLimit}
                <span class="text-sm text-red-600">Shorten the text over its character limit to submit.</span>
            {/if}
        </div>
    </form>
</div>
