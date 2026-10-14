from types import SimpleNamespace
from unittest import TestCase, mock

from agno.media import File

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

    def test_upload_content_file_preserves_pdf_filename_and_mime_type(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        client = SimpleNamespace(
            files=SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="file_123")))
        )
        model.get_client = mock.Mock(return_value=client)

        file = File(
            content=b"%PDF-test",
            filename="clinical-report.pdf",
            name="clinical-report.pdf",
            mime_type="application/pdf",
            file_type="pdf",
        )

        self.assertEqual(model._upload_file(file), "file_123")
        client.files.create.assert_called_once_with(
            file=("clinical-report.pdf", b"%PDF-test", "application/pdf"),
            purpose="assistants",
        )

    def test_upload_content_file_derives_extension_from_mime_type(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        client = SimpleNamespace(
            files=SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(id="file_456")))
        )
        model.get_client = mock.Mock(return_value=client)

        file = File(content=b"%PDF-test", name="attachment", mime_type="application/pdf")

        self.assertEqual(model._upload_file(file), "file_456")
        upload = client.files.create.call_args.kwargs["file"]
        self.assertEqual(upload[0], "attachment.pdf")
        self.assertEqual(upload[1], b"%PDF-test")
        self.assertEqual(upload[2], "application/pdf")

    def test_upload_content_file_without_identifiable_type_is_skipped(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        model.get_client = mock.Mock()

        file = File(content=b"unknown", name="attachment")

        self.assertIsNone(model._upload_file(file))
        model.get_client.assert_not_called()

    def test_create_vector_store_waits_for_file_batch_completion(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        create_store = mock.Mock(return_value=SimpleNamespace(id="vs_123"))
        create_and_poll = mock.Mock(return_value=SimpleNamespace(status="completed", file_counts=None))
        client = SimpleNamespace(
            vector_stores=SimpleNamespace(
                create=create_store,
                file_batches=SimpleNamespace(create_and_poll=create_and_poll),
            )
        )
        model.get_client = mock.Mock(return_value=client)

        self.assertEqual(model._create_vector_store(["file_1", "file_2"]), "vs_123")
        create_and_poll.assert_called_once_with(
            vector_store_id="vs_123",
            file_ids=["file_1", "file_2"],
            poll_interval_ms=500,
        )

    def test_create_vector_store_rejects_failed_indexing(self):
        model = SanitizedOpenAIResponses(id="gpt-6-luna", api_key="test")
        client = SimpleNamespace(
            vector_stores=SimpleNamespace(
                create=mock.Mock(return_value=SimpleNamespace(id="vs_failed")),
                file_batches=SimpleNamespace(
                    create_and_poll=mock.Mock(
                        return_value=SimpleNamespace(status="failed", file_counts={"failed": 1})
                    )
                ),
            )
        )
        model.get_client = mock.Mock(return_value=client)

        with self.assertRaisesRegex(ValueError, "could not finish indexing"):
            model._create_vector_store(["file_bad"])
