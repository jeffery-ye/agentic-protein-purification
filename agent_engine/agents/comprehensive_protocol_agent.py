from pydantic_ai import Agent
from ..llm import reasoning_model

class SuggestedProtocolAgent:
    """
    Orchestrates a two-step generation process:
    1. PlannerAgent: Analyzes data and drafts a raw strategy (CoT).
    2. FormatterAgent: Reviews the draft and structures it into a final protocol.
    """
    def __init__(self):
        self.reasoning_model = reasoning_model
        
        self.planner_agent = Agent(
            model=self.reasoning_model,
            output_type=str,
            instructions=(
                """
                You are a Principal Scientist in Protein Biochemistry.
                Focus solely on derivation and logic.
                Analyze input metadata and reference protocols to design a purification strategy.
                Perform internal simulations to catch errors (buffer mismatches, protease risks).
                Output a detailed, raw technical draft explaining the 'Why' and 'How'.
                Do not worry about final formatting.
                """
            ),
        )

        self.formatter_agent = Agent(
            model=self.reasoning_model,
            output_type=str,
            instructions=(
                """
                You are a Principal Scientist in Protein Biochemistry.
                Review the provided raw purification strategy for logical consistency.
                Convert the raw strategy into a standardized protocol.
                Ensure strict adherence to the required output structure.
                """
            ),
        )

    def _construct_planner_prompt(self, purifications: list, failed_purification, target_metadata):
        input_string = ""
        
        if target_metadata:
            input_string += "### TARGET METADATA\n"
            input_string += f"Organism: {target_metadata.get('organism', 'Unknown')}\n"
            input_string += f"Location: {', '.join(target_metadata.get('comments', []))}\n"
            if target_metadata.get('features'):
                input_string += "Features: " + ", ".join(target_metadata['features']) + "\n"
        
        input_string += "\n### REFERENCE PROTOCOLS\n"
        if failed_purification:
            input_string += f"FAILED ATTEMPT:\n{failed_purification['purification_text']}\n"
            
        if purifications:
            for p in purifications:
                input_string += f"SUCCESSFUL ({p.get('organism_name', 'Unknown')}):\n{p['purification_text']}\n"
        else:
            input_string += "No successful protocols found.\n"

        input_string += """
        ### SYSTEM PERSONA & MISSION
        You are a Principal Scientist in Protein Biochemistry. Your task is to develop a comprehensive, end-to-end recombinant protein expression and purification strategy. You must focus on rigorous biochemical derivation and logical consistency. Output your step-by-step reasoning (Chain of Thought), followed by the final technical draft. Do not include conversational filler.

        ### UNIVERSAL PURIFICATION LAWS (CRITICAL CONSTRAINTS)
        You must strictly adhere to the following biochemical laws. Violation of these laws will result in protocol failure:
        1. **The pI Rule:** Buffer pH must be at least 1.0 to 1.5 units away from the target protein's isoelectric point (pI) to prevent precipitation. Never set buffer pH equal to the pI.
        2. **The Redox Rule:** Intracellular/cytosolic proteins require reducing agents (e.g., 1 mM TCEP or 1-5 mM DTT) in all buffers. Secreted/extracellular proteins with native disulfide bonds must NOT have reducing agents.
        3. **The SEC Volume Law:** Never load more than 2% to 5% of the Total Column Volume onto a Size Exclusion Chromatography (SEC) column. You must mandate a sample concentration step prior to SEC.
        4. **The Viscosity Law:** All cell lysis buffers must contain a nuclease (e.g., Benzonase) and its required cofactor (1-2 mM MgCl2) to degrade genomic DNA.
        5. **The Protease Compatibility Rule:** IMAC elution fractions containing high imidazole (>50 mM) must be dialyzed or desalted prior to the addition of a site-specific protease (e.g., TEV, HRV 3C), as high imidazole inhibits cleavage.
        6. **The Orthogonal Workflow:** Do not sequence the same separation principle back-to-back. The standard downstream hierarchy is Capture -> Intermediate/Cleavage -> Polishing.

        ### STEP 1: METADATA & PHYSICOCHEMICAL DECODING
        * **Compartment & PTMs:** Is the target Cytosolic, Secreted, Transmembrane, or Nuclear? Are there known PTMs or disulfide bonds?
        * **Properties:** Estimate/calculate the molecular weight (MW) and isoelectric point (pI). Use these to dictate resin choice and buffer pH.
        * **Historical Analysis:** Analyze provided successes/failures. Identify failure modes (e.g., inclusion bodies, degradation, low binding).

        ### STEP 2: UPSTREAM EXPRESSION STRATEGY
        * **Construct Design:** Define the necessary elements: Signal Peptides (for subcellular targeting), Solubility/Affinity Tags (e.g., His, SUMO, GST), Cleavage Sites, and precise boundaries.
        * **Culture Parameters:** Select the appropriate Host Organism (E. coli, HEK293, Insect, etc.). Define induction temperature, duration, and inducer concentration designed to maximize soluble expression over inclusion bodies.

        ### STEP 3: DOWNSTREAM PURIFICATION STRATEGY
        * **Lysis:** Define the mechanical/chemical lysis method and exact lysis buffer composition (pH, salts, additives, protease inhibitors).
        * **Chromatography:** Select the sequence of columns (Capture, Intermediate, Polishing) and their respective Binding, Wash, and Elution buffers.

        ### STEP 4: MENTAL SIMULATION (ERROR CHECKING)
        * **Scenario A - Host Compatibility:** Do the tags interfere with the active site? Are eukaryotic signal peptides correctly swapped out if using a prokaryotic host?
        * **Scenario B - Binding Mechanics:** Verify that the Buffer pH is compatible with the target's pI and the chosen resin's operational limits.
        * **Scenario C - Reagent Clashes:** Are reducing agents erroneously mixed with incompatible resins (e.g., bare Ni-NTA without chelating defenses)?

        ### STEP 5: FINAL RAW STRATEGY
        Synthesize your reasoning into a detailed, sequential technical workflow from gene to pure protein. Ensure you explain the 'Why' behind your choices.
        """
        return input_string

    def _construct_formatter_prompt(self, raw_plan: str):
        return f"""
        ### RAW SCIENTIFIC STRATEGY
        {raw_plan}

        ### INSTRUCTIONS
        1. Check the above plan for any errors, logical inconsistencies, or missing steps.
        2. Format it strictly into the following structure:

        * **Step 0: Construct Design:** (Signal Peptide, Tags, Cleavage Sites, Target Host).
        * **Step 1: Expression Parameters:** (Host strain/cell line, culture media, induction temperature, inducer concentration, duration).
        * **Step 2: Cell Harvest & Lysis/Prep:** (Specific to organism, buffers with exact concentrations, lysis method).
        * **Step 3: Capture (Affinity/IEX):** (Column type, Binding/Wash/Elution buffers, Tag cleavage parameters if applicable).
        * **Step 4: Polishing (SEC/IEX):** (Column type, SEC buffer, target oligomeric state).
        * **Step 5: Storage:** (Final buffer, concentration method, freezing/storage temperature).

        Constraint: Do not add conversational filler, introductions, or conclusions. Output only the structured protocol.
        """

    def run(self, purifications: list, failed_purification=None, target_metadata=None):
        planner_input = self._construct_planner_prompt(purifications, failed_purification, target_metadata)
        
        print("Generating raw plan...")
        raw_plan_result = self.planner_agent.run_sync(planner_input)
        raw_plan = raw_plan_result.output

        formatter_input = self._construct_formatter_prompt(raw_plan)
        
        print("Formatting final protocol...")
        final_protocol_result = self.formatter_agent.run_sync(formatter_input)
        
        return final_protocol_result.output, raw_plan