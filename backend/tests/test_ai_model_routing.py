from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from core.ai_models import (
    get_chat_model_override,
    is_native_reasoning_model,
    requires_none_for_chat_tool_calling,
    resolve_chat_model,
    resolve_reasoning_effort,
    supports_none_reasoning_effort,
)
from services.serializers import ServiceSerializer


class ChatModelRoutingTests(TestCase):
    def test_paid_user_uses_service_model_without_environment_override(self):
        selected = resolve_chat_model(
            service_model_id="gpt-5.4",
            is_demo_mode=False,
            environment_override="",
        )

        self.assertEqual(selected.model_id, "gpt-5.4")
        self.assertEqual(selected.source, "agent_service")

    def test_demo_user_uses_demo_model_without_environment_override(self):
        selected = resolve_chat_model(
            service_model_id="gpt-5.4",
            is_demo_mode=True,
            demo_model_override="gpt-5-mini",
            environment_override="",
        )

        self.assertEqual(selected.model_id, "gpt-5-mini")
        self.assertEqual(selected.source, "demo_override")

    def test_environment_override_wins_for_paid_and_demo_users(self):
        for is_demo_mode in (False, True):
            with self.subTest(is_demo_mode=is_demo_mode):
                selected = resolve_chat_model(
                    service_model_id="gpt-5.4",
                    is_demo_mode=is_demo_mode,
                    demo_model_override="gpt-5-mini",
                    environment_override="gpt-6-luna",
                )

                self.assertEqual(selected.model_id, "gpt-6-luna")
                self.assertEqual(selected.source, "stage_environment_override")

    def test_blank_environment_variable_disables_override(self):
        with patch.dict("os.environ", {"VANIA_CHAT_MODEL_OVERRIDE": "   "}, clear=False):
            self.assertIsNone(get_chat_model_override())

    def test_environment_variable_is_used_when_no_explicit_override_is_passed(self):
        with patch.dict(
            "os.environ",
            {"VANIA_CHAT_MODEL_OVERRIDE": "gpt-6-luna"},
            clear=False,
        ):
            selected = resolve_chat_model(
                service_model_id="gpt-5.4",
                is_demo_mode=False,
            )

        self.assertEqual(selected.model_id, "gpt-6-luna")
        self.assertEqual(selected.source, "stage_environment_override")


class ModelCapabilityTests(TestCase):
    def test_luna_is_native_and_supports_none_effort(self):
        self.assertTrue(is_native_reasoning_model("gpt-6-luna"))
        self.assertTrue(supports_none_reasoning_effort("gpt-6-luna"))
        self.assertTrue(requires_none_for_chat_tool_calling("gpt-6-luna"))

    def test_luna_forces_none_effort_for_chat_tool_compatibility(self):
        for requested in (None, "default", "low", "medium", "high", "none"):
            with self.subTest(requested=requested):
                self.assertEqual(
                    resolve_reasoning_effort("gpt-6-luna", requested),
                    "none",
                )

    def test_existing_non_native_model_behavior_is_preserved(self):
        self.assertFalse(is_native_reasoning_model("gpt-4o-mini"))
        self.assertIsNone(resolve_reasoning_effort("gpt-4o-mini", "high"))

    def test_serializer_classifies_luna_as_native(self):
        obj = SimpleNamespace(model_id="gpt-6-luna", enable_reasoning=False)
        self.assertEqual(ServiceSerializer().get_reasoning_type(obj), "NATIVE")
