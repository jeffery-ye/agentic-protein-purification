<script lang="ts">
  import { link } from "svelte-spa-router";
</script>

<div class="app-wrapper min-h-screen flex flex-col font-sans text-[#3C4649] bg-white">
  <header class="bg-white px-8 py-6 border-b border-gray-100 flex justify-between items-center">
    <div class="cursor-pointer">
      <h3 class="text-[#3C4649] text-3xl font-bold leading-tight">Protein Purification Agent</h3>
    </div>
    
    <div class="hidden md:flex items-center space-x-4">
        <span class="text-sm text-gray-500 italic">User: Purification Rescue Agent</span>
    </div>
  </header>

  <nav class="bg-[#333366] text-white px-8 py-0 flex items-center">
    <ul class="flex items-center space-x-6 text-sm font-medium">
        <li>
            <!-- @ts-ignore -->
            <a href="/" use:link class="block py-3 px-2 hover:bg-[#444488] transition-colors">Home</a>
        </li>
    </ul>
  </nav>

  <main class="flex-grow w-full max-w-7xl mx-auto px-6 py-10">
    <slot />
  </main>

  <footer class="bg-white border-t border-gray-200 mt-auto">
    <div class="max-w-7xl mx-auto px-6 py-8 text-sm text-[#3C4649]">
       <p class="mb-4">
          SSGCID is funded by Federal funds from the National Institute of Allergy and Infectious Diseases (NIAID),
          National Institutes of Health (NIH), Department of Health and Human Services.
       </p>
    </div>
  </footer>
</div>

<style>
  @media print {
    header, nav, footer {
      display: none !important;
    }
    main {
      padding: 0 !important;
      max-width: none !important;
    }
    .app-wrapper {
      min-height: 0 !important;
    }
  }
</style>
