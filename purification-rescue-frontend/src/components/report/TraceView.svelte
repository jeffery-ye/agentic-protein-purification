<script lang="ts">
  import { api } from '../../lib/api/client';
  import type { ProtocolResult, Trace, TraceStep } from '../../lib/types/api';

  // What the pipeline did for this job (#77): each source protocol's extracted
  // text and the reasoning of the calls that read it, the synthesis, and the
  // run's time, tokens and estimated cost. Everything here comes from papers or
  // model output, so it is rendered as plain text only.

  interface Props {
    jobId: string;
    result: ProtocolResult;
  }

  let { jobId, result }: Props = $props();

  let trace = $state<Trace | null>(null);
  let status = $state<'loading' | 'loaded' | 'error'>('loading');

  $effect(() => {
    const id = jobId;
    status = 'loading';
    api.getTrace(id).then(
      loaded => {
        trace = loaded;
        status = 'loaded';
      },
      () => (status = 'error')
    );
  });

  const AGENT_LABELS: Record<string, string> = {
    extraction: 'Extraction',
    structuring: 'Structuring',
    planner: 'Planner',
    formatter: 'Formatter'
  };

  function sourceKey(p: NonNullable<ProtocolResult['purifications']>[number]): string | null {
    return p.pdb_id ?? p.source ?? null;
  }

  function duration(seconds: number): string {
    if (seconds < 60) return `${seconds.toFixed(1)} s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
  }

  function tokens(n: number | null | undefined): string {
    return (n ?? 0).toLocaleString();
  }

  function cost(usd: number | null | undefined): string {
    return usd == null ? 'not available for this model' : `~$${usd.toFixed(4)}`;
  }

  let protocols = $derived(result.purifications ?? []);
  let steps = $derived(trace?.steps ?? []);
  let protocolKeys = $derived(new Set(protocols.map(sourceKey).filter(k => k !== null)));
  // Papers read that yielded no protocol, grouped by hit.
  let otherPapers = $derived.by(() => {
    const groups = new Map<string, TraceStep[]>();
    for (const step of steps) {
      if (!step.source_key || protocolKeys.has(step.source_key)) continue;
      groups.set(step.source_key, [...(groups.get(step.source_key) ?? []), step]);
    }
    return [...groups.entries()];
  });
  let synthesisSteps = $derived(steps.filter(s => s.agent === 'planner' || s.agent === 'formatter'));
</script>

{#snippet stepDetails(step: TraceStep)}
  <div class="border-l-2 border-gray-200 pl-3 py-1">
    <p class="text-xs text-gray-500">
      <span class="font-semibold text-[#333366]">{AGENT_LABELS[step.agent] ?? step.agent}</span>
      · {duration(step.seconds)}
      · {tokens(step.input_tokens)} in / {tokens(step.output_tokens)} out
      {#if step.model}· {step.model}{/if}
    </p>
    {#if step.error}
      <p class="text-sm text-red-700 mt-1">This call failed: {step.error}</p>
    {/if}
    {#if step.reasoning}
      <details class="mt-1">
        <summary class="text-sm text-[#333366] cursor-pointer">The model's summary of its reasoning</summary>
        <p class="text-sm text-[#3C4649] whitespace-pre-wrap mt-1">{step.reasoning}</p>
      </details>
    {:else}
      <p class="text-xs text-gray-400 italic mt-1">No reasoning summary was returned for this step.</p>
    {/if}
  </div>
{/snippet}

{#snippet stepsFor(key: string | null)}
  {#each steps.filter(s => s.source_key === key && s.agent !== 'planner' && s.agent !== 'formatter') as step, i (i)}
    {@render stepDetails(step)}
  {/each}
{/snippet}

<div class="space-y-8">
  {#if status === 'loading'}
    <p class="text-sm text-gray-500">Loading job info…</p>
  {:else if status === 'error'}
    <p class="text-sm text-gray-500">Job info couldn't be loaded. Try reloading the page.</p>
  {:else if trace === null}
    <p class="text-sm text-gray-500">
      No job info for this job. Jobs run before it was recorded don't have any.
    </p>
  {:else}
    <section>
      <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-3">Summary</h2>
      <dl class="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        <dt class="text-gray-500">Model</dt>
        <dd class="text-[#3C4649]">{trace.model ?? 'Unknown'}</dd>
        <dt class="text-gray-500">Runtime</dt>
        <dd class="text-[#3C4649]">
          {duration(trace.total_seconds)}
          {#if trace.stages?.length}
            <span class="text-gray-500">
              ({trace.stages.map(s => `${s.name} ${duration(s.seconds)}`).join(' · ')})
            </span>
          {/if}
        </dd>
        <dt class="text-gray-500">Tokens</dt>
        <dd class="text-[#3C4649]">
          {tokens(trace.input_tokens)} in / {tokens(trace.output_tokens)} out
        </dd>
        <dt class="text-gray-500">Cost</dt>
        <dd class="text-[#3C4649]">
          {cost(trace.cost_usd)}
        </dd>
      </dl>
    </section>

    <section>
      <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-3">Source protocols</h2>
      {#if protocols.length === 0}
        <p class="text-sm text-gray-500">No source protocols were found.</p>
      {/if}
      <div class="space-y-6">
        {#each protocols as protocol, i (i)}
          <div class="space-y-2">
            <h3 class="font-semibold text-[#333366]">
              {#if protocol.article_link}
                <a href={protocol.article_link} target="_blank" rel="noopener noreferrer" class="underline hover:text-[#444488]">
                  {protocol.article_title ?? 'View article'}
                </a>
              {:else}
                {protocol.article_title ?? 'Source protocol'}
              {/if}
              {#if protocol.pdb_id}<span class="text-xs font-mono text-gray-400 ml-2">PDB {protocol.pdb_id}</span>{/if}
            </h3>
            {#if protocol.purification_text}
              <details>
                <summary class="text-sm text-[#333366] cursor-pointer">Extracted purification text</summary>
                <p class="text-sm text-[#3C4649] whitespace-pre-wrap mt-1">{protocol.purification_text}</p>
              </details>
            {/if}
            {@render stepsFor(sourceKey(protocol))}
          </div>
        {/each}
      </div>
    </section>

    {#if otherPapers.length}
      <section>
        <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-3">Papers read without a protocol</h2>
        <div class="space-y-4">
          {#each otherPapers as [key, group] (key)}
            <div class="space-y-2">
              <h3 class="font-semibold text-[#333366]">{group[0].subject ?? key}</h3>
              {#each group as step, i (i)}
                {@render stepDetails(step)}
              {/each}
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <section>
      <h2 class="text-lg font-bold text-[#333366] border-b border-gray-300 pb-1 mb-3">Synthesis</h2>
      <div class="space-y-2">
        {#each synthesisSteps.filter(s => s.agent === 'planner') as step, i (i)}
          {@render stepDetails(step)}
        {/each}
        {#if result.raw_plan}
          <details>
            <summary class="text-sm text-[#333366] cursor-pointer">The planner's draft</summary>
            <p class="text-sm text-[#3C4649] whitespace-pre-wrap mt-1">{result.raw_plan}</p>
          </details>
        {/if}
        {#each synthesisSteps.filter(s => s.agent === 'formatter') as step, i (i)}
          {@render stepDetails(step)}
        {/each}
        {#if synthesisSteps.length === 0}
          <p class="text-sm text-gray-500">No synthesis steps were recorded.</p>
        {/if}
      </div>
    </section>
  {/if}
</div>
