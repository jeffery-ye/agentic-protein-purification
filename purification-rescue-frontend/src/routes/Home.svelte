<script lang="ts">
  import AnalysisForm from "../components/home/AnalysisForm.svelte";
</script>

<div class="w-full">
    <h2 class="text-[#3C4649] text-2xl font-normal mb-6 pb-2 border-b border-gray-200">
        New Analysis
    </h2>
    
    <p class="mb-4 text-sm leading-relaxed">
        This tool uses AI literature analysis to create and optimize purification protocols. 
        Enter your protein sequence, UniProt ID, or SSGCID ID to identify similar protocols and optimize your purification.
    </p>

    <AnalysisForm />
</div>
