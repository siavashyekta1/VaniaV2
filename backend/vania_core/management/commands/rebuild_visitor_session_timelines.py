from django.core.management.base import BaseCommand
from django.db import transaction

from services.models_canvas import CanvasInstance
from users.models import CustomUser
from vania_core.case_service import CaseService
from vania_core.patient_service import PatientDataService


class Command(BaseCommand):
    help = "Rebuild persisted visitor session timelines from canonical case-scoped session data."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--patient-id", type=int)

    def handle(self, *args, **options):
        canvases = CanvasInstance.objects.filter(
            session_id__startswith="visitor-dashboard-",
            canvas_def__component_key="VANIA_PATIENT_JOURNEY",
        ).order_by("session_id")
        if options["patient_id"]:
            canvases = canvases.filter(session_id=f"visitor-dashboard-{options['patient_id']}")

        counters = {"scanned": 0, "eligible": 0, "changed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        for canvas in canvases.iterator():
            counters["scanned"] += 1
            try:
                patient_id = int(canvas.session_id.removeprefix("visitor-dashboard-"))
                patient = CustomUser.objects.filter(pk=patient_id).first()
                if not patient:
                    counters["skipped"] += 1
                    continue
                state = canvas.current_state if isinstance(canvas.current_state, dict) else {}
                case_id = state.get("selected_case_id") or (state.get("selected_case") or {}).get("id")
                doctor_id = state.get("selected_doctor_id") or (state.get("selected_case") or {}).get("doctor_id")
                cases = CaseService.get_accessible_cases_for_patient(patient)
                selected_case = next((item for item in cases if item.get("id") == case_id), None)
                if not selected_case and doctor_id:
                    selected_case = next(
                        (item for item in cases if int(item.get("doctor_id") or 0) == int(doctor_id)),
                        None,
                    )
                if not selected_case:
                    counters["skipped"] += 1
                    continue
                counters["eligible"] += 1
                with transaction.atomic():
                    result = PatientDataService.refresh_patient_session_timeline_canvas(
                        patient,
                        int(selected_case["doctor_id"]),
                        selected_case["id"],
                        save=options["apply"],
                    )
                counters["changed" if result["changed"] else "unchanged"] += 1
            except Exception as exc:
                counters["failed"] += 1
                self.stderr.write(f"Failed canvas {canvas.id}: {exc}")

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(f"{mode} {counters}")
        if counters["failed"]:
            raise RuntimeError(f"Timeline rebuild failed for {counters['failed']} canvas(es).")
