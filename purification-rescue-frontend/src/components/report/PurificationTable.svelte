<script lang="ts">
  import type { Purification, BufferStep } from '../../lib/types/api';
  export let purifications: Purification[] = [];
</script>

<div>
  {#if purifications.length === 0}
    <p class="py-4 text-center text-gray-500">No protocol data found.</p>
  {/if}

  {#each purifications as purification, i}
    <div class="mb-8 {i > 0 ? 'border-t border-gray-200 pt-6' : ''}">
      <div class="mb-3">
        <h3 class="text-base font-bold text-[#333366]">
          {purification.organism_name ?? 'Unknown Organism'}
          {#if purification.uniprot_id}
            <span class="text-xs font-mono text-gray-400 ml-2">({purification.uniprot_id})</span>
          {/if}
        </h3>
        {#if purification.article_link}
          <a href={purification.article_link} target="_blank" rel="noopener noreferrer"
             class="text-sm text-[#333366] underline hover:text-[#444488]">
            {purification.article_title ?? 'View Article'}
          </a>
        {:else if purification.article_title}
          <span class="text-sm text-gray-600">{purification.article_title}</span>
        {/if}
      </div>

      {#if purification.protocol && purification.protocol.length > 0}
        <div class="overflow-x-auto">
          <table class="min-w-full text-sm text-left border-collapse">
            <thead>
              <tr class="border-b-2 border-[#333366]">
                <th class="py-2 px-3 font-bold text-[#333366]">Step</th>
                <th class="py-2 px-3 font-bold text-[#333366]">Buffer</th>
                <th class="py-2 px-3 font-bold text-[#333366]">Composition</th>
                <th class="py-2 px-3 font-bold text-[#333366]">pH</th>
                <th class="py-2 px-3 font-bold text-[#333366]">Salt</th>
                <th class="py-2 px-3 font-bold text-[#333366]">Supplements</th>
              </tr>
            </thead>
            <tbody>
              {#each purification.protocol as step, j}
                <tr class="border-b border-gray-200 {j % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}">
                  <td class="py-2 px-3 text-[#3C4649] font-medium">{step.purification_step ?? '-'}</td>
                  <td class="py-2 px-3 text-[#3C4649]">{step.buffer_name ?? '-'}</td>
                  <td class="py-2 px-3 text-[#3C4649]">{step.buffer_composition ?? '-'}</td>
                  <td class="py-2 px-3 text-[#3C4649]">{step.ph ?? '-'}</td>
                  <td class="py-2 px-3 text-[#3C4649]">{step.salt_type ?? '-'}</td>
                  <td class="py-2 px-3 text-[#3C4649]">{step.buffer_supplement ?? '-'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p class="text-sm text-gray-400 italic">No structured protocol extracted.</p>
      {/if}
    </div>
  {/each}
</div>
