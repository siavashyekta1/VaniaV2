from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from services.access_service import access_service
from services.models import AgentService
from services.views import ServiceListView
from users.models import ExpertProfession, UserRole


class StaffAdminAgentAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff_user = get_user_model().objects.create_user(
            phone_number="09120000002",
            is_staff=True,
        )
        self.agent = AgentService.objects.create(
            name="Expert Only Agent",
            slug="expert-only-agent",
            description="",
            system_prompt="Test",
            audience=AgentService.Audience.EXPERT,
            eligible_expert_professions=["psychologist"],
            is_free=False,
            is_active=True,
            is_public=False,
        )

    def test_staff_user_can_access_active_agent_without_role_or_plan(self):
        allowed, reason = access_service.check_permission(self.staff_user, self.agent.slug)

        self.assertTrue(allowed)
        self.assertEqual(reason, "Staff/admin access")

    def test_inactive_agent_stays_blocked_for_staff_user(self):
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active", "updated_at"])
        cache.clear()

        allowed, reason = access_service.check_permission(self.staff_user, self.agent.slug)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Service is currently disabled.")

    def test_staff_service_list_includes_private_ineligible_agents_as_owned(self):
        request = APIRequestFactory().get("/api/services/")
        force_authenticate(request, user=self.staff_user)

        response = ServiceListView.as_view()(request)

        slugs = {item["slug"]: item for item in response.data}
        self.assertIn(self.agent.slug, slugs)
        self.assertTrue(slugs[self.agent.slug]["is_owned"])
        self.assertEqual(slugs[self.agent.slug]["access_status"], "OWNED")

    def test_all_expert_access_unlocks_cross_profession_agent_without_plan(self):
        expert_role = UserRole.objects.create(slug="expert", name="متخصص")
        profession = ExpertProfession.objects.create(slug="psychologist", name="روان‌شناس")
        user = get_user_model().objects.create_user(
            phone_number="09120000003",
            role=expert_role,
            expert_profession=profession,
            is_expert_verified=True,
            has_all_expert_access=True,
        )
        self.agent.is_public = True
        self.agent.eligible_expert_professions = ["lawyer"]
        self.agent.save(update_fields=["is_public", "eligible_expert_professions", "updated_at"])
        cache.clear()

        allowed, reason = access_service.check_permission(user, self.agent.slug)

        self.assertTrue(allowed)
        self.assertEqual(reason, "Account access override")

        request = APIRequestFactory().get("/api/services/")
        force_authenticate(request, user=user)
        response = ServiceListView.as_view()(request)
        service = next(item for item in response.data if item["slug"] == self.agent.slug)
        self.assertTrue(service["is_owned"])
        self.assertEqual(service["access_status"], "OWNED")

    def test_psychology_student_gets_supervisor_without_plan(self):
        expert_role = UserRole.objects.create(slug="expert", name="متخصص")
        profession = ExpertProfession.objects.create(
            slug="psychology_student",
            name="دانشجوی روان‌شناسی",
        )
        user = get_user_model().objects.create_user(
            phone_number="09120000004",
            role=expert_role,
            expert_profession=profession,
            is_expert_verified=True,
        )
        supervisor = AgentService.objects.create(
            name="Supervisor",
            slug="supervisor-mashaghel",
            system_prompt="Test",
            audience=AgentService.Audience.EXPERT,
            eligible_expert_professions=["psychologist", "psychology_student"],
            is_free=False,
            is_active=True,
            is_public=True,
        )
        cache.clear()

        allowed, reason = access_service.check_permission(user, supervisor.slug)

        self.assertTrue(allowed)
        self.assertEqual(reason, "Account access override")
