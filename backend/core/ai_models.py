import os
from dataclasses import dataclass
from typing import Optional


CHAT_MODEL_OVERRIDE_ENV = "VANIA_CHAT_MODEL_OVERRIDE"
EXPERT_RESPONSES_ENABLED_ENV = "VANIA_EXPERT_RESPONSES_ENABLED"
EXPERT_RESPONSES_REASONING_EFFORT_ENV = "VANIA_EXPERT_RESPONSES_REASONING_EFFORT"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_EXPERT_RESPONSES_REASONING_EFFORTS = {"low", "medium", "high"}

_NATIVE_REASONING_MODEL_PREFIXES = (
    "o1",
    "o3",
    "gpt-5",
    "gpt5",
    "gpt-6",
    "gpt6",
    "deepseek-r1",
    "deepseek-reasoner",
)

_NONE_EFFORT_MODELS = {
    "gpt-5",
    "gpt5",
    "gpt-5.1",
    "gpt5.1",
    "gpt-5.2",
    "gpt5.2",
    "gpt-6-luna",
}


@dataclass(frozen=True)
class ChatModelSelection:
    model_id: str
    source: str


def normalize_model_id(model_id: Optional[str]) -> str:
    return (model_id or "").strip().lower()


def get_chat_model_override() -> Optional[str]:
    """Return the optional runtime-only model override used by isolated environments."""
    value = (os.getenv(CHAT_MODEL_OVERRIDE_ENV) or "").strip()
    return value or None


def expert_responses_enabled() -> bool:
    """Return whether the isolated expert Responses API pilot is enabled."""
    value = (os.getenv(EXPERT_RESPONSES_ENABLED_ENV) or "").strip().lower()
    return value in _TRUE_VALUES


def get_expert_responses_reasoning_effort() -> str:
    """Resolve the pilot effort supported by the currently pinned Agno version."""
    value = (os.getenv(EXPERT_RESPONSES_REASONING_EFFORT_ENV) or "high").strip().lower()
    if value not in _EXPERT_RESPONSES_REASONING_EFFORTS:
        return "high"
    return value


def should_use_expert_responses(*, audience: Optional[str], model_id: Optional[str]) -> bool:
    """Scope the pilot to Luna-powered expert assistants only."""
    model = normalize_model_id(model_id)
    is_luna = model == "gpt-6-luna" or model.startswith("gpt-6-luna-")
    return expert_responses_enabled() and (audience or "").upper() == "EXPERT" and is_luna


def resolve_chat_model(
    *,
    service_model_id: str,
    is_demo_mode: bool,
    demo_model_override: Optional[str] = None,
    environment_override: Optional[str] = None,
) -> ChatModelSelection:
    """Resolve the effective chat model without mutating synced agent definitions."""
    model_id = (service_model_id or "gpt-4o").strip() or "gpt-4o"
    source = "agent_service"

    if is_demo_mode and demo_model_override:
        cleaned_demo_model = demo_model_override.strip()
        if cleaned_demo_model:
            model_id = cleaned_demo_model
            source = "demo_override"

    override = environment_override
    if override is None:
        override = get_chat_model_override()
    if override:
        cleaned_override = override.strip()
        if cleaned_override:
            model_id = cleaned_override
            source = "stage_environment_override"

    return ChatModelSelection(model_id=model_id, source=source)


def is_native_reasoning_model(model_id: Optional[str]) -> bool:
    model = normalize_model_id(model_id)
    return any(model.startswith(prefix) for prefix in _NATIVE_REASONING_MODEL_PREFIXES)


def supports_none_reasoning_effort(model_id: Optional[str]) -> bool:
    model = normalize_model_id(model_id)
    return model in _NONE_EFFORT_MODELS or model.startswith("gpt-6-luna-")


def requires_none_for_chat_tool_calling(model_id: Optional[str]) -> bool:
    """GPT-6 Luna requires none effort for function calling via Chat Completions."""
    model = normalize_model_id(model_id)
    return model == "gpt-6-luna" or model.startswith("gpt-6-luna-")


def resolve_reasoning_effort(
    model_id: Optional[str],
    requested_effort: Optional[str],
    *,
    use_responses_api: bool = False,
) -> Optional[str]:
    if not is_native_reasoning_model(model_id):
        return None

    if requires_none_for_chat_tool_calling(model_id) and not use_responses_api:
        return "none"

    effort = (requested_effort or "").strip().lower()
    if not effort or effort == "default":
        return None
    if effort == "none" and not supports_none_reasoning_effort(model_id):
        return None
    return effort
