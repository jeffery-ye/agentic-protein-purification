"""
This is a pydanticai agent that extracts protein purification protocols from PMC articles
"""

from typing import List

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from ..llm import reasoning_model
from ..models import BufferStep, ExtractedStep
from ..trace import run_traced


class PurificationProtocol(BaseModel):
    steps: List[ExtractedStep] = Field(
        description=(
            "A list of all experimental steps from the text that use a defined buffer, "
            "in the order the procedure performs them."
        )
    )


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
                4.  **Salts with Concentrations (`salt_type`):** Give every salt with its concentration as the text states it (e.g., "300 mM NaCl, 5 mM MgCl2"), including the salt in binding, wash and elution buffers. Give a salt's name alone only when the text states no concentration for it.
                5.  **Procedure Order:** List the steps in the order the procedure performs them.
                6.  **Ignore Vague Steps:** If a buffer is mentioned but its composition is not detailed (e.g., "washed with PBS," "prepared according to the manufacturer's instructions"), disregard that step entirely. Only include steps with explicit component lists.

                Process the following text and generate the JSON output.
                """
            ),
        )

    def find_protocol(self, methods: str) -> List[BufferStep]:
        raw_output = run_traced(self.agent, "structuring", methods).output
        protocol_data = None

        if isinstance(raw_output, PurificationProtocol):
            protocol_data = raw_output
        else:
            print(f"Received an unexpected output type: {type(raw_output)}")
            return []

        if protocol_data and protocol_data.steps:
            print(f"\nSuccessfully parsed protocol. Found {len(protocol_data.steps)} buffer steps.")
            # Numbered here, not by the model, in the order it extracted them.
            return [
                BufferStep(**step.model_dump(), step_number=number)
                for number, step in enumerate(protocol_data.steps, start=1)
            ]
        else:
            print("\nCould not obtain structured data from the LLM")
            return []
