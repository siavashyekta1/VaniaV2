# backend/vania_core/session_service.py
import logging
import json
from django.utils import timezone
from users.services import user_context_manager
from users.models import UserContextEntry

logger = logging.getLogger(__name__)

class SessionService:
    """
    Encapsulates the business logic for managing Clinical Session Logs (SOAP Notes).
    It acts as a specialized adapter for the generic UserContextEntry system.
    """
    CONTEXT_KEY = "clinical_session_log"

    @staticmethod
    def _parse_history_payload(entry) -> dict:
        payload = entry.data.copy() if isinstance(entry.data, dict) else {}
        raw_summary = payload.get("summary", "")
        if isinstance(raw_summary, str) and raw_summary.strip().startswith("{"):
            try:
                parsed_summary = json.loads(raw_summary)
                if isinstance(parsed_summary, dict):
                    payload["summary"] = (
                        parsed_summary.get("symptoms_analysis")
                        or parsed_summary.get("summary")
                        or ""
                    )
                    payload["flashcards"] = parsed_summary.get("flashcards") or payload.get("flashcards") or []
                    payload["swot_analysis"] = parsed_summary.get("swot_analysis") or payload.get("swot_analysis") or {}
                    payload["smart_goals"] = parsed_summary.get("smart_goals") or payload.get("smart_goals") or []
                    payload["session_number"] = parsed_summary.get("session_number") or payload.get("session_number")
                    payload["title"] = parsed_summary.get("topic") or payload.get("title")
                    payload["date"] = parsed_summary.get("date") or payload.get("date")
            except Exception:
                logger.warning("Failed to parse structured session summary for entry %s", entry.id)
        return payload

    @staticmethod
    def _get_linked_roadmap_sessions(patient, doctor_id: int, case_id: str, entries: list) -> dict:
        if not doctor_id or not case_id:
            return {}

        from .case_service import build_case_scoped_key
        from .roadmap_service import RoadmapService
        from .schemas import TherapyRoadmap

        roadmap_entry = user_context_manager.get_context(
            patient,
            build_case_scoped_key(RoadmapService.CONTEXT_KEY, doctor_id, case_id),
        )
        if not roadmap_entry or not isinstance(roadmap_entry.data, dict):
            return {}

        try:
            roadmap = TherapyRoadmap(**roadmap_entry.data)
        except Exception:
            logger.warning("Failed to parse roadmap while resolving session history for patient %s", patient.id)
            return {}

        entries_by_id = {entry.id: entry for entry in entries}
        linked_sessions = {}
        for roadmap_session in roadmap.sessions:
            if not roadmap_session.doc_id:
                continue
            try:
                entry_id = int(roadmap_session.doc_id)
            except (TypeError, ValueError):
                continue
            entry = entries_by_id.get(entry_id)
            if not entry:
                entry = UserContextEntry.objects.filter(
                    pk=entry_id,
                    user=patient,
                    definition__key=SessionService.CONTEXT_KEY,
                    is_active=True,
                ).first()
                if entry:
                    entries.append(entry)
                    entries_by_id[entry.id] = entry
            if not entry:
                continue
            data = entry.data if isinstance(entry.data, dict) else {}
            if int(data.get("doctor_id") or 0) != int(doctor_id) or data.get("case_id") != case_id:
                continue
            linked_sessions[entry.id] = roadmap_session
        return linked_sessions

    @staticmethod
    def log_session(
        patient, 
        doctor, 
        summary: str, 
        private_notes: str, 
        mood_rating: int = None,
        doctor_id: int = None,
        case_id: str = None,
    ) -> UserContextEntry:
        """
        Saves a session report into the patient's context history as an immutable log entry.

        Args:
            patient: The CustomUser object for the patient.
            doctor: The CustomUser object for the doctor authoring the note.
            summary: Public notes visible to both doctor and patient.
            private_notes: Confidential notes visible only to the doctor.
            mood_rating: Optional 1-10 rating of the patient's mood.

        Returns:
            The newly created UserContextEntry object.
        """
        data = {
            "doctor_id": doctor_id or doctor.id,
            "doctor_name": doctor.full_name or "Doctor",
            "date": timezone.now().isoformat(),
            "summary": summary,
            "private_notes": private_notes,
            "mood_rating": mood_rating,
            "case_id": case_id,
        }
        
        # Uses the generic user_context_manager to append this as a new "fact"
        return user_context_manager.add_entry(
            user=patient,
            key=SessionService.CONTEXT_KEY,
            data=data,
            source=UserContextEntry.SourceType.AGENT, 
            creator=doctor
        )

    @staticmethod
    def save_structured_report(
        patient,
        doctor,
        summary: str,
        private_notes: str,
        doctor_id: int,
        case_id: str,
        entry_id: int = None,
    ) -> UserContextEntry:
        entry = None
        if entry_id:
            entry = UserContextEntry.objects.filter(
                pk=entry_id,
                user=patient,
                definition__key=SessionService.CONTEXT_KEY,
                is_active=True,
            ).first()

        if not entry:
            return SessionService.log_session(
                patient=patient,
                doctor=doctor,
                summary=summary,
                private_notes=private_notes,
                doctor_id=doctor_id,
                case_id=case_id,
            )

        payload = dict(entry.data) if isinstance(entry.data, dict) else {}
        payload.update({
            "doctor_id": doctor_id,
            "doctor_name": doctor.full_name or "Doctor",
            "date": timezone.now().isoformat(),
            "summary": summary,
            "private_notes": private_notes,
            "case_id": case_id,
        })
        entry.data = payload
        entry.created_by = doctor
        entry.save(update_fields=["data", "created_by"])
        return entry

    @staticmethod
    def get_patient_history(patient, viewer_role: str = 'DOCTOR', doctor_id: int = None, case_id: str = None) -> list:
        """
        Retrieves a patient's session history with Role-Based redaction.

        Args:
            patient: The CustomUser object for the patient.
            viewer_role: The role of the person viewing the data ('DOCTOR' or 'PATIENT').

        Returns:
            A list of session dictionaries, sanitized based on viewer role.
        """
        # Fetches all entries for this key from the generic context service
        entries = user_context_manager.get_history(patient, SessionService.CONTEXT_KEY, limit=100)
        entries = list(entries)
        linked_sessions = SessionService._get_linked_roadmap_sessions(
            patient,
            doctor_id,
            case_id,
            entries,
        )
        linked_session_numbers = {
            str(session.session_number)
            for session in linked_sessions.values()
        }
        history = []
        seen_structured_sessions = set()
        
        for entry in entries:
            # Skip soft-deleted entries
            if not entry.is_active:
                continue
            
            payload = SessionService._parse_history_payload(entry)
            if doctor_id and int(payload.get("doctor_id") or 0) != int(doctor_id):
                continue
            if case_id and payload.get("case_id") != case_id:
                continue

            linked_session = linked_sessions.get(entry.id)
            if linked_session:
                payload["session_number"] = linked_session.session_number
                payload["title"] = payload.get("title") or linked_session.title
                payload["date"] = payload.get("date") or linked_session.scheduled_date

            session_number = payload.get("session_number")
            if session_number is not None:
                if not linked_session and str(session_number) in linked_session_numbers:
                    continue
                session_key = (
                    str(payload.get("doctor_id") or ""),
                    payload.get("case_id"),
                    str(session_number),
                )
                if session_key in seen_structured_sessions:
                    continue
                seen_structured_sessions.add(session_key)
            
            # [CRITICAL] Inject the DB ID so the frontend can reference this specific log
            payload['id'] = entry.id 
            
            # [PRIVACY FILTER] Patients cannot see the private clinical notes field.
            if viewer_role == 'PATIENT':
                payload.pop('private_notes', None)
            
            history.append(payload)
            
        return history

    @staticmethod
    def update_session(entry_id: int, user, summary: str, private_notes: str, date: str) -> bool:
        """Updates the content of an existing session log."""
        try:
            entry = UserContextEntry.objects.get(pk=entry_id, definition__key=SessionService.CONTEXT_KEY)
            # Security: Only the creator (doctor) or an admin can edit.
            if entry.created_by != user and not user.is_staff:
                return False
            
            entry.data['summary'] = summary
            entry.data['private_notes'] = private_notes
            entry.data['date'] = date
            entry.save()
            return True
        except UserContextEntry.DoesNotExist:
            return False

    @staticmethod
    def delete_session(entry_id: int, user) -> bool:
        """Soft-deletes a session log."""
        try:
            entry = UserContextEntry.objects.get(pk=entry_id, definition__key=SessionService.CONTEXT_KEY)
            if entry.created_by != user and not user.is_staff:
                return False
            
            entry.is_active = False # Soft delete
            entry.save()
            return True
        except UserContextEntry.DoesNotExist:
            return False
