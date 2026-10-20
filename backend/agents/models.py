import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from agno.models.message import Message
from agno.models.openai import OpenAIChat
from agno.models.openai.responses import OpenAIResponses
from agno.models.response import ModelResponse
from agno.media import File
from agno.tools.function import FunctionCall, FunctionExecutionResult
from agno.utils.timer import Timer

from .tool_result_sanitizer import sanitize_tool_result_content


class SanitizedOpenAIChat(OpenAIChat):
    last_usage_metrics: Optional[Dict[str, int]] = None

    def reset_usage_metrics(self) -> None:
        self.last_usage_metrics = None

    def _remember_usage_metrics(self, response: ModelResponse) -> None:
        usage = getattr(response, "response_usage", None)
        if usage is None:
            return

        if hasattr(usage, "to_dict"):
            usage_data = usage.to_dict()
        elif hasattr(usage, "model_dump"):
            usage_data = usage.model_dump()
        elif isinstance(usage, dict):
            usage_data = usage
        else:
            usage_data = usage.__dict__

        self.last_usage_metrics = {
            "input_tokens": int(usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0)) or 0),
            "output_tokens": int(usage_data.get("output_tokens", usage_data.get("completion_tokens", 0)) or 0),
            "total_tokens": int(usage_data.get("total_tokens", 0) or 0),
        }

    def create_function_call_result(
        self,
        function_call: FunctionCall,
        success: bool,
        output: Optional[Union[List[Any], str]] = None,
        timer: Optional[Timer] = None,
        function_execution_result: Optional[FunctionExecutionResult] = None,
    ) -> Message:
        sanitized_output = sanitize_tool_result_content(output) if success else output
        return super().create_function_call_result(
            function_call=function_call,
            success=success,
            output=sanitized_output,
            timer=timer,
            function_execution_result=function_execution_result,
        )

    def _parse_provider_response(self, *args, **kwargs) -> ModelResponse:
        response = super()._parse_provider_response(*args, **kwargs)
        self._remember_usage_metrics(response)
        return response

    def _parse_provider_response_delta(self, *args, **kwargs) -> ModelResponse:
        response = super()._parse_provider_response_delta(*args, **kwargs)
        self._remember_usage_metrics(response)
        return response


class SanitizedOpenAIResponses(OpenAIResponses):
    """Responses API model with Vania's tool-output safety and billing metrics."""

    last_usage_metrics: Optional[Dict[str, int]] = None

    def reset_usage_metrics(self) -> None:
        self.last_usage_metrics = None

    @staticmethod
    def _content_file_name(file: File) -> Optional[str]:
        """Return a filename that OpenAI can use to identify the file type.

        Agno 2.3.2 uploads ``File.content`` as raw bytes. The OpenAI SDK then
        assigns no filename, so Responses file-search rejects otherwise valid
        PDFs with ``Files with extensions [none] are not supported``.
        """
        candidate = getattr(file, "filename", None) or getattr(file, "name", None)
        if candidate:
            candidate = Path(str(candidate)).name

        mime_type = getattr(file, "mime_type", None)
        suffix = Path(candidate).suffix if candidate else ""
        if not suffix and mime_type:
            guessed = mimetypes.guess_extension(str(mime_type).split(";", 1)[0].strip())
            if guessed:
                candidate = f"{candidate or 'attachment'}{guessed}"

        if not candidate or not Path(candidate).suffix:
            return None
        return candidate

    def _upload_file(self, file: File) -> Optional[str]:
        """Upload content-backed files without dropping filename metadata."""
        if file.content is None or file.url is not None or file.filepath is not None:
            return super()._upload_file(file)

        file_name = self._content_file_name(file)
        if not file_name:
            # Skipping an unidentifiable file keeps the whole agent run alive.
            # Vania's tool result still contains attachment metadata, allowing
            # the assistant to report that the attachment was not inspected.
            return None

        mime_type = (
            getattr(file, "mime_type", None)
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        result = self.get_client().files.create(
            file=(file_name, file.content, mime_type),
            purpose="assistants",
        )
        return result.id

    def _create_vector_store(self, file_ids: List[str]) -> str:
        """Create a vector store and wait until every uploaded file is indexed.

        Agno 2.3.2 polls ``vector_stores.files.list`` immediately and treats an
        empty, eventually-consistent first response as completion. That lets the
        next model call run before the PDFs are searchable. The SDK's batch
        helper waits for the terminal indexing state instead.
        """
        vector_store = self.get_client().vector_stores.create(name=self.vector_store_name)
        batch = self.get_client().vector_stores.file_batches.create_and_poll(
            vector_store_id=vector_store.id,
            file_ids=file_ids,
            poll_interval_ms=500,
        )
        if getattr(batch, "status", None) != "completed":
            counts = getattr(batch, "file_counts", None)
            raise ValueError(
                f"OpenAI could not finish indexing attached files (status={getattr(batch, 'status', None)}, "
                f"file_counts={counts})."
            )
        return vector_store.id

    def _remember_usage_metrics(self, response: ModelResponse) -> None:
        usage = getattr(response, "response_usage", None)
        if usage is None:
            return

        if hasattr(usage, "to_dict"):
            usage_data = usage.to_dict()
        elif hasattr(usage, "model_dump"):
            usage_data = usage.model_dump()
        elif isinstance(usage, dict):
            usage_data = usage
        else:
            usage_data = usage.__dict__

        self.last_usage_metrics = {
            "input_tokens": int(usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0)) or 0),
            "output_tokens": int(usage_data.get("output_tokens", usage_data.get("completion_tokens", 0)) or 0),
            "total_tokens": int(usage_data.get("total_tokens", 0) or 0),
        }

    def create_function_call_result(
        self,
        function_call: FunctionCall,
        success: bool,
        output: Optional[Union[List[Any], str]] = None,
        timer: Optional[Timer] = None,
        function_execution_result: Optional[FunctionExecutionResult] = None,
    ) -> Message:
        sanitized_output = sanitize_tool_result_content(output) if success else output
        return super().create_function_call_result(
            function_call=function_call,
            success=success,
            output=sanitized_output,
            timer=timer,
            function_execution_result=function_execution_result,
        )

    def _parse_provider_response(self, *args, **kwargs) -> ModelResponse:
        response = super()._parse_provider_response(*args, **kwargs)
        self._remember_usage_metrics(response)
        return response

    def _parse_provider_response_delta(self, *args, **kwargs):
        response, tool_use = super()._parse_provider_response_delta(*args, **kwargs)
        self._remember_usage_metrics(response)
        return response, tool_use
