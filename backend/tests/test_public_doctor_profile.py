from django.test import TestCase
from rest_framework.test import APIClient

from users.models import CustomUser, ExpertProfession, UserRole
from vania_core.models import DoctorProfile, Location


class PublicDoctorProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.visitor_role = UserRole.objects.create(slug="visitor", name="مراجع")
        self.expert_role = UserRole.objects.create(slug="expert", name="متخصص")
        self.viewer = CustomUser.objects.create_user(
            phone_number="09120001000",
            full_name="مراجع",
            role=self.visitor_role,
        )
        self.expert = CustomUser.objects.create_user(
            phone_number="09120001001",
            full_name="متخصص کامل",
            role=self.expert_role,
            is_expert_verified=True,
        )
        self.location = Location.objects.create(name="تهران")

    def _directory_items(self, response):
        data = response.data
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data

    def test_directory_hides_public_expert_until_required_profile_fields_are_complete(self):
        incomplete_expert = CustomUser.objects.create_user(
            phone_number="09120001002",
            full_name="متخصص ناقص",
            role=self.expert_role,
            is_expert_verified=True,
        )
        incomplete_profile = DoctorProfile.objects.create(
            user=incomplete_expert,
            is_public=True,
            specialty="روانشناس بالینی",
        )
        complete_profile = DoctorProfile.objects.create(
            user=self.expert,
            is_public=True,
            specialty="روانشناس بالینی",
            location=self.location,
            clinic_address="تهران، خیابان ولیعصر، پلاک ۱۰",
            bio="من در حوزه سلامت روان بزرگسالان فعالیت می‌کنم.",
        )

        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/vania/experts/")

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in self._directory_items(response)}
        self.assertIn(complete_profile.id, ids)
        self.assertNotIn(incomplete_profile.id, ids)

    def test_expert_cannot_enable_public_listing_with_incomplete_profile(self):
        DoctorProfile.objects.create(user=self.expert, is_public=False)

        self.client.force_authenticate(self.expert)
        response = self.client.patch(
            "/api/vania/my-profile/",
            {
                "is_public": "True",
                "specialty": "روانشناس بالینی",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_public", response.data)

    def test_incomplete_public_expert_cannot_keep_public_listing_on_profile_update(self):
        DoctorProfile.objects.create(
            user=self.expert,
            is_public=True,
            specialty="روانشناس بالینی",
        )

        self.client.force_authenticate(self.expert)
        response = self.client.patch(
            "/api/vania/my-profile/",
            {"accepting_new_patients": "False"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_public", response.data)

    def test_expert_can_enable_public_listing_after_required_profile_fields_are_complete(self):
        DoctorProfile.objects.create(user=self.expert, is_public=False)

        self.client.force_authenticate(self.expert)
        response = self.client.patch(
            "/api/vania/my-profile/",
            {
                "is_public": "True",
                "specialty": "روانشناس بالینی",
                "location_id": str(self.location.id),
                "clinic_address": "تهران، خیابان ولیعصر، پلاک ۱۰",
                "bio": "من در حوزه سلامت روان بزرگسالان فعالیت می‌کنم.",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.expert.doctor_profile.refresh_from_db()
        self.assertTrue(self.expert.doctor_profile.is_public)

    def test_directory_paginates_all_complete_profiles_without_duplicates(self):
        psychologist = ExpertProfession.objects.create(slug="psychologist", name="روانشناس")
        lawyer = ExpertProfession.objects.create(slug="lawyer", name="وکیل")
        for index in range(31):
            user = CustomUser.objects.create_user(
                phone_number=f"0912010{index:04d}",
                full_name=f"متخصص {index:02d}",
                role=self.expert_role,
                is_expert_verified=True,
                expert_profession=psychologist if index % 2 == 0 else lawyer,
            )
            DoctorProfile.objects.create(
                user=user, is_public=True, specialty="روانشناس بالینی",
                location=self.location, clinic_address="تهران، خیابان ولیعصر",
                bio="من در حوزه سلامت روان بزرگسالان فعالیت می‌کنم.",
            )
        # Hidden and incomplete profiles must not affect the count or consume a slot.
        DoctorProfile.objects.create(user=self.expert, is_public=True, specialty="ناقص")
        hidden = CustomUser.objects.create_user(
            phone_number="09120109999", full_name="متخصص پنهان", role=self.expert_role,
        )
        DoctorProfile.objects.create(
            user=hidden, is_public=False, specialty="روانشناس بالینی",
            location=self.location, clinic_address="تهران، خیابان ولیعصر",
            bio="من در حوزه سلامت روان بزرگسالان فعالیت می‌کنم.",
        )
        self.client.force_authenticate(self.viewer)
        first = self.client.get("/api/vania/experts/")
        second = self.client.get("/api/vania/experts/", {"page": 2})
        alias = self.client.get("/api/vania/doctors/", {"page": 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["count"], 31)
        self.assertEqual(len(first.data["results"]), 24)
        self.assertEqual(len(second.data["results"]), 7)
        self.assertIsNotNone(first.data["next"])
        self.assertIsNone(first.data["previous"])
        self.assertIsNone(second.data["next"])
        self.assertIsNotNone(second.data["previous"])
        first_ids = {item["id"] for item in first.data["results"]}
        second_ids = {item["id"] for item in second.data["results"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 31)
        self.assertEqual(alias.data["results"], second.data["results"])
        filtered = self.client.get("/api/vania/experts/", {"search": "متخصص 30", "locations": self.location.id})
        self.assertEqual(filtered.data["count"], 1)
        self.assertEqual(len(filtered.data["results"]), 1)
        self.assertIsNone(filtered.data["next"])
        profession_filtered = self.client.get("/api/vania/experts/", {"profession": "psychologist"})
        self.assertEqual(profession_filtered.data["count"], 16)
        self.assertTrue(all(item["expert_profession_slug"] == "psychologist" for item in profession_filtered.data["results"]))

    def test_directory_requires_authentication_and_rejects_missing_pages(self):
        self.assertEqual(self.client.get("/api/vania/experts/").status_code, 401)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get("/api/vania/experts/", {"page": 999}).status_code, 404)
