from types import SimpleNamespace

from django.test import TestCase

from users.eligibility import (
    has_agent_access_override,
    is_user_eligible_for_agent,
    is_user_eligible_for_plan,
)


class UserEligibilityTests(TestCase):
    def _user(
        self,
        role_slug: str,
        *,
        verified: bool = False,
        profession_slug: str | None = None,
        has_all_expert_access: bool = False,
    ):
        return SimpleNamespace(
            role=SimpleNamespace(slug=role_slug),
            is_expert_verified=verified,
            expert_profession=SimpleNamespace(slug=profession_slug) if profession_slug else None,
            is_staff=False,
            is_superuser=False,
            has_all_expert_access=has_all_expert_access,
        )

    def _agent(self, audience: str, *, professions: list[str] | None = None, slug: str = "agent"):
        return SimpleNamespace(
            audience=audience,
            eligible_expert_professions=professions or [],
            slug=slug,
        )

    def _plan(self, audience: str, *, professions: list[str] | None = None):
        return SimpleNamespace(
            audience=audience,
            eligible_expert_professions=professions or [],
        )

    def test_expert_user_is_eligible_for_visitor_audience_agent(self):
        user = self._user("expert", verified=True, profession_slug="psychologist")
        agent = self._agent("VISITOR")
        self.assertTrue(is_user_eligible_for_agent(user, agent))

    def test_expert_user_is_not_eligible_for_visitor_audience_plan(self):
        user = self._user("expert", verified=True, profession_slug="psychologist")
        plan = self._plan("VISITOR")
        self.assertFalse(is_user_eligible_for_plan(user, plan))

    def test_verified_expert_user_is_eligible_for_matching_expert_plan(self):
        user = self._user("expert", verified=True, profession_slug="psychologist")
        plan = self._plan("EXPERT", professions=["psychologist"])
        self.assertTrue(is_user_eligible_for_plan(user, plan))

    def test_staff_user_is_eligible_for_any_agent_audience(self):
        user = self._user("visitor")
        user.is_staff = True
        agent = self._agent("EXPERT", professions=["psychologist"])
        self.assertTrue(is_user_eligible_for_agent(user, agent))

    def test_staff_user_is_eligible_for_any_plan_audience(self):
        user = self._user("visitor")
        user.is_staff = True
        plan = self._plan("EXPERT", professions=["psychologist"])
        self.assertTrue(is_user_eligible_for_plan(user, plan))

    def test_all_expert_access_bypasses_expert_profession_filter(self):
        user = self._user(
            "expert",
            verified=True,
            profession_slug="psychologist",
            has_all_expert_access=True,
        )
        agent = self._agent("EXPERT", professions=["lawyer"], slug="expert-lawyer-assistant")

        self.assertTrue(is_user_eligible_for_agent(user, agent))
        self.assertTrue(has_agent_access_override(user, agent))

    def test_all_expert_access_still_requires_verified_expert_role(self):
        user = self._user(
            "visitor",
            verified=False,
            profession_slug="psychologist",
            has_all_expert_access=True,
        )
        agent = self._agent("EXPERT", professions=["lawyer"], slug="expert-lawyer-assistant")

        self.assertFalse(is_user_eligible_for_agent(user, agent))
        self.assertFalse(has_agent_access_override(user, agent))

    def test_psychology_student_only_gets_supervisor_among_expert_agents(self):
        user = self._user("expert", verified=True, profession_slug="psychology_student")
        supervisor = self._agent(
            "EXPERT",
            professions=["psychologist", "psychology_student"],
            slug="supervisor-mashaghel",
        )
        therapy_designer = self._agent(
            "EXPERT",
            professions=["psychologist"],
            slug="tarahi-darman",
        )

        self.assertTrue(is_user_eligible_for_agent(user, supervisor))
        self.assertTrue(has_agent_access_override(user, supervisor))
        self.assertFalse(is_user_eligible_for_agent(user, therapy_designer))

    def test_psychology_student_gets_general_agent_access_override(self):
        user = self._user("expert", verified=True, profession_slug="psychology_student")
        general_agent = self._agent("ALL", slug="ravanyar")

        self.assertTrue(is_user_eligible_for_agent(user, general_agent))
        self.assertTrue(has_agent_access_override(user, general_agent))
