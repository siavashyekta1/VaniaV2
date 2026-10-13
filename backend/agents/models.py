from typing import Any, Dict, List, Optional, Union

from agno.models.message import Message
from agno.models.openai import OpenAIChat
from agno.models.openai.responses import OpenAIResponses
from agno.models.response import ModelResponse
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
