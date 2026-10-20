from django.test import TestCase

from billing.models import SubscriptionPlan
from definitions.agents import AGENTS as DISCOVERED_AGENTS
from definitions.agents.expert_specialties.common import PROMPT_WRAPPER_HEADER, load_prompt
from definitions.billing import (
    GENERAL_DOCTOR_AGENT_SLUGS,
    LAWYER_AGENT_SLUGS,
    PLANS,
    PSYCHIATRIST_AGENT_SLUGS,
    PSYCHOLOGIST_AGENT_SLUGS,
)
from definitions.sync import DefinitionSync
from services.models import AgentService
from services.models_canvas import AgentCanvasConfig


EXPECTED_AGENT_CONFIG = {
    "expert-psychologist-assistant": {
        "profession": "psychologist",
        "sources": [
            "tashkil-parvande",
            "ravansanj",
            "tarahi-darman",
            "tarahi-jalasat-darman",
            "tarahi-jalasat-ravan-darman",
        ],
    },
    "expert-psychiatrist-assistant": {
        "profession": "psychiatrist",
        "sources": [
            "tashkil-parvande",
            "ravansanj",
            "tarahi-darman",
            "tarahi-jalasat-darman",
            "tarahi-jalasat-daro-darman",
        ],
    },
    "expert-lawyer-assistant": {
        "profession": "lawyer",
        "sources": ["vakil"],
    },
    "expert-general-doctor-assistant": {
        "profession": "general_doctor",
        "sources": ["general-doctor"],
    },
}


class ExpertSpecialtyAgentTests(TestCase):
    def _agent_by_slug(self, slug: str):
        return next(agent for agent in DISCOVERED_AGENTS if agent.slug == slug)

    def test_agent_discovery_includes_new_specialty_agents(self):
        discovered_slugs = {agent.slug for agent in DISCOVERED_AGENTS}
        self.assertTrue(set(EXPECTED_AGENT_CONFIG).issubset(discovered_slugs))

    def test_new_agents_have_expected_workspace_config(self):
        for slug, expected in EXPECTED_AGENT_CONFIG.items():
            agent = self._agent_by_slug(slug)
            self.assertEqual(agent.audience, "EXPERT")
            self.assertEqual(agent.eligible_expert_professions, [expected["profession"]])
            self.assertTrue(agent.requires_visitor_selector)
            self.assertEqual(agent.capabilities, ["vania_expert"])
            self.assertEqual(agent.default_open_canvases, ["VANIA_PATIENT_MANAGER"])
            self.assertEqual(agent.model_id, "gpt-5.4")
            self.assertEqual(agent.static_tools, ["duckduckgo"])
            self.assertTrue(agent.extra_config.get("has_canvas"))

    def test_prompt_composition_keeps_wrapper_and_source_order(self):
        psychologist_prompt = self._agent_by_slug("expert-psychologist-assistant").system_prompt
        self.assertIn(PROMPT_WRAPPER_HEADER, psychologist_prompt)

        ordered_sources = [
            load_prompt(module_name).strip()
            for module_name in EXPECTED_AGENT_CONFIG["expert-psychologist-assistant"]["sources"]
        ]
        last_index = psychologist_prompt.index(PROMPT_WRAPPER_HEADER)
        for prompt_body in ordered_sources:
            marker = prompt_body[:120].strip()
            current_index = psychologist_prompt.index(marker)
            self.assertGreater(current_index, last_index)
            last_index = current_index

    def test_billing_lists_include_new_specialty_agents(self):
        self.assertIn("expert-lawyer-assistant", LAWYER_AGENT_SLUGS)
        self.assertIn("expert-psychiatrist-assistant", PSYCHIATRIST_AGENT_SLUGS)
        self.assertIn("expert-psychologist-assistant", PSYCHOLOGIST_AGENT_SLUGS)
        self.assertIn("expert-general-doctor-assistant", GENERAL_DOCTOR_AGENT_SLUGS)

    def test_sync_persists_new_agents_and_plan_memberships(self):
        DefinitionSync.sync_agents()
        DefinitionSync.sync_plans_and_products()

        for slug in EXPECTED_AGENT_CONFIG:
            synced = AgentService.objects.get(slug=slug)
            self.assertEqual(synced.audience, "EXPERT")
            self.assertEqual(synced.capabilities, ["vania_expert"])
            self.assertTrue(synced.requires_visitor_selector)
            self.assertEqual(synced.eligible_expert_professions, [EXPECTED_AGENT_CONFIG[slug]["profession"]])
            self.assertTrue(
                AgentCanvasConfig.objects.filter(agent=synced, canvas__component_key="VANIA_PATIENT_MANAGER").exists()
            )

        plan_expectations = {
            "expert-lawyer-1500-30d": "expert-lawyer-assistant",
            "expert-psychiatrist-1500-30d": "expert-psychiatrist-assistant",
            "expert-psychologist-1500-30d": "expert-psychologist-assistant",
            "expert-general_doctor-1500-30d": "expert-general-doctor-assistant",
        }
        for plan_slug, agent_slug in plan_expectations.items():
            plan = SubscriptionPlan.objects.get(slug=plan_slug)
            self.assertTrue(plan.agents.filter(slug=agent_slug).exists())

    def test_plan_definitions_reference_new_agents(self):
        plans_by_slug = {plan.slug: plan for plan in PLANS}
        self.assertIn("expert-lawyer-assistant", plans_by_slug["expert-lawyer-1500-30d"].included_agent_slugs)
        self.assertIn("expert-psychiatrist-assistant", plans_by_slug["expert-psychiatrist-1500-30d"].included_agent_slugs)
        self.assertIn("expert-psychologist-assistant", plans_by_slug["expert-psychologist-1500-30d"].included_agent_slugs)
        self.assertIn("expert-general-doctor-assistant", plans_by_slug["expert-general_doctor-1500-30d"].included_agent_slugs)
