import unittest
import warnings

from pydantic import TypeAdapter

from app.agent.aiops.state import Plan, PlanExecuteState


class StateSerializationTest(unittest.TestCase):
    def test_past_steps_serialize_without_pydantic_warning(self):
        state: PlanExecuteState = {
            "input": "diagnose",
            "plan": [],
            "past_steps": [("query_prometheus_metrics", "result")],
            "response": "report",
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            TypeAdapter(PlanExecuteState).dump_python(state)

        messages = [str(item.message) for item in caught]
        self.assertFalse(any("PydanticSerializationUnexpectedValue" in item for item in messages))

    def test_structured_plan_serializes_without_pydantic_warning(self):
        state: PlanExecuteState = {
            "input": "diagnose",
            "plan": Plan(steps=["query metrics", "generate report"]),
            "past_steps": [],
            "response": "",
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            dumped = TypeAdapter(PlanExecuteState).dump_python(state)

        self.assertEqual(dumped["plan"]["steps"], ["query metrics", "generate report"])
        messages = [str(item.message) for item in caught]
        self.assertFalse(any("PydanticSerializationUnexpectedValue" in item for item in messages))
