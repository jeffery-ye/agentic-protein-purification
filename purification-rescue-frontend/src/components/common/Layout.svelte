<script lang="ts">
  import type { Snippet } from "svelte";
  import { link } from "svelte-spa-router";
  import ActiveJobsIndicator from "./ActiveJobsIndicator.svelte";
  import { signIn, signOutUrl } from "../../lib/api/client";
  import { userStore } from "../../lib/stores/user";

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();
</script>

<div class="app-wrapper min-h-screen flex flex-col font-sans text-[#3C4649] bg-white">
  <header class="bg-white px-8 py-6 border-b border-gray-100 flex justify-between items-center">
    <div>
      <h3 class="text-[#3C4649] text-3xl font-bold leading-tight">Protein Purification Agent</h3>
    </div>
    
    <div class="flex items-center gap-4 text-sm">
        <ActiveJobsIndicator />
        {#if $userStore.user}
            <a href="/profile" use:link class="hidden md:inline text-gray-600 hover:underline">{$userStore.user.username}</a>
            {#if $userStore.user.auth_enabled}
                <a href={signOutUrl} class="text-gray-600 hover:underline">Sign out</a>
            {/if}
        {:else if $userStore.status !== "loading"}
            <a href={signIn.url} class="rounded-md bg-[#333366] px-3 py-1.5 font-medium text-white hover:bg-[#444488]">Sign in</a>
        {/if}
    </div>
  </header>

  <nav class="bg-[#333366] text-white px-8 py-0 flex items-center">
    <ul class="flex items-center space-x-6 text-sm font-medium">
        <li>
            <a href="/" use:link class="block py-3 px-2 hover:bg-[#444488] transition-colors">Home</a>
        </li>
        {#if $userStore.user}
            <li>
                <a href="/jobs" use:link class="block py-3 px-2 hover:bg-[#444488] transition-colors">My Jobs</a>
            </li>
            <li>
                <a href="/profile" use:link class="block py-3 px-2 hover:bg-[#444488] transition-colors">Profile</a>
            </li>
        {/if}
    </ul>
  </nav>

  <main class="grow w-full max-w-7xl mx-auto px-6 py-10">
    {@render children?.()}
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
