from unittest.mock import Mock, patch

from django.test import TestCase
import requests
from rest_framework.test import APIClient

from users.models import CustomUser, ExpertProfession, UserRole
from vania_core.models import DoctorProfile, RoleVerificationRequest


class UpgradeExpertViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.visitor_role = UserRole.objects.create(slug="visitor", name="مراجع")
        self.user = CustomUser.objects.create_user(
            phone_number="09123456789",
            password="Password123!",
            full_name="علی رضایی",
            role=self.visitor_role,
        )
        self.client.force_authenticate(user=self.user)

        self.psychologist = ExpertProfession.objects.create(
            slug="psychologist",
            name="روان شناس",
            is_active=True,
            validation_kind="mock_psychologist",
        )
        self.psychiatrist = ExpertProfession.objects.create(
            slug="psychiatrist",
            name="روان پزشک",
            is_active=True,
            validation_kind="mock_psychiatrist",
        )
        self.general_doctor = ExpertProfession.objects.create(
            slug="general_doctor",
            name="پزشک",
            is_active=True,
            validation_kind="mock_general_doctor",
            validation_config={"accepted_codes": ["123456"]},
        )
        self.psychology_student = ExpertProfession.objects.create(
            slug="psychology_student",
            name="دانشجوی روان‌شناسی",
            is_active=True,
            validation_kind="manual_psychology_student",
            validation_config={"university_required": True},
        )

    def _payload(self, profession_slug: str, credential_code: str) -> dict:
        return {
            "full_name": "علی رضایی",
            "profession_slug": profession_slug,
            "credential_code": credential_code,
            "national_code": "0084575948",
        }

    @patch("users.expert_validation.validators.requests.get")
    def test_psychologist_upgrade_uses_real_lookup_even_with_old_validation_kind(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text='<div class="card-title">علی رضایی</div>',
        )

        response = self.client.post("/api/auth/upgrade-expert/", self._payload("psychologist", "778899"), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.expert_profession_id, self.psychologist.id)
        self.assertEqual(self.user.expert_verification_meta.get("provider"), "pcoiran")

    @patch("users.expert_validation.validators.requests.get")
    def test_psychologist_provider_failure_falls_back_to_manual_review(self, mock_get):
        mock_get.side_effect = requests.RequestException("timeout")

        response = self.client.post("/api/auth/upgrade-expert/", self._payload("psychologist", "778899"), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_expert_verified)
        self.assertTrue(self.user.expert_verification_meta.get("manual_review"))
        self.assertEqual(self.user.expert_verification_meta.get("fallback_reason"), "connection_error")
        pending_request = RoleVerificationRequest.objects.get(user=self.user)
        self.assertEqual(pending_request.status, RoleVerificationRequest.Status.PENDING)
        self.assertEqual(pending_request.data.get("profession_slug"), "psychologist")

    def test_psychiatrist_upgrade_accepts_manual_review_flow(self):
        response = self.client.post("/api/auth/upgrade-expert/", self._payload("psychiatrist", "MED-445566"), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.assertIn("بررسی دستی", response.data["message"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.expert_profession_id, self.psychiatrist.id)
        self.assertFalse(self.user.is_expert_verified)
        self.assertTrue(self.user.expert_verification_meta.get("manual_review"))
        self.assertTrue(self.user.expert_verification_meta.get("admin_review_recommended"))
        self.assertEqual(self.user.expert_verification_meta.get("status"), "pending")
        pending_request = RoleVerificationRequest.objects.get(user=self.user)
        self.assertEqual(pending_request.status, RoleVerificationRequest.Status.PENDING)
        self.assertEqual(pending_request.data.get("profession_slug"), "psychiatrist")

    def test_new_manual_submission_replaces_previous_pending_request(self):
        first = self.client.post("/api/auth/upgrade-expert/", self._payload("psychiatrist", "MED-111"), format="json")
        self.assertEqual(first.status_code, 200)
        first_request = RoleVerificationRequest.objects.get(user=self.user)

        second = self.client.post("/api/auth/upgrade-expert/", self._payload("psychiatrist", "MED-222"), format="json")
        self.assertEqual(second.status_code, 200)

        requests = list(RoleVerificationRequest.objects.filter(user=self.user).order_by("created_at"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].id, first_request.id)
        self.assertEqual(requests[0].status, RoleVerificationRequest.Status.REJECTED)
        self.assertEqual(requests[1].status, RoleVerificationRequest.Status.PENDING)
        self.assertEqual(requests[1].data.get("credential_code"), "MED-222")

    def test_general_doctor_bypass_code_still_works(self):
        response = self.client.post("/api/auth/upgrade-expert/", self._payload("general_doctor", "123456"), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.expert_profession_id, self.general_doctor.id)
        self.assertEqual(self.user.expert_verification_meta.get("reason"), "test-backdoor")
        self.assertFalse(self.user.expert_verification_meta.get("admin_review_recommended"))

    def test_admin_approval_of_request_syncs_user(self):
        self.client.post("/api/auth/upgrade-expert/", self._payload("psychiatrist", "MED-445566"), format="json")
        verification_request = RoleVerificationRequest.objects.get(user=self.user)

        verification_request.status = RoleVerificationRequest.Status.APPROVED
        verification_request.save()

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_expert_verified)
        self.assertEqual(self.user.role.slug, "expert")
        self.assertEqual(self.user.expert_profession_id, self.psychiatrist.id)
        self.assertEqual(self.user.expert_verification_meta.get("status"), "approved")

    def test_psychology_student_requires_university_name(self):
        response = self.client.post(
            "/api/auth/upgrade-expert/",
            self._payload("psychology_student", "STU-778899"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "university_name is required.")

    def test_psychology_student_submission_waits_for_admin_approval(self):
        payload = {
            **self._payload("psychology_student", "STU-778899"),
            "university_name": "دانشگاه تهران",
        }

        response = self.client.post("/api/auth/upgrade-expert/", payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_expert_verified)
        self.assertEqual(self.user.expert_profession_id, self.psychology_student.id)
        self.assertEqual(
            self.user.expert_verification_meta.get("submitted_university_name"),
            "دانشگاه تهران",
        )
        verification_request = RoleVerificationRequest.objects.get(user=self.user)
        self.assertEqual(verification_request.status, RoleVerificationRequest.Status.PENDING)
        self.assertEqual(verification_request.data["credential_code"], "STU-778899")
        self.assertEqual(verification_request.data["national_code"], "0084575948")
        self.assertEqual(verification_request.data["university_name"], "دانشگاه تهران")

        verification_request.status = RoleVerificationRequest.Status.APPROVED
        verification_request.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_expert_verified)
        self.assertFalse(self.user.is_verified_doctor)
        self.assertEqual(self.user.role.slug, "expert")
        self.assertEqual(self.user.expert_profession.slug, "psychology_student")
        self.assertFalse(DoctorProfile.objects.filter(user=self.user).exists())

    def test_staff_user_can_select_expert_profession_without_verification(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        response = self.client.post(
            "/api/auth/admin-expert-profession/",
            {"profession_slug": "psychiatrist"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.expert_profession_id, self.psychiatrist.id)
        self.assertTrue(self.user.is_expert_verified)
        self.assertTrue(self.user.is_verified_doctor)
        self.assertTrue(self.user.expert_verification_meta.get("admin_bypass"))

    def test_non_staff_user_cannot_select_admin_expert_profession(self):
        response = self.client.post(
            "/api/auth/admin-expert-profession/",
            {"profession_slug": "psychiatrist"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
