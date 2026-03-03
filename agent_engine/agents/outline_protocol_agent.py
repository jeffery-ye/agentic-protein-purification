"""
This is a pydanticai agent that extracts protein purification protocols from PMC articles
"""                

from pydantic import BaseModel, Field
from typing import List, Optional
from pydantic_ai import Agent
from ..llm import reasoning_model

class BufferStep(BaseModel):
    """
    This class returns a tabular description of protocols, using pydantic's type validation to ensure consistent output
    """
    purification_step: Optional[str] = Field(
        ...,
        description=(
            "A highly specific name for the purification step. "
            "Combine the exact technique or resin name from the text with the action (e.g., Lysis, Wash, Elution). "
            "Examples: 'Ni-NTA Affinity Chromatography - Wash', 'Cell Extraction'."
        )
    )
    buffer_name: Optional[str] = Field(
        None,
        description="The specific name given to the buffer in the text, if any (e.g., 'Buffer A', 'Lysis Buffer')."
    )
    
    buffer_composition: Optional[str] = Field(
        None,
        description="List buffering agents and their concentrations (e.g., '50 mM Tris', '20 mM HEPES'). If not specified, leave as null."
    )
    
    ph: Optional[float] = Field(
        None, 
        description="The pH of the buffer, as a number (e.g., 8.0). If not mentioned, leave as null."
    ) 
    
    salt_type: Optional[str] = Field(
        None,
        description="List ALL salts. For example: 'NaCl, MgCl2'."
    )
    
    buffer_supplement: Optional[str] = Field(
        None,
        description="List additives present in the buffer like reducing agents (DTT), nucleotides (ATP), detergents, or cryoprotectants (glycerol). For example: '2 mM DTT, 30mM imidazole'."
    )

class PurificationProtocol(BaseModel):
    steps: List[BufferStep] = Field(description="A list of all experimental steps from the text that use a defined buffer.")
    
    
class ProtocolAgent:
    def __init__(self):
        self.agent = Agent(
            model=reasoning_model,
            output_type=PurificationProtocol,
            instructions=(
                """
                You are a precision data extraction engine specializing in parsing scientific literature. Your sole function is to read the provided text and extract protein purification protocol details into a structured JSON format.

                **Your Goal:** Identify every experimental step involving a buffer and extract its complete composition.

                **Core Rules:**
                1.  **Schema Adherence:** Your final output MUST be a single, valid JSON object that strictly adheres to the `PurificationProtocol` schema. Do not include any other text, explanations, or markdown formatting (like ```json).
                2.  **Data Fidelity:** Extract ONLY information explicitly stated in the text. If a detail (e.g., pH, a specific salt) is not mentioned for a step, its corresponding field must be `null`. DO NOT infer, calculate, or invent data.
                3.  **Specificity is Key (`purification_step`):**
                    *   The step name must be highly specific. Combine the technique/resin name with the action (e.g., Lysis, Wash, Elution).
                    *   Use the EXACT names of materials and resins from the text, including their descriptions (e.g., "gradients," "increasing or decreasing amounts," etc.).
                    *   **Example:**
                        *   **Source Text:** "...the M2 anti-FLAG affinity resin was washed three times with wash buffer..."
                        *   **CORRECT:** `"purification_step": "M2 anti-FLAG affinity resin - Wash"`
                        *   **INCORRECT:** `"purification_step": "Affinity column wash"`
                4.  **Ignore Vague Steps:** If a buffer is mentioned but its composition is not detailed (e.g., "washed with PBS," "prepared according to the manufacturer's instructions"), disregard that step entirely. Only include steps with explicit component lists.

                Process the following text and generate the JSON output.
                """
            ),
        )
    
    def find_protocol(self, methods: str) -> List[dict]:
        raw_output = self.agent.run_sync(methods).output
        protocol_data = None

        if isinstance(raw_output, PurificationProtocol):
            protocol_data = raw_output
        else:
            print(f"Received an unexpected output type: {type(raw_output)}")
            return []

        if protocol_data and protocol_data.steps:
            print(f"\nSuccessfully parsed protocol. Found {len(protocol_data.steps)} buffer steps.")
            return [step.model_dump() for step in protocol_data.steps]
        else:
            print("\nCould not obtain structured data from the LLM")
            return []