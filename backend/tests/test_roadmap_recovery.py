import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from users.models import CustomUser, UserContextEntry
from users.services import user_context_manager
from vania_core.case_service import build_case_scoped_key
from vania_core.schemas import TherapyRoadmap


class RoadmapRecoveryTests(TestCase):
    def setUp(self):
        self.patient = CustomUser.objects.create(phone_number="09981230001")
        self.doctor = CustomUser.objects.create(phone_number="09981230002")
        self.case_id = "case-a"
        self.key = build_case_scoped_key("therapy_roadmap", self.doctor.id, self.case_id)
        self.old_report = self.report(1)
        self.latest_report = self.report(1)
        source_data = TherapyRoadmap(sessions=[
            {"session_number": 1, "title": "Original", "status": "COMPLETED", "doc_id": str(self.old_report.id)},
            {"session_number": 2, "title": "Planned", "status": "READY"},
        ]).model_dump(mode="json")
        source_data["treatment_approaches"] = "CBT + ACT"
        self.source = self.store(self.key, source_data)
        self.current = self.store(self.key, TherapyRoadmap(sessions=[
            {"session_number": 1, "title": "New", "status": "COMPLETED", "doc_id": str(self.latest_report.id)},
        ]).model_dump(mode="json"))
        self.options = {"visitor_id": self.patient.id, "doctor_id": self.doctor.id,
                        "case_id": self.case_id, "source_entry_id": self.source.id,
                        "expected_current_entry_id": self.current.id}

    def store(self, key, data):
        return user_context_manager.set_singleton_context(user=self.patient, key=key, data=data, source="SYSTEM")

    def report(self, number):
        return user_context_manager.add_entry(user=self.patient, key="clinical_session_log", data={
            "doctor_id": self.doctor.id, "case_id": self.case_id,
            "summary": json.dumps({"session_number": number, "symptoms_analysis": "Preserve this text"}),
        })

    def run_recovery(self, **kwargs):
        call_command("recover_roadmap", **{**self.options, **kwargs}, stdout=io.StringIO())

    def test_preview_has_no_database_or_file_writes(self):
        before = UserContextEntry.objects.count()
        with TemporaryDirectory() as directory:
            self.run_recovery(backup_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(UserContextEntry.objects.count(), before)
        self.current.refresh_from_db()
        self.assertTrue(self.current.is_active)

    def test_recovery_backs_up_before_merging_and_can_be_rolled_back(self):
        original_report_data = self.latest_report.data.copy()
        with TemporaryDirectory() as directory:
            self.run_recovery(apply=True, backup_dir=directory)
            backup = next(Path(directory).glob("*.json"))
            snapshot = json.loads(backup.read_text())
            self.assertEqual(snapshot["plan"]["current_entry_id"], self.current.id)
            restored = UserContextEntry.objects.get(user=self.patient, definition__key=self.key, is_active=True)
            self.assertEqual([s["session_number"] for s in restored.data["sessions"]], [1, 2, 3])
            self.assertEqual(restored.data["sessions"][1]["status"], "READY")
            self.assertEqual(restored.data["treatment_approaches"], ["CBT + ACT"])
            self.latest_report.refresh_from_db()
            self.assertEqual(json.loads(self.latest_report.data["summary"])["session_number"], 3)
            self.assertEqual(json.loads(self.latest_report.data["summary"])["symptoms_analysis"], "Preserve this text")
            call_command("rollback_roadmap_recovery", backup_file=str(backup),
                         expected_current_entry_id=restored.id, apply=True, stdout=io.StringIO())
            self.current.refresh_from_db()
            self.latest_report.refresh_from_db()
            self.assertTrue(self.current.is_active)
            self.assertEqual(self.latest_report.data, original_report_data)

    def test_stale_current_revision_is_rejected_without_backup(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(CommandError):
                self.run_recovery(expected_current_entry_id=self.source.id, apply=True, backup_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_backup_failure_prevents_all_database_changes(self):
        with TemporaryDirectory() as directory:
            blocked = Path(directory) / "file"
            blocked.write_text("not a directory")
            with self.assertRaises(OSError):
                self.run_recovery(apply=True, backup_dir=str(blocked))
        self.current.refresh_from_db()
        self.latest_report.refresh_from_db()
        self.assertTrue(self.current.is_active)
        self.assertEqual(json.loads(self.latest_report.data["summary"])["session_number"], 1)

    def test_cross_case_report_is_rejected(self):
        self.latest_report.data["case_id"] = "other-case"
        self.latest_report.save()
        with self.assertRaises(CommandError):
            self.run_recovery()

    def test_rollback_refuses_later_report_edits(self):
        with TemporaryDirectory() as directory:
            self.run_recovery(apply=True, backup_dir=directory)
            backup = next(Path(directory).glob("*.json"))
            restored = UserContextEntry.objects.get(user=self.patient, definition__key=self.key, is_active=True)
            self.latest_report.refresh_from_db()
            self.latest_report.data["private_notes"] = "A later edit"
            self.latest_report.save()
            with self.assertRaises(CommandError):
                call_command("rollback_roadmap_recovery", backup_file=str(backup),
                             expected_current_entry_id=restored.id, apply=True, stdout=io.StringIO())
            restored.refresh_from_db()
            self.assertTrue(restored.is_active)

    def test_rollback_refuses_damaged_backup(self):
        with TemporaryDirectory() as directory:
            self.run_recovery(apply=True, backup_dir=directory)
            backup = next(Path(directory).glob("*.json"))
            backup.write_text(backup.read_text() + " ")
            restored = UserContextEntry.objects.get(user=self.patient, definition__key=self.key, is_active=True)
            with self.assertRaises(CommandError):
                call_command("rollback_roadmap_recovery", backup_file=str(backup),
                             expected_current_entry_id=restored.id, apply=True, stdout=io.StringIO())
            restored.refresh_from_db()
            self.assertTrue(restored.is_active)
