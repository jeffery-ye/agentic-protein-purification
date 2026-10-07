<script lang="ts">
  import type { BlastResult, Paper } from '../../lib/types/api';

  // Closed-access papers the pipeline couldn't read, listed so the user can read
  // them by hand (#47). A plain list in the order of the hits that cite them: no
  // ranking, since judging relevance is the user's call.

  interface Props {
    papers: Paper[];
    hits?: BlastResult[];
    /** The PDF shows every abstract in full rather than behind a toggle. */
    print?: boolean;
  }

  let { papers, hits = [], print = false }: Props = $props();

  const ACCESS_LABELS: Record<string, string> = {
    pmc_restricted: 'In PMC, not open access',
    not_in_pmc: 'Not in PMC'
  };

  let listed = $derived.by(() => {
    const byPmid = new Map(papers.filter(p => p.for_manual_review).map(p => [p.pmid, p]));
    const rows: { paper: Paper; hit: BlastResult | undefined }[] = [];
    for (const hit of hits) {
      const paper = hit.pmid ? byPmid.get(hit.pmid) : undefined;
      if (paper) {
        rows.push({ paper, hit });
        byPmid.delete(paper.pmid);
      }
    }
    // Papers no hit points to still get listed, after the rest.
    for (const paper of byPmid.values()) rows.push({ paper, hit: undefined });
    return rows;
  });
</script>

<div>
  <p class="text-sm text-gray-600 mb-4">
    We weren't able to access these papers, but they may contain useful information.
  </p>

  {#if listed.length === 0}
    <p class="text-sm text-gray-400 italic">No closed-access papers among the hits.</p>
  {/if}

  <ol class="space-y-5">
    {#each listed as { paper, hit } (paper.pmid)}
      <li class="border-b border-gray-200 pb-4 break-inside-avoid">
        <a href={paper.link} target="_blank" rel="noopener noreferrer"
           class="font-semibold text-[#333366] underline hover:text-[#444488]">
          {paper.title ?? `PubMed ${paper.pmid}`}
        </a>
        <p class="text-xs text-gray-500 mt-1">
          {[paper.journal, paper.year].filter(Boolean).join(', ')}
          {#if paper.journal || paper.year}·{/if}
          PMID {paper.pmid}
          {#if paper.access}· {ACCESS_LABELS[paper.access] ?? paper.access}{/if}
        </p>
        {#if hit}
          <p class="text-xs text-gray-500 mt-1">
            From hit {hit.pdb_id}: {hit.pident.toFixed(1)}% identity{#if hit.organism_name}, {hit.organism_name}{/if}
          </p>
        {/if}
        {#if paper.abstract}
          {#if print}
            <p class="text-sm text-[#3C4649] mt-2">{paper.abstract}</p>
          {:else}
            <details class="mt-2">
              <summary class="text-sm text-[#333366] cursor-pointer">Abstract</summary>
              <p class="text-sm text-[#3C4649] mt-1">{paper.abstract}</p>
            </details>
          {/if}
        {/if}
      </li>
    {/each}
  </ol>
</div>
