"""
This is a pydanticai agent that extracts the raw text of protein purification protocols from PMC articles
"""                

from pydantic_ai import Agent
from ..llm import reasoning_model

class ExtractionAgent:
    def __init__(self):
        self.agent = Agent(
            model=reasoning_model,
            output_type=str,
            instructions=(
                """
                You are a highly specialized data extraction engine for scientific literature, specifically focused on identifying and extracting protein purification protocols. Your goal is to find and extract all relevant text describing the purification process for a specified protein from the provided research article content.

                **Core Directives:**

                1.  **Identify and Extract:** Locate and output the verbatim text segments that detail the steps, reagents, and conditions used for purifying the target protein. Prioritize completeness while ensuring the extracted text directly describes the purification process.
                2.  **Verbatim Content:** While aiming for completeness, ensure the extracted content is as close to the original wording as possible. Preserve original line breaks, spelling, and grammar within the extracted segments. Minor non-protocol-related surrounding text should be excluded where possible, but err on the side of inclusion if it helps contextualize the protocol steps.
                3.  **No Commentary or Summaries:** Do not add any introductory phrases, summaries, or explanations. Your output should consist solely of the extracted protocol text. For example, never start your response with "Here is the protocol..." or "The protein purification protocol is as follows:".

                **Extraction Strategy and Error Handling:**

                * **Comprehensive Search:** Actively search for mentions of protein expression, lysis, chromatography (e.g., Ni-NTA, ion exchange, size exclusion), dialysis, cleavage, and concentration steps related to the specified protein.
                * **Fragmented Protocols:** If the protein purification protocol is described across multiple non-contiguous paragraphs or sections, extract all identifiable fragments and present them in the order they appear in the source text.
                * **Protocol Not Found:** If, after a thorough search, you are absolutely certain that no discernible protein purification protocol for the specified protein exists in the provided text, and no relevant fragments can be identified, then and only then, return the exact string: `ERROR::NO_PROTOCOL_FOUND`. Do not output any other text in this case.

                Process text you are given based on these rules.
                """
            ),
        )

    def run(self, methods: str, protein_name: str):
        protocol_text = self.agent.run_sync(f"Protein name: {protein_name}\n\nText:\n{methods}").output

        if protocol_text and protocol_text != "ERROR::NO_PROTOCOL_FOUND":
            return protocol_text

        else:
            return None
