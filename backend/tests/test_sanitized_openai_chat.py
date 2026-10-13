from types import SimpleNamespace
from unittest import TestCase

from agents.service_agent import _extract_model_usage_metrics
from agents.models import SanitizedOpenAIChat, SanitizedOpenAIResponses


class SanitizedOpenAIChatTests(TestCase):
    def test_create_function_call_result_strips_custom_event_repr(self):
        model = SanitizedOpenAIChat(id="gpt-4o-mini", api_key="test")
        function_call = SimpleNamespace(
            call_id="call_123",
            arguments={"case_id": "1"},
            function=SimpleNamespace(name="create_case", stop_after_tool_call=False),
        )

        message = model.create_function_call_result(
            function_call=function_call,
            success=True,
            output=(
                "CanvasUpdateEvent(created_at=1, event='CustomEvent', name='CANVAS_UPDATE', "
                "value={'delta': {'cases': []}})\n✅ Case created."
            ),
        )

        self.assertEqual(message.content, "✅ Case created.")

    def test_remember_usage_metrics_keeps_latest_prompt_and_completion_tokens(self):
        model = SanitizedOpenAIChat(id="gpt-5.4", api_key="test")
        response = SimpleNamespace(
            response_usage=SimpleNamespace(
                input_tokens=13,
                output_tokens=7,
                total_tokens=20,
            )
        )

        model._remember_usage_metrics(response)

        self.assertEqual(
            model.last_usage_metrics,
            {
                "input_tokens": 13,
                "output_tokens": 7,
                "total_tokens": 20,
            },
        )

    def test_extract_model_usage_metrics_reads_cached_usage(self):
        model = SanitizedOpenAIChat(id="gpt-5.4", api_key="test")
        model.last_usage_metrics = {
            "input_tokens": 21,
            "output_tokens": 9,
            "total_tokens": 30,
        }

        self.assertEqual(
            _extract_model_usage_metrics(model),
            {
                "input_tokens": 21,
                "output_tokens": 9,
                "total_tokens": 30,
            },
        )


class SanitizedOpenAIResponsesTests(TestCase):
    def test_create_function_call_result_strips_custom_event_repr(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        function_call = SimpleNamespace(
            call_id="call_456",
            arguments={"case_id": "2"},
            function=SimpleNamespace(name="create_case", stop_after_tool_call=False),
        )

        message = model.create_function_call_result(
            function_call=function_call,
            success=True,
            output=(
                "CanvasUpdateEvent(created_at=1, event='CustomEvent', name='CANVAS_UPDATE', "
                "value={'delta': {'cases': []}})\n✅ Case created through Responses."
            ),
        )

        self.assertEqual(message.content, "✅ Case created through Responses.")

    def test_remember_usage_metrics_supports_responses_metrics(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        response = SimpleNamespace(
            response_usage=SimpleNamespace(
                input_tokens=34,
                output_tokens=11,
                total_tokens=45,
            )
        )

        model._remember_usage_metrics(response)

        self.assertEqual(
            model.last_usage_metrics,
            {
                "input_tokens": 34,
                "output_tokens": 11,
                "total_tokens": 45,
            },
        )
