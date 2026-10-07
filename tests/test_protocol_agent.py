"""The table agent's steps: salts with concentrations, numbered in code (#71)."""

from pydantic_ai.models.test import TestModel

from agent_engine.agents.outline_protocol_agent import ProtocolAgent, PurificationProtocol
from agent_engine.models import BufferStep

STEPS = [
    {"purification_step": "Ni-NTA - Binding", "salt_type": "300 mM NaCl"},
    {"purification_step": "Ni-NTA - Wash", "salt_type": "300 mM NaCl, 5 mM MgCl2"},
    {"purification_step": "Superdex 200 - SEC", "salt_type": "KCl"},
]


def test_steps_are_numbered_in_the_order_the_model_lists_them():
    agent = ProtocolAgent()

    with agent.agent.override(model=TestModel(custom_output_args={"steps": STEPS})):
        steps = agent.find_protocol("Methods text.")

    assert steps == [
        BufferStep(**step, step_number=number) for number, step in enumerate(STEPS, start=1)
    ]


def test_the_model_is_not_asked_for_a_position():
    """Position is the pipeline's to set; the model's schema has no field for it."""
    step_schema = PurificationProtocol.model_json_schema()["$defs"]["ExtractedStep"]

    assert "step_number" not in step_schema["properties"]
    assert "concentrations" in step_schema["properties"]["salt_type"]["description"]


def test_no_steps_gives_an_empty_protocol():
    agent = ProtocolAgent()

    with agent.agent.override(model=TestModel(custom_output_args={"steps": []})):
        assert agent.find_protocol("Nothing here.") == []
