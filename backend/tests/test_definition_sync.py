from django.test import TestCase, override_settings

from definitions.sync import DefinitionSync
from definitions.agents import AGENTS
from services.models import AgentService
from users.models import CustomUser


class DefinitionSyncAdminTests(TestCase):
    def test_sync_admin_user_creates_bootstrap_admin(self):
        DefinitionSync.sync_admin_user()

        user = CustomUser.objects.get(phone_number="09123456789")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("Adminadmin@123"))

    @override_settings(DEBUG=True)
    def test_sync_admin_user_resets_bootstrap_password_in_debug(self):
        user = CustomUser.objects.create_superuser(
            phone_number="09123456789",
            password="AnotherPass123!",
            email="admin@example.com",
            full_name="مدیر سیستم",
        )
        self.assertTrue(user.check_password("AnotherPass123!"))

        DefinitionSync.sync_admin_user()

        user.refresh_from_db()
        self.assertTrue(user.check_password("Adminadmin@123"))


class DefinitionSyncAgentPromptTests(TestCase):
    def test_sync_preserves_existing_prompt_unless_slug_is_explicit(self):
        slug = "tarahi-jalasat-ravan-darman"
        code_prompt = next(agent.system_prompt for agent in AGENTS if agent.slug == slug)

        DefinitionSync.sync_agents()
        service = AgentService.objects.get(slug=slug)
        service.system_prompt = "database-managed prompt"
        service.save(update_fields=["system_prompt"])

        DefinitionSync.sync_agents()
        service.refresh_from_db()
        self.assertEqual(service.system_prompt, "database-managed prompt")

        DefinitionSync.sync_agents(prompt_slugs={slug})
        service.refresh_from_db()
        self.assertEqual(service.system_prompt, code_prompt)
