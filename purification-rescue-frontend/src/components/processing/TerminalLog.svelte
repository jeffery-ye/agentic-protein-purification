<script lang="ts">
  import { afterUpdate } from 'svelte';
  import { logStore } from "../../lib/stores/job";
  
  let terminalDiv: HTMLElement;

  function scrollToBottom() {
    if (terminalDiv) {
      terminalDiv.scrollTop = terminalDiv.scrollHeight;
    }
  }

  afterUpdate(() => {
    scrollToBottom();
  });
</script>

<div class="rounded-lg bg-gray-900 border border-gray-800 shadow-2xl overflow-hidden flex flex-col h-full max-h-[600px]">
  <div class="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
    <div class="flex space-x-2">
      <div class="w-3 h-3 rounded-full bg-red-500"></div>
      <div class="w-3 h-3 rounded-full bg-yellow-500"></div>
      <div class="w-3 h-3 rounded-full bg-green-500"></div>
    </div>
    <div class="text-xs text-gray-400 font-mono">analysis_runner.exe — ssh</div>
    <div></div>
  </div>

  <div 
    bind:this={terminalDiv}
    class="flex-1 p-4 font-mono text-sm overflow-y-auto scroll-smooth bg-[#0d1117] text-gray-300"
  >
    {#if $logStore.length === 0}
        <span class="text-gray-500">Waiting for logs...</span>
    {/if}

    {#each $logStore as log}
      <div class="border-l-2 border-transparent hover:border-gray-700 hover:bg-gray-800/30 pl-2 py-0.5">
          <span class="text-green-500 select-none mr-2">$</span>
          {log}
      </div>
    {/each}
  </div>
</div>
