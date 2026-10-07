"""The planner's default heuristics (#72) and computed target properties (#81)."""

import pytest
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from agent_engine.agent_tools.sequence_properties import sequence_properties
from agent_engine.agents.comprehensive_protocol_agent import SuggestedProtocolAgent

SEQUENCE = "MKAWVTLLAGLLAAQ"


@pytest.fixture
def agent():
    return SuggestedProtocolAgent()


def test_properties_are_pinned_for_a_known_sequence():
    assert sequence_properties(f">target\n{SEQUENCE}\n") == {
        "theoretical_pi": 8.5,
        "molecular_weight_da": 1586,
    }


@pytest.mark.parametrize(
    "fasta",
    [
        # UniProt's REST API wraps sequences at 60 residues.
        f">sp|P00000|TEST_HUMAN Test protein\n{SEQUENCE[:6]}\n{SEQUENCE[6:]}\n",
        # A translated CTTdb sequence, as _resolve_input formats it.
        SeqRecord(Seq(SEQUENCE), id="ABC.1", description="").format("fasta"),
        # A pasted record with a stop codon and stray spaces.
        f">pasted\n{SEQUENCE[:6]} {SEQUENCE[6:]}*\n",
    ],
)
def test_every_input_route_yields_the_same_properties(fasta):
    assert sequence_properties(fasta) == sequence_properties(f">target\n{SEQUENCE}\n")


def test_only_the_first_record_is_analysed():
    two = f">first\n{SEQUENCE}\n>second\nGGGGGGGG\n"
    assert sequence_properties(two) == sequence_properties(f">first\n{SEQUENCE}\n")


@pytest.mark.parametrize("fasta", [">x\nMKXWV\n", ">x\nMKBWV\n", ">header only\n", "P9WQA3", ""])
def test_unanalysable_input_gives_no_values_rather_than_an_error(fasta):
    assert sequence_properties(fasta) is None


def test_planner_prompt_carries_the_computed_values(agent):
    prompt = agent._construct_planner_prompt(
        [], None, {"theoretical_pi": 8.5, "molecular_weight_da": 1586}
    )
    assert "Theoretical pI: 8.5 (computed from the full input sequence)" in prompt
    assert "Molecular weight: 1586 Da (computed from the full input sequence)" in prompt
    # Without a UniProt match there is no organism to report, rather than "Unknown".
    assert "Organism:" not in prompt


def test_planner_prompt_without_metadata_has_no_metadata_block(agent):
    assert "### TARGET METADATA" not in agent._construct_planner_prompt([], None, None)


def test_heuristics_are_defaults_the_planner_may_depart_from(agent):
    prompt = agent._construct_planner_prompt([], None, None)
    assert "### DEFAULT PURIFICATION HEURISTICS" in prompt
    assert "give a reason to depart from one" in prompt
    assert "UNIVERSAL" not in prompt
    assert "strictly adhere" not in prompt


def test_neither_prompt_asks_for_a_departures_section(agent):
    """Departures aren't reported, so the output stays the protocol alone."""
    planner = agent._construct_planner_prompt([], None, None)
    formatter = agent._construct_formatter_prompt("raw plan")
    assert "Departures" not in planner and "Departures" not in formatter
    assert "**Step 5: Storage:**" in formatter
