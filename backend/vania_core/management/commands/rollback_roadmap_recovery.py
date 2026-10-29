import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users.models import CustomUser, UserContextEntry
from vania_core.case_service import build_case_scoped_key
from vania_core.management.commands.recover_roadmap import merge_roadmaps


class Command(BaseCommand):
    help = "Preview or roll back one roadmap recovery, refusing later edits."

    def add_arguments(self, parser):
        parser.add_argument("--backup-file", required=True)
        parser.add_argument("--expected-current-entry-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["backup_file"]).expanduser()
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != path.with_suffix(".sha256").read_text().strip():
            raise CommandError("Backup checksum does not match; no changes made.")
        snapshot = json.loads(content)
        expected_key = build_case_scoped_key("therapy_roadmap", snapshot["doctor_id"], snapshot["case_id"])
        if snapshot.get("format") != 1 or snapshot["key"] != expected_key:
            raise CommandError("Unsupported or inconsistent backup.")
        patient = CustomUser.objects.select_for_update().get(pk=snapshot["visitor_id"])
        active = list(UserContextEntry.objects.select_for_update().filter(
            user=patient, definition__key=expected_key, is_active=True,
        ))
        if len(active) != 1 or active[0].id != options["expected_current_entry_id"]:
            raise CommandError("Active roadmap changed; rollback stopped.")
        records = {item["pk"]: item["fields"] for item in snapshot["entries"]}
        original_id = snapshot["plan"]["current_entry_id"]
        original = UserContextEntry.objects.select_for_update().get(
            pk=original_id, user=patient, definition__key=expected_key, is_active=False,
        )
        if original.data != records[original_id]["data"]:
            raise CommandError("Original revision was edited; rollback stopped.")
        recovered, _ = merge_roadmaps(
            records[snapshot["plan"]["source_entry_id"]]["data"], records[original_id]["data"],
        )
        expected = recovered.model_dump(mode="json")
        actual = dict(active[0].data)
        expected.pop("updated_at", None)
        actual.pop("updated_at", None)
        if actual != expected:
            raise CommandError("Recovered roadmap has later edits; rollback stopped.")
        reports = []
        for report_id, changed_data in snapshot["report_changes"].items():
            report = UserContextEntry.objects.select_for_update().get(
                pk=int(report_id), user=patient, definition__key="clinical_session_log", is_active=True,
            )
            if report.data != changed_data:
                raise CommandError("A report has later edits; rollback stopped.")
            reports.append((report, records[int(report_id)]["data"]))
        self.stdout.write(f"Restore active roadmap {original_id}; restore {len(reports)} report(s).")
        if not options["apply"]:
            self.stdout.write("Preview only; no changes made.")
            return
        for report, data in reports:
            report.data = data
            report.save(update_fields=["data"])
        UserContextEntry.objects.filter(pk=active[0].id).update(is_active=False)
        UserContextEntry.objects.filter(pk=original_id).update(is_active=True)
        self.stdout.write(self.style.SUCCESS("Recovery rolled back; all revisions retained."))
