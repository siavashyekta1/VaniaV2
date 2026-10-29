"""Recover a roadmap from an explicit historical revision, with a scoped backup."""
import copy
import hashlib
import json
import os
from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from users.models import CustomUser, UserContextEntry
from users.services import user_context_manager
from vania_core.case_service import build_case_scoped_key
from vania_core.schemas import TherapyRoadmap


def merge_roadmaps(source_data, current_data):
    """Keep the original plan and append newer reports without reusing numbers."""
    restored = TherapyRoadmap.model_validate(copy.deepcopy(source_data))
    current = TherapyRoadmap.model_validate(copy.deepcopy(current_data))
    source_docs = {item.doc_id for item in restored.sessions if item.doc_id}
    source_numbers = {item.session_number for item in restored.sessions}
    if len(source_numbers) != len(restored.sessions):
        raise ValueError("Source roadmap contains duplicate session numbers.")
    if len(source_docs) != sum(bool(item.doc_id) for item in restored.sessions):
        raise ValueError("Source roadmap contains duplicate report links.")
    renumbered = {}
    for session in current.sessions:
        if session.doc_id and session.doc_id in source_docs:
            original = next(item for item in restored.sessions if item.doc_id == session.doc_id)
            if original.model_dump(exclude={"session_number"}) != session.model_dump(exclude={"session_number"}):
                raise ValueError("A linked session changed since the source revision; review it before recovery.")
            continue
        if not session.doc_id:
            raise ValueError("Current roadmap contains an unlinked session; review it before recovery.")
        old_number = session.session_number
        session.session_number = max(source_numbers, default=0) + 1
        source_numbers.add(session.session_number)
        source_docs.add(session.doc_id)
        restored.sessions.append(session)
        if old_number != session.session_number:
            renumbered[session.doc_id] = session.session_number
    # Retain the historical strategy and plan. A reset's empty strategy is not
    # evidence that the clinician intended to discard the original treatment.
    return restored, renumbered


class Command(BaseCommand):
    help = "Preview recovery of a roadmap; --apply requires a verified backup directory."

    def add_arguments(self, parser):
        parser.add_argument("--visitor-id", type=int, required=True)
        parser.add_argument("--doctor-id", type=int, required=True)
        parser.add_argument("--case-id", required=True)
        parser.add_argument("--source-entry-id", type=int, required=True)
        parser.add_argument("--expected-current-entry-id", type=int, required=True)
        parser.add_argument("--backup-dir")
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["apply"] and not options["backup_dir"]:
            raise CommandError("--backup-dir is required with --apply.")
        patient = CustomUser.objects.select_for_update().get(pk=options["visitor_id"])
        key = build_case_scoped_key("therapy_roadmap", options["doctor_id"], options["case_id"])
        entries = UserContextEntry.objects.select_for_update().filter(user=patient, definition__key=key)
        active = list(entries.filter(is_active=True))
        if len(active) != 1 or active[0].id != options["expected_current_entry_id"]:
            raise CommandError("Active roadmap changed or is ambiguous; no data was written.")
        current = active[0]
        source = entries.filter(pk=options["source_entry_id"]).first()
        if not source or source.id == current.id:
            raise CommandError("Source must be a different revision of this exact case roadmap.")
        try:
            restored, renumbered = merge_roadmaps(source.data, current.data)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        reports = {}
        for session in restored.sessions:
            if not session.doc_id:
                if session.status == "COMPLETED":
                    raise CommandError("A completed session has no report; recovery stopped.")
                continue
            report = UserContextEntry.objects.select_for_update().filter(
                pk=session.doc_id, user=patient, definition__key="clinical_session_log", is_active=True,
            ).first()
            if not report or str(report.data.get("doctor_id")) != str(options["doctor_id"]) \
                    or report.data.get("case_id") != options["case_id"]:
                raise CommandError("A report is missing or belongs to another case; recovery stopped.")
            reports[str(report.id)] = report

        # Plan every report edit before taking the backup or making any changes.
        report_changes = {}
        for report_id, number in renumbered.items():
            payload = copy.deepcopy(reports[report_id].data)
            try:
                summary = json.loads(payload["summary"])
                if not isinstance(summary, dict):
                    raise ValueError("Report summary must be an object.")
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandError("Cannot safely renumber a report with an unstructured summary.") from exc
            summary["session_number"] = number
            payload["summary"] = json.dumps(summary, ensure_ascii=False)
            if "session_number" in payload:
                payload["session_number"] = number
            report_changes[report_id] = payload

        plan = {"source_entry_id": source.id, "current_entry_id": current.id,
                "restored_sessions": len(restored.sessions), "renumbered_reports": renumbered}
        self.stdout.write(json.dumps(plan, sort_keys=True))
        if not options["apply"]:
            self.stdout.write("Preview only; no database writes or backup files created.")
            return

        directory = Path(options["backup_dir"]).expanduser()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"roadmap-{patient.id}-{current.id}-{timezone.now():%Y%m%dT%H%M%S%fZ}.json"
        snapshot = {"format": 1, "plan": plan, "visitor_id": patient.id,
                    "doctor_id": options["doctor_id"], "case_id": options["case_id"], "key": key,
                    "entries": json.loads(serializers.serialize("json", [source, current, *reports.values()])),
                    "report_changes": report_changes}
        content = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        digest = hashlib.sha256(content).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CommandError("Backup verification failed; no database changes were made.")
        path.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
        self.stdout.write(f"Verified backup: {path} SHA256={digest}")

        for report_id, payload in report_changes.items():
            report = reports[report_id]
            report.data = payload
            report.save(update_fields=["data"])
        restored.updated_at = timezone.now().isoformat()
        new_entry = user_context_manager.set_singleton_context(
            user=patient, key=key, data=restored.model_dump(mode="json"),
            source=UserContextEntry.SourceType.ADMIN,
        )
        self.stdout.write(self.style.SUCCESS(f"Recovered roadmap entry {new_entry.id}; previous revisions retained."))
