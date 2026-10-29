from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from vania_core.roadmap_service import RoadmapDataError, RoadmapService
from vania_core.schemas import TherapyRoadmap


class RoadmapIntegrityTests(SimpleTestCase):
    def setUp(self):
        self.patient = SimpleNamespace(id=116)
        self.payload = {
            "sessions": [{"session_number": 1, "title": "Existing session",
                          "status": "COMPLETED", "doc_id": "123"}],
            "treatment_approaches": ["CBT"],
        }

    def test_legacy_text_keeps_existing_session_and_report(self):
        entry = SimpleNamespace(id=10, data={**self.payload, "treatment_approaches": " CBT + ACT "})
        with patch("vania_core.roadmap_service.migrate_doctor_scoped_to_case_once", return_value=entry):
            roadmap = RoadmapService.get_or_create_roadmap(self.patient, 14, "case-a")
        self.assertEqual(roadmap.treatment_approaches, ["CBT + ACT"])
        self.assertEqual(roadmap.sessions[0].doc_id, "123")

    def test_blank_legacy_text_becomes_empty_list(self):
        self.assertEqual(TherapyRoadmap(treatment_approaches="  ").treatment_approaches, [])

    def test_strategy_assignment_normalizes_and_keeps_sessions(self):
        roadmap = TherapyRoadmap(**self.payload)
        roadmap.treatment_approaches = "ACT"
        reloaded = TherapyRoadmap(**roadmap.model_dump())
        self.assertEqual(reloaded.treatment_approaches, ["ACT"])
        self.assertEqual(reloaded.sessions[0].doc_id, "123")

    def test_invalid_assignment_does_not_replace_previous_strategy(self):
        roadmap = TherapyRoadmap(**self.payload)
        with self.assertRaises(ValidationError):
            roadmap.treatment_approaches = {"unexpected": "ACT"}
        self.assertEqual(roadmap.treatment_approaches, ["CBT"])

    def test_invalid_stored_session_raises_instead_of_returning_empty_plan(self):
        entry = SimpleNamespace(id=10, data={"sessions": [{"title": "Missing session number"}]})
        with patch("vania_core.roadmap_service.migrate_doctor_scoped_to_case_once", return_value=entry), \
             patch("vania_core.roadmap_service.user_context_manager.set_singleton_context") as write:
            with self.assertRaises(RoadmapDataError):
                RoadmapService.add_session(self.patient, "New session", "", doctor_id=14, case_id="case-a")
            write.assert_not_called()

    def test_nested_mutation_is_validated_before_archiving_data(self):
        roadmap = TherapyRoadmap(**self.payload)
        roadmap.sessions[0].status = "INVALID_STATUS"
        with patch("vania_core.roadmap_service.user_context_manager.set_singleton_context") as write:
            with self.assertRaises(ValidationError):
                RoadmapService.save_roadmap(self.patient, roadmap, 14, "case-a")
            write.assert_not_called()

    def test_strategy_update_preserves_report_and_scope(self):
        roadmap = TherapyRoadmap(**self.payload)
        with patch.object(RoadmapService, "get_or_create_roadmap", return_value=roadmap), \
             patch("vania_core.roadmap_service.user_context_manager.set_singleton_context") as write:
            RoadmapService.update_strategy(self.patient, "ACT", 14, "case-a")
            saved = write.call_args.kwargs
        self.assertEqual(saved["data"]["sessions"][0]["doc_id"], "123")
        self.assertEqual(saved["data"]["treatment_approaches"], ["ACT"])
        self.assertTrue(saved["key"].startswith("therapy_roadmap__d14__c"))

    def test_invalid_strategy_update_does_not_write(self):
        with patch.object(RoadmapService, "get_or_create_roadmap", return_value=TherapyRoadmap(**self.payload)), \
             patch("vania_core.roadmap_service.user_context_manager.set_singleton_context") as write:
            with self.assertRaises(ValidationError):
                RoadmapService.update_strategy(self.patient, {"invalid": "ACT"}, 14, "case-a")
            write.assert_not_called()
