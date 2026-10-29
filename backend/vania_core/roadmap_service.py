import logging
from datetime import date, timedelta
from django.utils import timezone
from pydantic import ValidationError
from users.services import user_context_manager
from users.models import UserContextEntry
from .schemas import TherapyRoadmap, TherapyPhase, RoadmapSession
from .context_scope import migrate_legacy_to_scoped_once, migrate_doctor_scoped_to_case_once, build_scoped_key
from .case_service import build_case_scoped_key

logger = logging.getLogger(__name__)


class RoadmapDataError(ValueError):
    """Stored roadmap is invalid; callers must not replace it with an empty plan."""


class RoadmapService:
    CONTEXT_KEY = "therapy_roadmap"

    @staticmethod
    def get_or_create_roadmap(patient, doctor_id=None, case_id=None) -> TherapyRoadmap:
        """
        Retrieves the roadmap from the DB and returns it as a Pydantic model.
        """
        if doctor_id and case_id:
            entry = migrate_doctor_scoped_to_case_once(
                patient=patient,
                doctor_id=doctor_id,
                case_id=case_id,
                base_key=RoadmapService.CONTEXT_KEY,
                default_factory=lambda: TherapyRoadmap().model_dump(),
            )
        elif doctor_id:
            entry = migrate_legacy_to_scoped_once(
                patient=patient,
                doctor_id=doctor_id,
                base_key=RoadmapService.CONTEXT_KEY,
                default_factory=lambda: TherapyRoadmap().model_dump(),
            )
        else:
            entry = user_context_manager.get_context(patient, RoadmapService.CONTEXT_KEY)
        
        # Default empty state
        default_roadmap = TherapyRoadmap()

        if not entry:
            # Create new in DB
            user_context_manager.set_singleton_context(
                user=patient,
                key=build_case_scoped_key(RoadmapService.CONTEXT_KEY, doctor_id, case_id) if doctor_id and case_id else build_scoped_key(RoadmapService.CONTEXT_KEY, doctor_id) if doctor_id else RoadmapService.CONTEXT_KEY,
                data=default_roadmap.model_dump(),
                source=UserContextEntry.SourceType.SYSTEM
            )
            return default_roadmap
        
        try:
            # Parse existing JSON into Pydantic
            return TherapyRoadmap(**entry.data)
        except ValidationError as exc:
            logger.error("Invalid roadmap entry %s for user %s", entry.id, patient.id)
            raise RoadmapDataError(
                f"Roadmap entry {entry.id} is invalid; existing data was preserved."
            ) from exc

    @staticmethod
    def save_roadmap(patient, roadmap_model: TherapyRoadmap, doctor_id=None, case_id=None, creator=None):
        """
        Saves the Pydantic model back to the DB.
        """
        # Revalidate nested/in-place changes too, before archiving any stored data.
        validated_roadmap = TherapyRoadmap.model_validate(roadmap_model.model_dump())
        key = build_case_scoped_key(RoadmapService.CONTEXT_KEY, doctor_id, case_id) if doctor_id and case_id else build_scoped_key(RoadmapService.CONTEXT_KEY, doctor_id) if doctor_id else RoadmapService.CONTEXT_KEY
        # Keep roadmap writes aligned with the singleton context pattern used by other
        # case-scoped services so refreshes always read the latest authoritative record.
        validated_roadmap.updated_at = timezone.now().isoformat()
        if not validated_roadmap.created_at:
            validated_roadmap.created_at = validated_roadmap.updated_at
        roadmap_model.updated_at = validated_roadmap.updated_at
        roadmap_model.created_at = validated_roadmap.created_at
        user_context_manager.set_singleton_context(
            user=patient,
            key=key,
            data=validated_roadmap.model_dump(),
            source=UserContextEntry.SourceType.AGENT if creator else UserContextEntry.SourceType.SYSTEM,
            creator=creator,
        )

    @staticmethod
    def update_strategy(patient, approaches, doctor_id=None, case_id=None):
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        roadmap.treatment_approaches = approaches
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)

    @staticmethod
    def update_phase(patient, phase: TherapyPhase, doctor_id=None, case_id=None):
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        roadmap.current_phase = phase
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)

    @staticmethod
    def add_session(patient, title: str, instructions: str, scheduled_date: str = None, doctor_id=None, case_id=None):
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        
        next_num = len(roadmap.sessions) + 1

        # If date is not provided, schedule with a weekly cadence from today/last session.
        resolved_date = scheduled_date
        if not resolved_date:
            last_session_date = None
            if roadmap.sessions:
                last = roadmap.sessions[-1]
                if last.scheduled_date:
                    try:
                        last_session_date = date.fromisoformat(last.scheduled_date)
                    except ValueError:
                        last_session_date = None

            if last_session_date:
                resolved_date = (last_session_date + timedelta(days=7)).isoformat()
            else:
                resolved_date = date.today().isoformat()

        new_session = RoadmapSession(
            session_number=next_num,
            title=title,
            doctor_instructions=instructions,
            scheduled_date=resolved_date,
            status="READY",
        )
        roadmap.sessions.append(new_session)
        
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)
        
        return new_session # [FIX] Return the object!

    @staticmethod
    def ensure_session(
        patient,
        session_number: int,
        title: str,
        instructions: str = "",
        scheduled_date: str = None,
        status: str = "READY",
        doctor_id=None,
        case_id=None,
    ) -> RoadmapSession:
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        target_session = next((sess for sess in roadmap.sessions if sess.session_number == int(session_number)), None)

        if target_session:
            if title:
                target_session.title = title
            if instructions:
                target_session.doctor_instructions = instructions
            if scheduled_date and not target_session.scheduled_date:
                target_session.scheduled_date = scheduled_date
            if status == "COMPLETED":
                target_session.status = "COMPLETED"
            elif target_session.status != "COMPLETED":
                target_session.status = status
            RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)
            return target_session

        new_session = RoadmapSession(
            session_number=int(session_number),
            title=title or f"جلسه {session_number}",
            doctor_instructions=instructions or None,
            scheduled_date=scheduled_date,
            status=status,
        )
        roadmap.sessions.append(new_session)
        roadmap.sessions.sort(key=lambda sess: sess.session_number)
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)
        return new_session

    @staticmethod
    def set_active_session(patient, session_number: int, doctor_id=None, case_id=None):
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        target_found = False

        for sess in roadmap.sessions:
            if sess.session_number == session_number:
                target_found = True
                # Any non-completed session becomes READY when selected.
                if sess.status != "COMPLETED":
                    sess.status = "READY"
                break

        if not target_found:
            raise ValueError(f"Session {session_number} not found in roadmap.")

        roadmap.active_session_number = session_number
        roadmap.current_phase = TherapyPhase.PHASE_5_EXECUTION
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)

    @staticmethod
    def complete_session(patient, session_number: int, doc_id: str, doctor_id=None, case_id=None):
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        
        for sess in roadmap.sessions:
            if sess.session_number == session_number:
                sess.status = "COMPLETED"
                sess.doc_id = doc_id
        
        # Reset active session if it was this one
        if roadmap.active_session_number == session_number:
            roadmap.active_session_number = None
            
        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)

    @staticmethod
    def delete_session(patient, session_number: int, doctor_id=None, case_id=None) -> bool:
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)

        original_len = len(roadmap.sessions)
        remaining_sessions = [sess for sess in roadmap.sessions if sess.session_number != session_number]
        if len(remaining_sessions) == original_len:
            return False

        for index, sess in enumerate(remaining_sessions, start=1):
            sess.session_number = index

        roadmap.sessions = remaining_sessions
        if roadmap.active_session_number == session_number:
            roadmap.active_session_number = None
        elif roadmap.active_session_number and roadmap.active_session_number > session_number:
            roadmap.active_session_number -= 1

        RoadmapService.save_roadmap(patient, roadmap, doctor_id=doctor_id, case_id=case_id)
        return True
