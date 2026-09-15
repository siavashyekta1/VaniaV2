import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from users.models import CustomUser, UserRole
from vania_core.patient_service import PatientDataService
from vania_core.roadmap_service import RoadmapService
from vania_core.session_service import SessionService


class SessionReportSyncTests(TestCase):
    def setUp(self):
        expert_role = UserRole.objects.get(slug="expert")
        visitor_role = UserRole.objects.get(slug="visitor")
        self.expert = CustomUser.objects.create_user(
            phone_number="09120000100",
            full_name="Expert",
            role=expert_role,
        )
        self.visitor = CustomUser.objects.create_user(
            phone_number="09370000100",
            full_name="Visitor",
            role=visitor_role,
        )
        self.case_id = "case-session-sync"

    def report_payload(self, summary):
        return json.dumps(
            {
                "is_structured_report": True,
                "session_number": 1,
                "topic": "Session One",
                "date": "2026-07-26",
                "symptoms_analysis": summary,
            }
        )

    def test_structured_report_updates_linked_entry_instead_of_duplicating(self):
        first = SessionService.save_structured_report(
            patient=self.visitor,
            doctor=self.expert,
            summary=self.report_payload("first"),
            private_notes="private one",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        updated = SessionService.save_structured_report(
            patient=self.visitor,
            doctor=self.expert,
            summary=self.report_payload("updated"),
            private_notes="private two",
            doctor_id=self.expert.id,
            case_id=self.case_id,
            entry_id=first.id,
        )

        self.assertEqual(updated.id, first.id)
        self.assertEqual(
            self.visitor.context_entries.filter(definition__key=SessionService.CONTEXT_KEY, is_active=True).count(),
            1,
        )
        self.assertEqual(SessionService.get_patient_history(self.visitor, case_id=self.case_id)[0]["summary"], "updated")

    def test_history_hides_older_duplicate_for_same_scoped_session(self):
        SessionService.log_session(
            self.visitor,
            self.expert,
            self.report_payload("older"),
            "",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        newest = SessionService.log_session(
            self.visitor,
            self.expert,
            self.report_payload("newest"),
            "",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )

        history = SessionService.get_patient_history(
            self.visitor,
            viewer_role="PATIENT",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], newest.id)
        self.assertEqual(history[0]["summary"], "newest")
        self.assertNotIn("private_notes", history[0])

    def test_history_prefers_roadmap_links_and_repairs_stored_session_number(self):
        linked_session_three = SessionService.log_session(
            self.visitor,
            self.expert,
            self.report_payload("linked three").replace('"session_number": 1', '"session_number": 4'),
            "",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        linked_session_four = SessionService.log_session(
            self.visitor,
            self.expert,
            self.report_payload("linked four").replace('"session_number": 1', '"session_number": 4'),
            "",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        SessionService.log_session(
            self.visitor,
            self.expert,
            self.report_payload("unlinked duplicate").replace('"session_number": 1', '"session_number": 4'),
            "",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        RoadmapService.ensure_session(
            self.visitor,
            3,
            "Session Three",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        RoadmapService.complete_session(
            self.visitor,
            3,
            str(linked_session_three.id),
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        RoadmapService.ensure_session(
            self.visitor,
            4,
            "Session Four",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )
        RoadmapService.complete_session(
            self.visitor,
            4,
            str(linked_session_four.id),
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )

        history = SessionService.get_patient_history(
            self.visitor,
            viewer_role="PATIENT",
            doctor_id=self.expert.id,
            case_id=self.case_id,
        )

        self.assertEqual({item["session_number"] for item in history}, {3, 4})
        self.assertEqual({item["id"] for item in history}, {linked_session_three.id, linked_session_four.id})

    @patch.object(PatientDataService, "get_patient_dashboard_snapshot")
    @patch("services.models_canvas.CanvasInstance.objects.filter")
    def test_refresh_patient_dashboard_replaces_stale_snapshot(self, filter_mock, snapshot_mock):
        canvas = SimpleNamespace(current_state={"timeline": [{"id": "stale"}]}, save=Mock())
        filter_mock.return_value.first.return_value = canvas
        snapshot_mock.return_value = {
            "timeline": [{"id": "fresh"}],
            "selected_case_id": self.case_id,
        }

        refreshed = PatientDataService.refresh_patient_dashboard_canvas(
            self.visitor,
            self.expert.id,
            self.case_id,
        )

        self.assertTrue(refreshed)
        self.assertEqual(canvas.current_state["timeline"], [{"id": "fresh"}])
        self.assertEqual(canvas.current_state["selected_case_id"], self.case_id)
        canvas.save.assert_called_once_with(update_fields=["current_state", "last_modified_at"])

    @patch.object(SessionService, "get_patient_history")
    @patch("services.models_canvas.CanvasInstance.objects.filter")
    def test_timeline_refresh_changes_only_session_fields(self, filter_mock, history_mock):
        canvas = SimpleNamespace(
            current_state={
                "timeline": [{"id": "stale"}],
                "selected_case": {"id": self.case_id, "timeline": [{"id": "stale"}], "tasks": ["keep"]},
                "unrelated": {"keep": True},
            },
            save=Mock(),
        )
        filter_mock.return_value.first.return_value = canvas
        history_mock.return_value = [{"id": "fresh", "smart_goals": ["goal"]}]

        result = PatientDataService.refresh_patient_session_timeline_canvas(
            self.visitor,
            self.expert.id,
            self.case_id,
        )

        self.assertTrue(result["changed"])
        self.assertEqual(canvas.current_state["timeline"], history_mock.return_value)
        self.assertEqual(canvas.current_state["selected_case"]["timeline"], history_mock.return_value)
        self.assertEqual(canvas.current_state["selected_case"]["tasks"], ["keep"])
        self.assertEqual(canvas.current_state["unrelated"], {"keep": True})
        canvas.save.assert_called_once_with(update_fields=["current_state", "last_modified_at"])

    @patch.object(SessionService, "get_patient_history")
    @patch("services.models_canvas.CanvasInstance.objects.filter")
    def test_timeline_refresh_dry_run_does_not_save(self, filter_mock, history_mock):
        canvas = SimpleNamespace(current_state={"timeline": [{"id": "stale"}]}, save=Mock())
        filter_mock.return_value.first.return_value = canvas
        history_mock.return_value = [{"id": "fresh"}]

        result = PatientDataService.refresh_patient_session_timeline_canvas(
            self.visitor,
            self.expert.id,
            self.case_id,
            save=False,
        )

        self.assertTrue(result["changed"])
        canvas.save.assert_not_called()
