import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from agno.run import RunContext
from agno.tools import tool
from agno.tools.function import ToolResult

from agents.context import resource_context, selected_case_context
from capabilities.test_attachment_media import (
    build_case_file_tool_result,
    build_test_attachment_tool_result,
    case_file_is_loadable,
    test_has_loadable_attachments,
)
from capabilities.base import BaseCapability
from canvas.events import CanvasUpdateEvent
from canvas.manager import canvas_manager
from services.models_canvas import CanvasInstance
from users.models import ContextDefinition, CustomUser, UserContextEntry
from users.services import user_context_manager
from vania_core.case_service import CaseService
from vania_core.models import TreatmentConnection
from vania_core.flashcards import normalize_flashcards
from vania_core.medication_service import MedicationService
from vania_core.profession_policy import (
    build_canvas_policy_payload,
    filter_form_definitions,
    filter_tests_catalog,
    get_policy_for_user,
    is_tool_family_allowed,
    resolve_allowed_form_keys,
    sanitize_expert_case_payload,
)
from vania_core.profile_snapshots import get_expert_profile_payload, get_visitor_base_profile_payload
from vania_core.patient_service import PatientDataService
from vania_core.schemas import TherapyPhase
from vania_core.services import AppendixService, ProfileService, RoadmapService, SessionService, TaskService
from vania_core.case_files_service import CaseFilesService
from vania_core.appendix_service import normalize_resource_type
from vania_core.task_service import normalize_rescue_dimension
from vania_core.tests_service import ClinicalTestsService
from vania_core.tests_catalog import TEST_CATALOG
from vania_core.models import EsanjTestAccessRule
from vania_core.esanj_serializers import user_can_access_esanj_rule

from .forms import ALL_FORMS_LIST

logger = logging.getLogger(__name__)

TOOL_FAMILY_BY_NAME = {
    "get_my_expert_profile": "profiles",
    "get_active_visitor_profile": "profiles",
    "list_accessible_visitors": "profiles",
    "select_visitor": "profiles",
    "list_accessible_cases": "case_management",
    "get_case_snapshot": "case_management",
    "create_case": "case_management",
    "rename_case": "case_management",
    "delete_case": "case_management",
    "select_case": "case_management",
    "update_clinical_summary": "clinical_summary",
    "manage_roadmap": "roadmap",
    "finalize_session_report": "roadmap",
    "add_rescue_task": "rescue_net",
    "manage_rescue_task": "rescue_net",
    "prescribe_resource": "appendix",
    "manage_medications": "medications",
    "get_current_medications": "medications",
    "get_form_schema": "forms",
    "submit_clinical_form": "forms",
    "manage_clinical_tests": "tests",
    "get_test_result_details": "tests",
    "get_test_attachment_details": "tests",
    "list_case_files": "files",
    "search_case_files": "files",
    "read_case_file": "files",
    "get_case_file_details": "files",
    "update_forms_tests_analysis": "analysis",
}


def _resolve_tool_name(tool_obj: Any) -> str:
    if hasattr(tool_obj, "name") and tool_obj.name:
        return str(tool_obj.name)
    entrypoint = getattr(tool_obj, "entrypoint", None)
    if entrypoint and hasattr(entrypoint, "__name__"):
        return str(entrypoint.__name__)
    if hasattr(tool_obj, "__name__"):
        return str(tool_obj.__name__)
    return ""


def _flatten_form_fields(schema: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    for field in schema or []:
        if field.get("type") == "section":
            flattened.extend(_flatten_form_fields(field.get("fields", [])))
            continue
        flattened.append(field)
    return flattened


def _normalize_medication_action(action: str) -> str:
    action_key = (action or "").strip().upper()
    aliases = {
        "ADD_MEDICATION": "ADD",
        "CREATE_MEDICATION": "ADD",
        "UPDATE_MEDICATION": "UPDATE",
        "EDIT_MEDICATION": "UPDATE",
        "DELETE_MEDICATION": "DELETE",
        "REMOVE_MEDICATION": "DELETE",
        "REPLACE_PLAN": "REPLACE",
        "SET_PLAN": "REPLACE",
        "GET_MEDICATIONS": "SNAPSHOT",
        "LIST_MEDICATIONS": "SNAPSHOT",
    }
    return aliases.get(action_key, action_key)


def _append_medication_text(base: Optional[str], extra_parts: List[str]) -> str:
    clean_parts = [part.strip() for part in extra_parts if isinstance(part, str) and part.strip()]
    if not clean_parts:
        return (base or "").strip()
    if base and base.strip():
        return "\n".join([base.strip(), *clean_parts])
    return "\n".join(clean_parts)


def _normalize_medication_payload(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    payload = data or {}
    timing = payload.get("timing") or payload.get("frequency") or ""
    duration = payload.get("duration") or ""
    if not duration:
        start_date = (payload.get("start_date") or "").strip()
        end_date = (payload.get("end_date") or "").strip()
        if start_date and end_date:
            duration = f"از {start_date} تا {end_date}"
        elif start_date:
            duration = f"شروع از {start_date}"
        elif end_date:
            duration = f"تا {end_date}"

    usage_instructions = _append_medication_text(
        payload.get("usage_instructions") or payload.get("instructions") or "",
        [f"روش مصرف: {payload.get('route')}" if payload.get("route") else ""],
    )
    notes = _append_medication_text(
        payload.get("notes") or "",
        [
            f"اندیکاسیون: {payload.get('indication')}" if payload.get("indication") else "",
            f"عوارض/هشدار: {payload.get('side_effects')}" if payload.get("side_effects") else "",
            f"وضعیت: {payload.get('status')}" if payload.get("status") else "",
        ],
    )
    return {
        "drug_name": (payload.get("drug_name") or payload.get("name") or "").strip(),
        "dosage": (payload.get("dosage") or "").strip(),
        "usage_instructions": usage_instructions,
        "timing": timing.strip(),
        "duration": duration.strip(),
        "notes": notes,
    }


async def _get_active_patient() -> Optional[CustomUser]:
    patient_id = resource_context.get()
    if not patient_id:
        return None
    try:
        return await CustomUser.objects.aget(pk=patient_id)
    except CustomUser.DoesNotExist:
        return None


async def _get_active_doctor(run_context: RunContext) -> CustomUser:
    return await CustomUser.objects.select_related("expert_profession", "role").aget(pk=run_context.user_id)


async def _get_active_case(patient: CustomUser, doctor: CustomUser, requested_case_id: Optional[str] = None) -> Dict[str, Any]:
    requested_case_id = requested_case_id or selected_case_context.get()
    return await sync_to_async(CaseService.get_or_create_selected_case_for_expert)(patient, doctor, requested_case_id)


async def _ensure_case_editable(patient: CustomUser, doctor: CustomUser, case_id: str) -> Optional[str]:
    can_edit = await sync_to_async(CaseService.expert_can_edit_case)(patient, doctor, case_id)
    if can_edit:
        return None
    return "❌ This case is read-only for you."


async def _get_canvas_id(session_id: str, component_key: str) -> Optional[str]:
    try:
        instance = await CanvasInstance.objects.filter(
            session_id=session_id,
            canvas_def__component_key=component_key
        ).afirst()
        return str(instance.id) if instance else None
    except Exception as exc:
        logger.error("Failed to resolve canvas id: %s", exc)
        return None


def _extract_active_goals_from_history(history: List[Dict[str, Any]]) -> List[str]:
    for item in history:
        goals = item.get("smart_goals")
        if isinstance(goals, list) and goals:
            return [str(goal).strip() for goal in goals if str(goal).strip()]
        raw_summary = item.get("summary", "")
        if isinstance(raw_summary, str) and raw_summary.strip().startswith("{"):
            try:
                parsed = json.loads(raw_summary)
                goals = parsed.get("smart_goals") if isinstance(parsed, dict) else None
                if goals:
                    return goals
            except Exception:
                continue
    return []


def _normalize_swot(raw_swot: Any) -> Dict[str, List[str]]:
    normalized = {
        "Strengths": [],
        "Weaknesses": [],
        "Opportunities": [],
        "Threats": [],
    }
    if not isinstance(raw_swot, dict):
        return normalized

    key_aliases = {
        "Strengths": ("Strengths", "strengths"),
        "Weaknesses": ("Weaknesses", "weaknesses"),
        "Opportunities": ("Opportunities", "opportunities"),
        "Threats": ("Threats", "threats"),
    }
    for normalized_key, aliases in key_aliases.items():
        value = next((raw_swot.get(alias) for alias in aliases if alias in raw_swot), [])
        if isinstance(value, list):
            normalized[normalized_key] = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            normalized[normalized_key] = [line.strip() for line in value.splitlines() if line.strip()]
    return normalized


def _compact_case_item(case_item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": case_item.get("id"),
        "title": case_item.get("title"),
        "can_edit": bool(case_item.get("can_edit")),
        "is_read_only": bool(case_item.get("is_read_only")),
        "access_mode": case_item.get("access_mode"),
        "doctor_id": case_item.get("doctor_id"),
        "doctor_name": case_item.get("doctor_name"),
        "doctor_profession_slug": case_item.get("doctor_profession_slug"),
        "updated_at": case_item.get("updated_at"),
        "created_at": case_item.get("created_at"),
    }


def _compact_visitor_item(visitor: CustomUser, doctor: CustomUser) -> Dict[str, Any]:
    accessible_cases = CaseService.get_accessible_cases_for_expert(visitor, doctor)
    return {
        "id": int(visitor.id),
        "name": visitor.full_name or visitor.phone_number or "مراجع",
        "phone_number": visitor.phone_number,
        "case_count": len(accessible_cases),
        "latest_case_id": accessible_cases[0].get("id") if accessible_cases else None,
        "latest_case_title": accessible_cases[0].get("title") if accessible_cases else None,
    }


def _compact_accessible_visitors(doctor: CustomUser) -> List[Dict[str, Any]]:
    connections = list(
        TreatmentConnection.objects.filter(
            doctor=doctor,
            status=TreatmentConnection.Status.ACTIVE,
        ).select_related("patient")
    )
    return [_compact_visitor_item(conn.patient, doctor) for conn in connections]


def _compact_roadmap_payload(roadmap: Any) -> Dict[str, Any]:
    roadmap_dict = roadmap.model_dump() if hasattr(roadmap, "model_dump") else dict(roadmap or {})
    sessions = roadmap_dict.get("sessions", []) or []
    return {
        "current_phase": roadmap_dict.get("current_phase"),
        "treatment_approaches": roadmap_dict.get("treatment_approaches", []),
        "active_session_number": roadmap_dict.get("active_session_number"),
        "sessions": [
            {
                "session_number": item.get("session_number"),
                "title": item.get("title"),
                "scheduled_date": item.get("scheduled_date"),
                "status": item.get("status"),
                "doc_id": item.get("doc_id"),
            }
            for item in sessions
        ],
        "session_count": len(sessions),
        "created_at": roadmap_dict.get("created_at"),
        "updated_at": roadmap_dict.get("updated_at"),
    }


def _compact_test_item(test: Dict[str, Any]) -> Dict[str, Any]:
    attachments = test.get("attachments", []) or []
    payload = {
        "id": test.get("id"),
        "title": test.get("title"),
        "source": test.get("source") or "manual",
        "catalog_id": test.get("catalog_id"),
        "interactive_test_id": test.get("interactive_test_id"),
        "interactive_status": test.get("interactive_status"),
        "interactive_attempt_id": test.get("interactive_attempt_id"),
        "url": test.get("url"),
        "result_summary": test.get("result_summary") or test.get("result_text") or "",
        "attachment_count": len(attachments),
        "attachments": [
            {
                "id": item.get("id"),
                "file_name": item.get("file_name"),
                "content_type": item.get("content_type"),
            }
            for item in attachments
            if isinstance(item, dict)
        ],
        "created_at": test.get("created_at"),
        "updated_at": test.get("updated_at"),
        "case_id": test.get("case_id"),
    }
    return payload


def _available_interactive_tests_for_user(user: CustomUser) -> List[Dict[str, Any]]:
    rules = (
        EsanjTestAccessRule.objects.filter(is_active=True)
        .prefetch_related("eligible_expert_professions")
        .order_by("esanj_test_id")
    )
    return [
        {
            "interactive_test_id": rule.esanj_test_id,
            "title": rule.title,
            "kind": "interactive",
        }
        for rule in rules
        if user_can_access_esanj_rule(user, rule)
    ]


def _get_accessible_interactive_rule_for_user(user: CustomUser, interactive_test_id: int):
    rule = (
        EsanjTestAccessRule.objects.prefetch_related("eligible_expert_professions")
        .filter(esanj_test_id=interactive_test_id)
        .first()
    )
    if not rule or not user_can_access_esanj_rule(user, rule):
        return None
    return rule


def _compact_task_item(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": task.get("id"),
        "text": task.get("text"),
        "status": task.get("status"),
        "dimension": task.get("dimension"),
        "due_date": task.get("due_date"),
        "created_at": task.get("created_at"),
        "completed_at": task.get("completed_at"),
        "case_id": task.get("case_id"),
    }


def _compact_form_item(form: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": form.get("id"),
        "form_key": form.get("form_key"),
        "type": form.get("type"),
        "date": form.get("date"),
        "case_id": form.get("case_id"),
        "is_base_profile": bool(form.get("is_base_profile")),
    }


def _compact_file_item(file_item: Dict[str, Any]) -> Dict[str, Any]:
    text_stats = file_item.get("text_stats", {}) if isinstance(file_item.get("text_stats"), dict) else {}
    return {
        "id": file_item.get("id"),
        "name": file_item.get("name"),
        "original_file_name": file_item.get("original_file_name"),
        "description": file_item.get("description"),
        "content_type": file_item.get("content_type"),
        "file_extension": file_item.get("file_extension"),
        "uploaded_at": file_item.get("uploaded_at"),
        "extraction_status": file_item.get("extraction_status"),
        "text_stats": {
            "readable": bool(text_stats.get("readable")),
            "total_chars": int(text_stats.get("total_chars") or 0),
            "total_chunks": int(text_stats.get("total_chunks") or 0),
            "total_pages": int(text_stats.get("total_pages") or 0),
        },
    }


def _build_case_snapshot_payload(case_meta: Dict[str, Any], case_payload: Dict[str, Any]) -> Dict[str, Any]:
    selected_case = case_payload.get("selected_case", {})
    forms = selected_case.get("forms", []) or []
    tests = selected_case.get("tests", []) or []
    tasks = selected_case.get("tasks", []) or []
    files = selected_case.get("files", []) or []
    roadmap = selected_case.get("roadmap_data", {}) or {}

    return {
        "case": _compact_case_item(case_meta),
        "clinical_summary": selected_case.get("clinical_summary", ""),
        "forms_tests_analysis": selected_case.get("forms_tests_analysis", ""),
        "roadmap": _compact_roadmap_payload(roadmap),
        "forms": [_compact_form_item(item) for item in forms],
        "tests": [_compact_test_item(item) for item in tests],
        "tasks": [_compact_task_item(item) for item in tasks],
        "files": [_compact_file_item(item) for item in files],
        "counts": {
            "forms": len(forms),
            "tests": len(tests),
            "tasks": len(tasks),
            "files": len(files),
        },
    }


async def _build_case_payload(patient: CustomUser, doctor_id: int, case_id: str) -> Dict[str, Any]:
    roadmap = await sync_to_async(RoadmapService.get_or_create_roadmap)(patient, doctor_id, case_id)
    appendix = await sync_to_async(AppendixService.get_library)(patient, doctor_id, case_id)
    medications = await sync_to_async(MedicationService.get_plan)(patient, doctor_id, case_id)
    sessions = await sync_to_async(SessionService.get_patient_history)(patient, "DOCTOR", doctor_id, case_id)
    return {
        "selected_case": {
            "clinical_summary": await sync_to_async(ProfileService.get_summary)(patient, doctor_id, case_id),
            "forms_tests_analysis": await sync_to_async(ProfileService.get_forms_tests_analysis)(patient, doctor_id, case_id),
            "roadmap_data": roadmap.model_dump(),
            "appendix_data": appendix.model_dump(),
            "medications": [item.model_dump() for item in medications.medications],
            "tasks": await sync_to_async(TaskService.get_patient_tasks)(patient, doctor_id, case_id),
            "sessions": sessions,
            "active_goals": _extract_active_goals_from_history(sessions),
            "forms": await sync_to_async(CaseService.get_visible_form_entries)(patient, "EXPERT", doctor_id, case_id),
            "tests": await sync_to_async(CaseService.get_visible_tests)(patient, "EXPERT", doctor_id, case_id),
            "files": await sync_to_async(CaseFilesService.get_files)(patient, doctor_id, case_id),
        },
        "base_profile": {
            "form": (await sync_to_async(CaseService.get_latest_base_profile_entry)(patient)).data if await sync_to_async(CaseService.get_latest_base_profile_entry)(patient) else {},
            "forms": await sync_to_async(CaseService.get_visible_form_entries)(patient, "EXPERT", doctor_id, None),
            "tests": await sync_to_async(CaseService.get_visible_tests)(patient, "EXPERT", doctor_id, None),
        }
    }


def _tool_family_error(doctor: CustomUser, family: str) -> Optional[str]:
    policy = get_policy_for_user(doctor)
    if is_tool_family_allowed(policy, family):
        return None
    return "❌ This action is not available for your expert profession."


def _resolve_allowed_form_keys_for_user(doctor: CustomUser) -> List[str]:
    return resolve_allowed_form_keys(ALL_FORMS_LIST, getattr(getattr(doctor, "expert_profession", None), "slug", None))


async def _emit_canvas_refresh(run_context: RunContext, patient: CustomUser, doctor: CustomUser, case_id: str, extra_delta: Optional[Dict[str, Any]] = None):
    canvas_id = await _get_canvas_id(run_context.session_id, "VANIA_PATIENT_MANAGER")
    case_meta = await sync_to_async(CaseService.get_accessible_case_for_expert)(patient, doctor, case_id)
    storage_doctor_id = int((case_meta or {}).get("doctor_id") or doctor.id)
    payload = await _build_case_payload(patient, storage_doctor_id, case_id)
    profession_slug = getattr(getattr(doctor, "expert_profession", None), "slug", None)
    policy_payload = build_canvas_policy_payload(profession_slug, viewer="expert", form_definitions=ALL_FORMS_LIST)
    allowed_form_keys = policy_payload["allowed_form_keys"]
    selected_case_payload = sanitize_expert_case_payload(
        payload["selected_case"],
        profession_slug,
        allowed_form_keys,
    )
    payload.update({
        **policy_payload,
        "selected_case_id": case_id,
        "cases": await sync_to_async(CaseService.get_accessible_cases_for_expert)(patient, doctor),
        "patient_profile": await sync_to_async(CaseService.build_patient_profile)(patient),
        "base_profile": {
            "form": payload["base_profile"]["form"],
            "forms": sanitize_expert_case_payload(
                {
                    "forms": payload["base_profile"]["forms"],
                    "tests": [],
                },
                profession_slug,
                allowed_form_keys,
            )["forms"],
            "tests": sanitize_expert_case_payload(
                {
                    "forms": [],
                    "tests": payload["base_profile"]["tests"],
                },
                profession_slug,
                allowed_form_keys,
            )["tests"],
        },
        "tests_catalog": filter_tests_catalog(TEST_CATALOG, profession_slug),
        "available_forms": filter_form_definitions(ALL_FORMS_LIST, profession_slug),
        "selected_case": {
            "id": case_id,
            "title": case_meta.get("title") if case_meta else "",
            "doctor_id": case_meta.get("doctor_id") if case_meta else storage_doctor_id,
            "doctor_name": case_meta.get("doctor_name") if case_meta else "",
            "doctor_profession_slug": case_meta.get("doctor_profession_slug") if case_meta else profession_slug,
            "doctor_profession_label": case_meta.get("doctor_profession_label") if case_meta else "",
            "can_edit": case_meta.get("can_edit", True) if case_meta else True,
            "is_read_only": case_meta.get("is_read_only", False) if case_meta else False,
            **policy_payload,
            **selected_case_payload,
        },
    })
    if extra_delta:
        payload.update(extra_delta)
    if canvas_id:
        try:
            await sync_to_async(canvas_manager.update_canvas_state)(
                canvas_id=canvas_id,
                patch_data=payload,
                operation="merge",
            )
        except Exception as exc:
            logger.warning("Failed to persist expert canvas refresh for %s: %s", canvas_id, exc)

    return CanvasUpdateEvent(value={
        "canvas_id": canvas_id,
        "component_key": "VANIA_PATIENT_MANAGER",
        "delta": payload,
    })


async def _emit_visitor_refresh(
    run_context: RunContext,
    patient: CustomUser,
    doctor: CustomUser,
    case_id: Optional[str] = None,
) -> CanvasUpdateEvent:
    selected_case = None
    if case_id:
        selected_case = await sync_to_async(CaseService.get_accessible_case_for_expert)(patient, doctor, case_id)
    if not selected_case:
        accessible_cases = await sync_to_async(CaseService.get_accessible_cases_for_expert)(patient, doctor)
        selected_case = accessible_cases[0] if accessible_cases else None

    if selected_case:
        return await _emit_canvas_refresh(
            run_context,
            patient,
            doctor,
            selected_case["id"],
            {
                "active_view": "CASES",
                "selected_case_id": selected_case["id"],
                "selected_doctor_id": selected_case.get("doctor_id"),
            },
        )

    canvas_id = await _get_canvas_id(run_context.session_id, "VANIA_PATIENT_MANAGER")
    profession_slug = getattr(getattr(doctor, "expert_profession", None), "slug", None)
    policy_payload = build_canvas_policy_payload(profession_slug, viewer="expert", form_definitions=ALL_FORMS_LIST)
    base_form_entry = await sync_to_async(CaseService.get_latest_base_profile_entry)(patient)
    base_form = base_form_entry.data if base_form_entry and isinstance(base_form_entry.data, dict) else {}
    base_forms = await sync_to_async(CaseService.get_visible_form_entries)(patient, "EXPERT", int(doctor.id), None)
    base_tests = await sync_to_async(CaseService.get_visible_tests)(patient, "EXPERT", int(doctor.id), None)
    base_payload = sanitize_expert_case_payload(
        {
            "forms": base_forms,
            "tests": base_tests,
        },
        profession_slug,
        policy_payload["allowed_form_keys"],
    )
    return CanvasUpdateEvent(value={
        "canvas_id": canvas_id,
        "component_key": "VANIA_PATIENT_MANAGER",
        "delta": {
            "is_active": True,
            "active_view": "BASE",
            "active_tab": "CASE_OVERVIEW",
            "patient_profile": await sync_to_async(CaseService.build_patient_profile)(patient),
            "base_profile": {
                "form": base_form,
                "forms": base_payload["forms"],
                "tests": base_payload["tests"],
            },
            "cases": [],
            "selected_case_id": None,
            "selected_case": None,
            "selected_doctor_id": None,
            "tests_catalog": filter_tests_catalog(TEST_CATALOG, profession_slug),
            "available_forms": filter_form_definitions(ALL_FORMS_LIST, profession_slug),
            **policy_payload,
            "ui_signal": None,
        },
    })


@tool
async def get_my_expert_profile(run_context: RunContext) -> AsyncGenerator[Any, None]:
    doctor = await _get_active_doctor(run_context)
    payload = await sync_to_async(get_expert_profile_payload)(doctor)
    if not payload:
        yield "❌ Expert profile not found."
        return
    yield json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def get_active_visitor_profile(run_context: RunContext) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    if not patient:
        yield "Error: No visitor selected. Use `list_accessible_visitors` and `select_visitor` first."
        return
    payload = await sync_to_async(get_visitor_base_profile_payload)(patient)
    yield json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def list_accessible_visitors(run_context: RunContext) -> AsyncGenerator[Any, None]:
    doctor = await _get_active_doctor(run_context)
    active_patient = await _get_active_patient()
    visitors = await sync_to_async(_compact_accessible_visitors)(doctor)
    yield json.dumps(
        {
            "active_visitor_id": int(active_patient.id) if active_patient else None,
            "count": len(visitors),
            "visitors": visitors,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
async def select_visitor(run_context: RunContext, visitor_id: int, case_id: str = "") -> AsyncGenerator[Any, None]:
    doctor = await _get_active_doctor(run_context)
    connection = await sync_to_async(TreatmentConnection.objects.filter(
        doctor=doctor,
        patient_id=visitor_id,
        status=TreatmentConnection.Status.ACTIVE,
    ).select_related("patient").first)()
    if not connection:
        yield "❌ Visitor not found or not accessible to you."
        return

    patient = connection.patient
    event = await _emit_visitor_refresh(run_context, patient, doctor, case_id or None)
    yield event

    accessible_cases = await sync_to_async(CaseService.get_accessible_cases_for_expert)(patient, doctor)
    active_case = None
    if case_id:
        active_case = next((item for item in accessible_cases if item.get("id") == case_id), None)
    if not active_case and accessible_cases:
        active_case = accessible_cases[0]

    payload = {
        "visitor": (await sync_to_async(_compact_visitor_item)(patient, doctor)),
        "active_case_id": active_case.get("id") if active_case else None,
        "active_case_title": active_case.get("title") if active_case else None,
        "case_count": len(accessible_cases),
    }
    yield json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def list_accessible_cases(run_context: RunContext) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected. Use `list_accessible_visitors` and `select_visitor` first."
        return
    cases = await sync_to_async(CaseService.get_accessible_cases_for_expert)(patient, doctor)
    active_case_id = selected_case_context.get()
    if not active_case_id and cases:
        active_case_id = cases[0].get("id")
    yield json.dumps(
        {
            "active_case_id": active_case_id,
            "count": len(cases),
            "cases": [_compact_case_item(item) for item in cases],
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
async def get_case_snapshot(run_context: RunContext, case_id: str = "") -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected. Use `list_accessible_visitors` and `select_visitor` first."
        return
    target_case = await sync_to_async(CaseService.get_accessible_case_for_expert)(patient, doctor, case_id or "")
    if not target_case:
        target_case = await _get_active_case(patient, doctor)
    if not target_case:
        yield "❌ No accessible case found."
        return
    storage_doctor_id = int(target_case.get("doctor_id") or doctor.id)
    case_payload = await _build_case_payload(patient, storage_doctor_id, target_case["id"])
    yield json.dumps(_build_case_snapshot_payload(target_case, case_payload), ensure_ascii=False, indent=2)


@tool
async def create_case(run_context: RunContext, title: str = "") -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected."
        return
    created_case = await sync_to_async(CaseService.create_case)(patient, doctor, title or None)
    event = await _emit_canvas_refresh(run_context, patient, doctor, created_case["id"], {"active_view": "CASES"})
    yield event
    yield f"✅ Case '{created_case['title']}' created and selected."


@tool
async def rename_case(run_context: RunContext, case_id: str, title: str) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected."
        return
    editable_error = await _ensure_case_editable(patient, doctor, case_id)
    if editable_error:
        yield editable_error
        return
    updated_case = await sync_to_async(CaseService.rename_case)(patient, int(doctor.id), case_id, title)
    if not updated_case:
        yield "❌ Case not found or title is invalid."
        return
    event = await _emit_canvas_refresh(run_context, patient, doctor, case_id, {"active_view": "CASES"})
    yield event
    yield f"✅ Case renamed to '{updated_case['title']}'."


@tool
async def delete_case(run_context: RunContext, case_id: str) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected."
        return
    case_item = await sync_to_async(CaseService.get_accessible_case_for_expert)(patient, doctor, case_id)
    if not case_item:
        yield "❌ Case not found."
        return
    if not case_item.get("can_edit"):
        yield "❌ This case is read-only for you."
        return
    deleted = await sync_to_async(CaseService.delete_case)(patient, int(doctor.id), case_id)
    if not deleted:
        yield "❌ Case could not be deleted."
        return
    remaining_cases = await sync_to_async(CaseService.get_accessible_cases_for_expert)(patient, doctor)
    if remaining_cases:
        next_case_id = remaining_cases[0]["id"]
    else:
        next_case = await sync_to_async(CaseService.create_case)(patient, doctor)
        next_case_id = next_case["id"]
    event = await _emit_canvas_refresh(run_context, patient, doctor, next_case_id, {"active_view": "CASES"})
    yield event
    yield f"✅ Case '{case_item['title']}' deleted."


@tool
async def select_case(run_context: RunContext, case_id: str) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    if not patient:
        yield "Error: No visitor selected."
        return
    case_item = await sync_to_async(CaseService.get_accessible_case_for_expert)(patient, doctor, case_id)
    if not case_item:
        yield "❌ Case not found."
        return
    event = await _emit_canvas_refresh(run_context, patient, doctor, case_id, {"active_view": "CASES"})
    yield event
    yield f"✅ Case '{case_item['title']}' is now active."


@tool
async def update_clinical_summary(run_context: RunContext, summary_text: str) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "clinical_summary")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor is selected."
        return
    active_case = await _get_active_case(patient, doctor)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    await sync_to_async(ProfileService.update_summary)(patient, summary_text, int(active_case.get("doctor_id") or doctor.id), active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ Case clinical summary updated."


@tool
async def manage_roadmap(run_context: RunContext, action: str, data: Dict[str, Any] = {}) -> AsyncGenerator[Any, None]:
    """
    Manage the roadmap tab (`سند پشتیبان`) for the active case.

    Supported actions:
    - SNAPSHOT / GET
    - SET_PHASE with data.phase
    - ADD_SESSION with data.title, optional data.instructions, optional data.scheduled_date
    - UPDATE_STRATEGY with data.approaches
    - SET_ACTIVE_SESSION with data.session_number
    - DELETE_SESSION with data.session_number

    Do not use invented actions like INIT, SET_PHASE_1, or SET_TREATMENT_APPROACHES_EMPTY.
    Use ADD_SESSION to create or schedule a future session.
    Use finalize_session_report only for a completed structured session report.
    """
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "roadmap")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    doctor_id = int(active_case.get("doctor_id") or doctor.id)
    action_key = (action or "").upper()
    if action_key in {"GET", "SNAPSHOT"}:
        roadmap = await sync_to_async(RoadmapService.get_or_create_roadmap)(patient, doctor_id, active_case["id"])
        yield json.dumps(
            {
                "case": _compact_case_item(active_case),
                "roadmap": _compact_roadmap_payload(roadmap),
            },
            ensure_ascii=False,
            indent=2,
        )
        return
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    if action_key == "SET_PHASE":
        phase_enum = TherapyPhase(data.get("phase"))
        await sync_to_async(RoadmapService.update_phase)(patient, phase_enum, doctor_id, active_case["id"])
    elif action_key == "ADD_SESSION":
        await sync_to_async(RoadmapService.add_session)(
            patient,
            data.get("title") or "جلسه جدید",
            data.get("instructions", ""),
            data.get("scheduled_date"),
            doctor_id,
            active_case["id"],
        )
    elif action_key == "UPDATE_STRATEGY":
        roadmap = await sync_to_async(RoadmapService.get_or_create_roadmap)(patient, doctor_id, active_case["id"])
        roadmap.treatment_approaches = data.get("approaches", [])
        await sync_to_async(RoadmapService.save_roadmap)(patient, roadmap, doctor_id, active_case["id"])
    elif action_key == "SET_ACTIVE_SESSION":
        session_number = data.get("session_number")
        if session_number is None:
            yield "❌ session_number is required."
            return
        try:
            await sync_to_async(RoadmapService.set_active_session)(patient, int(session_number), doctor_id, active_case["id"])
        except ValueError as exc:
            yield f"❌ {exc}"
            return
    elif action_key == "DELETE_SESSION":
        session_number = data.get("session_number")
        if session_number is None:
            yield "❌ session_number is required."
            return
        roadmap = await sync_to_async(RoadmapService.get_or_create_roadmap)(patient, doctor_id, active_case["id"])
        target_session = next((item for item in roadmap.sessions if item.session_number == int(session_number)), None)
        if not target_session:
            yield "❌ Session not found."
            return
        if target_session.doc_id:
            try:
                await sync_to_async(SessionService.delete_session)(int(target_session.doc_id), doctor)
            except Exception as exc:
                logger.warning("Failed to delete linked session log for roadmap session %s: %s", session_number, exc)
        deleted = await sync_to_async(RoadmapService.delete_session)(patient, int(session_number), doctor_id, active_case["id"])
        if not deleted:
            yield "❌ Session not found."
            return
    else:
        yield (
            f"❌ Unknown action for manage_roadmap: '{action}'. "
            "Supported actions: SNAPSHOT, SET_PHASE, ADD_SESSION, UPDATE_STRATEGY, "
            "SET_ACTIVE_SESSION, DELETE_SESSION. Use finalize_session_report for a completed session report."
        )
        return
    await sync_to_async(CaseService.touch_case)(patient, doctor_id, active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ Roadmap updated."


@tool
async def finalize_session_report(
    run_context: RunContext,
    session_number: int,
    topic: str,
    summary: str,
    swot: Optional[Dict[str, Any]] = None,
    smart_goals: Optional[List[str]] = None,
    flashcards: Optional[List[Any]] = None,
    private_notes: str = "",
) -> AsyncGenerator[Any, None]:
    """
    Finalize one completed session report for the active case.

    Use this only for a completed session, not for creating a future roadmap session placeholder.

    Accepted shapes:
    - `swot`: dict with Strengths/Weaknesses/Opportunities/Threats or lowercase equivalents.
    - `flashcards`: list of strings, {"title": "...", "content": "..."}, or {"front": "...", "back": "..."}.
    """
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "roadmap")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    normalized_flashcards = normalize_flashcards(flashcards or [])
    normalized_swot = _normalize_swot(swot or {})
    payload = {
        "is_structured_report": True,
        "session_number": session_number,
        "topic": topic,
        "date": timezone.now().strftime("%Y-%m-%d"),
        "symptoms_analysis": summary,
        "swot_analysis": normalized_swot,
        "smart_goals": [str(item).strip() for item in (smart_goals or []) if str(item).strip()],
        "flashcards": normalized_flashcards,
    }
    storage_doctor_id = int(active_case.get("doctor_id") or doctor.id)
    target_session = await sync_to_async(RoadmapService.ensure_session)(
        patient,
        session_number=session_number,
        title=topic or f"جلسه {session_number}",
        scheduled_date=payload["date"],
        status="COMPLETED",
        doctor_id=storage_doctor_id,
        case_id=active_case["id"],
    )
    log_entry = await sync_to_async(SessionService.save_structured_report)(
        patient=patient,
        doctor=doctor,
        summary=json.dumps(payload, ensure_ascii=False),
        private_notes=private_notes,
        doctor_id=storage_doctor_id,
        case_id=active_case["id"],
        entry_id=target_session.doc_id,
    )
    await sync_to_async(RoadmapService.complete_session)(patient, session_number, str(log_entry.id), storage_doctor_id, active_case["id"])
    await sync_to_async(CaseService.touch_case)(patient, storage_doctor_id, active_case["id"])
    try:
        await sync_to_async(PatientDataService.refresh_patient_dashboard_canvas)(
            patient,
            storage_doctor_id,
            active_case["id"],
        )
    except Exception as exc:
        logger.warning(
            "Failed to refresh visitor dashboard after finalizing session %s for patient %s: %s",
            session_number,
            patient.id,
            exc,
        )
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield f"✅ Session {session_number} report finalized."


async def _manage_rescue_task_impl(
    run_context: RunContext,
    action: str,
    data: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "rescue_net")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    storage_doctor_id = int(active_case.get("doctor_id") or doctor.id)
    action_key = (action or "").upper()
    payload = data or {}
    if action_key in {"LIST", "GET", "SNAPSHOT"}:
        tasks = await sync_to_async(TaskService.get_patient_tasks)(patient, storage_doctor_id, active_case["id"])
        yield json.dumps(
            {
                "case": _compact_case_item(active_case),
                "count": len(tasks),
                "tasks": [_compact_task_item(item) for item in tasks],
            },
            ensure_ascii=False,
            indent=2,
        )
        return
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    if action_key == "ADD":
        text = (payload.get("text") or "").strip()
        dimension = normalize_rescue_dimension(payload.get("dimension"))
        if not text:
            yield "❌ text is required."
            return
        if not dimension:
            yield "❌ dimension is required."
            return
        await sync_to_async(TaskService.assign_task)(
            patient,
            doctor,
            text,
            payload.get("due_date"),
            dimension,
            storage_doctor_id,
            active_case["id"],
        )
    elif action_key == "UPDATE":
        task_id = payload.get("task_id") or payload.get("id")
        if not task_id:
            yield "❌ task_id is required."
            return
        updated = await sync_to_async(TaskService.edit_task)(
            patient,
            task_id,
            payload.get("text"),
            payload.get("due_date"),
            storage_doctor_id,
            active_case["id"],
        )
        if not updated:
            yield "❌ Task not found."
            return
    elif action_key in {"SET_STATUS", "UPDATE_STATUS", "TOGGLE_STATUS"}:
        task_id = payload.get("task_id") or payload.get("id")
        if not task_id:
            yield "❌ task_id is required."
            return
        status = (payload.get("status") or "").upper()
        if action_key == "TOGGLE_STATUS":
            tasks = await sync_to_async(TaskService.get_patient_tasks)(patient, storage_doctor_id, active_case["id"])
            existing = next((item for item in tasks if item.get("id") == task_id), None)
            if not existing:
                yield "❌ Task not found."
                return
            status = "PENDING" if existing.get("status") == "DONE" else "DONE"
        if status not in {"PENDING", "DONE"}:
            yield "❌ status must be PENDING or DONE."
            return
        updated = await sync_to_async(TaskService.update_task_status)(
            patient,
            task_id,
            status,
            payload.get("reflection"),
            storage_doctor_id,
            active_case["id"],
        )
        if not updated:
            yield "❌ Task not found."
            return
    elif action_key == "DELETE":
        task_id = payload.get("task_id") or payload.get("id")
        if not task_id:
            yield "❌ task_id is required."
            return
        deleted = await sync_to_async(TaskService.delete_task)(
            patient,
            task_id,
            storage_doctor_id,
            active_case["id"],
        )
        if not deleted:
            yield "❌ Task not found."
            return
    else:
        yield f"❌ Unknown rescue task action '{action}'."
        return
    await sync_to_async(CaseService.touch_case)(patient, storage_doctor_id, active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ Rescue net updated."


@tool
async def add_rescue_task(run_context: RunContext, text: str, dimension: str, due_date: str = None) -> AsyncGenerator[Any, None]:
    async for item in _manage_rescue_task_impl(
        run_context,
        action="ADD",
        data={"text": text, "dimension": dimension, "due_date": due_date},
    ):
        yield item


@tool
async def manage_rescue_task(
    run_context: RunContext,
    action: str,
    data: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Any, None]:
    async for item in _manage_rescue_task_impl(run_context, action=action, data=data):
        yield item


@tool
async def prescribe_resource(run_context: RunContext, type: str, title: str, creator: str, reason: str, excerpt: str = "") -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "appendix")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    await sync_to_async(AppendixService.add_resource)(
        patient,
        doctor,
        {
            "type": normalize_resource_type(type),
            "title": title,
            "creator": creator,
            "reason_for_prescription": reason,
            "content_excerpt": excerpt,
        },
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
    )
    await sync_to_async(CaseService.touch_case)(patient, int(active_case.get("doctor_id") or doctor.id), active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield f"✅ Resource '{title}' added."


@tool
async def manage_medications(
    run_context: RunContext,
    action: str,
    data: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Any, None]:
    """
    Manage case-scoped medications for the active case.

    Supported actions:
    - ADD / ADD_MEDICATION
    - UPDATE / UPDATE_MEDICATION
    - DELETE / DELETE_MEDICATION
    - REPLACE / REPLACE_PLAN
    - GET / LIST / SNAPSHOT

    Notes:
    - For add/update/delete, pass medication fields in `data`.
    - Canonical persisted fields are `drug_name`, `dosage`, `timing`, `duration`, `usage_instructions`, and `notes`.
    - Common aliases such as `frequency`, `instructions`, `route`, `start_date`, `end_date`, `indication`, `side_effects`, and `status` are folded into those saved fields.
    - For add, `drug_name` or `name` is preferred, but if omitted a minimal placeholder is saved.
    - For read actions, this tool returns the current medication plan for the active case.
    """
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "medications")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return

    storage_doctor_id = int(active_case.get("doctor_id") or doctor.id)
    action_key = _normalize_medication_action(action)
    payload = data or {}
    normalized_medication = _normalize_medication_payload(payload)
    if action_key in {"GET", "LIST", "SNAPSHOT"}:
        plan = await sync_to_async(MedicationService.get_plan)(patient, storage_doctor_id, active_case["id"])
        yield json.dumps(
            {
                "case": _compact_case_item(active_case),
                "medications": [item.model_dump() for item in plan.medications],
            },
            ensure_ascii=False,
            indent=2,
        )
        return
    if action_key == "ADD":
        await sync_to_async(MedicationService.add_medication)(
            patient,
            doctor,
            {
                **normalized_medication,
                "drug_name": normalized_medication["drug_name"] or "داروی جدید",
            },
            storage_doctor_id,
            active_case["id"],
        )
    elif action_key == "UPDATE":
        updated = await sync_to_async(MedicationService.update_medication)(
            patient,
            payload.get("medication_id") or payload.get("id"),
            {
                key: value
                for key, value in normalized_medication.items()
                if value is not None
            },
            creator=doctor,
            doctor_id=storage_doctor_id,
            case_id=active_case["id"],
        )
        if not updated:
            yield "❌ Medication not found."
            return
    elif action_key == "DELETE":
        deleted = await sync_to_async(MedicationService.delete_medication)(
            patient,
            payload.get("medication_id") or payload.get("id"),
            creator=doctor,
            doctor_id=storage_doctor_id,
            case_id=active_case["id"],
        )
        if not deleted:
            yield "❌ Medication not found."
            return
    elif action_key == "REPLACE":
        plan = await sync_to_async(MedicationService.save_plan)(
            patient,
            payload.get("medications", []),
            doctor,
            storage_doctor_id,
            active_case["id"],
        )
        yield json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)
    else:
        yield f"❌ Unknown medication action '{action}'."
        return

    await sync_to_async(CaseService.touch_case)(patient, storage_doctor_id, active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ Medication plan updated."


@tool
async def get_current_medications(run_context: RunContext) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "medications")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    storage_doctor_id = int(active_case.get("doctor_id") or doctor.id)
    plan = await sync_to_async(MedicationService.get_plan)(patient, storage_doctor_id, active_case["id"])
    yield json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)


@tool
async def get_form_schema(run_context: RunContext, form_key: str) -> AsyncGenerator[Any, None]:
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "forms")
    if blocked:
        yield blocked
        return
    allowed_form_keys = _resolve_allowed_form_keys_for_user(doctor)
    if form_key not in allowed_form_keys:
        yield f"❌ Form '{form_key}' is not available for your expert profession."
        return
    form_def = next((f for f in ALL_FORMS_LIST if f["key"] == form_key), None)
    if not form_def:
        yield f"Error: Form with key '{form_key}' not found."
        return
    flattened_fields = _flatten_form_fields(form_def.get("schema", []))
    yield json.dumps({
        "form_key": form_def["key"],
        "title": form_def["title"],
        "description": form_def["description"],
        "fields": [
            {
                "name": field["name"],
                "label": field["label"],
                "type": field.get("type", "text"),
                "options": field.get("options"),
            }
            for field in flattened_fields
        ],
    }, ensure_ascii=False, indent=2)


@tool
async def submit_clinical_form(run_context: RunContext, form_key: str, **kwargs) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "forms")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    allowed_form_keys = _resolve_allowed_form_keys_for_user(doctor)
    if form_key not in allowed_form_keys:
        yield f"❌ Form '{form_key}' is not available for your expert profession."
        return
    form_def = next((f for f in ALL_FORMS_LIST if f["key"] == form_key), None)
    if not form_def:
        yield f"Error: Invalid form_key '{form_key}'."
        return
    actual_data = kwargs.get("kwargs", kwargs) if "kwargs" in kwargs else kwargs
    active_case = await _get_active_case(patient, doctor)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    timestamp = int(time.time())
    instance_key = f"clinical_form_{form_key.lower()}_{timestamp}"
    payload = {
        "form_key": form_key,
        "form_title": form_def["title"],
        "handler": "AgentTool",
        "submitted_by_doctor_id": int(active_case.get("doctor_id") or doctor.id),
        "submission_timestamp": timestamp,
        "visibility_scope": "SHARED_BASE" if form_key == "BASE_PROFILE_V1" else "CASE_PRIVATE",
        "case_id": None if form_key == "BASE_PROFILE_V1" else active_case["id"],
        **actual_data,
    }
    if form_key == "BASE_PROFILE_V1":
        await sync_to_async(CaseService.save_base_profile)(
            patient,
            payload,
            creator=doctor,
            source=UserContextEntry.SourceType.AGENT,
        )
    else:
        await sync_to_async(ContextDefinition.objects.get_or_create)(key=instance_key, defaults={"description": f"Agent submission: {form_def['title']}"})
        await sync_to_async(user_context_manager.add_entry)(user=patient, key=instance_key, data=payload, source=UserContextEntry.SourceType.AGENT, creator=doctor)
    if form_key == "BASE_PROFILE_V1" and payload.get("full_name"):
        patient.full_name = payload["full_name"]
        await sync_to_async(patient.save)(update_fields=["full_name"])
    if form_key != "BASE_PROFILE_V1":
        await sync_to_async(CaseService.touch_case)(patient, int(active_case.get("doctor_id") or doctor.id), active_case["id"])
    selected_case_id = active_case["id"]
    yield await _emit_canvas_refresh(run_context, patient, doctor, selected_case_id)
    yield "✅ Form submitted."


@tool
async def manage_clinical_tests(run_context: RunContext, action: str, data: Dict[str, str | int | bool | None] | None = None) -> AsyncGenerator[Any, None]:
    """
    Manage case-scoped clinical tests.

    Supported actions:
    - LIST / GET / SNAPSHOT
    - ADD_TEST
    - UPDATE_SUMMARY / UPDATE_RESULT_TEXT
    - UPDATE_TEST
    - DELETE_TEST
    - ATTACH_CASE_FILE
    - DELETE_ATTACHMENT

    Important:
    - For an interactive test, call LIST first and then ADD_TEST with data={"source": "interactive", "interactive_test_id": <id>}.
    - If the user asks about files attached to a test, use LIST/SNAPSHOT to identify the test when needed, then call get_test_result_details.
    - If the user asks about one specific attachment such as "the PDF" or "the image", prefer get_test_attachment_details with that attachment id.
    - Do not use this tool for the case-level forms/tests analysis panel.
    """
    data = data or {}
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "tests")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    doctor_id = int(active_case.get("doctor_id") or doctor.id)
    policy = get_policy_for_user(doctor)
    action_key = (action or "").strip().upper()
    action_aliases = {
        "LIST_AVAILABLE": "LIST",
        "ASSIGN_NEW_TEST": "ADD_TEST",
        "ADD": "ADD_TEST",
        "CREATE": "ADD_TEST",
        "CREATE_TEST": "ADD_TEST",
        "SET_RESULT": "UPDATE_SUMMARY",
        "SAVE_RESULT": "UPDATE_SUMMARY",
        "UPDATE_RESULT": "UPDATE_SUMMARY",
        "EDIT_RESULT": "UPDATE_SUMMARY",
    }
    action_key = action_aliases.get(action_key, action_key)
    if action_key in {"LIST", "GET", "SNAPSHOT"}:
        tests = await sync_to_async(ClinicalTestsService.get_tests)(patient, doctor_id, active_case["id"])
        interactive_catalog = await sync_to_async(_available_interactive_tests_for_user)(doctor)
        yield json.dumps(
            {
                "case": _compact_case_item(active_case),
                "count": len(tests),
                "tests": [_compact_test_item(item) for item in tests],
                "available_interactive_tests": interactive_catalog,
            },
            ensure_ascii=False,
            indent=2,
        )
        return
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    if action_key == "ADD_TEST":
        interactive_test_id = data.get("interactive_test_id")
        is_interactive = data.get("source") == "interactive" or bool(interactive_test_id)
        if policy.get("test_mode") == "exams_only":
            if data.get("catalog_id") or is_interactive:
                yield "❌ General doctors can register only manual exam entries without catalog-based or interactive tests."
                return
            data = {**data, "url": ""}
        if is_interactive:
            try:
                interactive_test_id = int(interactive_test_id)
            except (TypeError, ValueError):
                yield "❌ interactive_test_id is required for interactive tests."
                return
            rule = await sync_to_async(_get_accessible_interactive_rule_for_user)(doctor, interactive_test_id)
            if not rule:
                yield "❌ This interactive test is not available for your expert profession."
                return
            data = {**data, "source": "interactive", "interactive_test_id": interactive_test_id, "title": rule.title, "url": ""}
        await sync_to_async(ClinicalTestsService.add_test)(
            patient,
            doctor,
            data.get("catalog_id"),
            data.get("title"),
            data.get("url"),
            None,
            doctor_id,
            active_case["id"],
            data.get("source"),
            data.get("interactive_test_id"),
        )
    elif action_key in {"UPDATE_SUMMARY", "UPDATE_RESULT_TEXT"}:
        updated = await sync_to_async(ClinicalTestsService.update_test)(
            patient,
            doctor,
            data.get("test_id"),
            {
                "result_text": data.get("result_text", data.get("result_summary", "")),
                "result_summary": data.get("result_text", data.get("result_summary", "")),
            },
            doctor_id,
            active_case["id"],
        )
        if not updated:
            yield "❌ Test not found."
            return
    elif action_key == "UPDATE_TEST":
        updated = await sync_to_async(ClinicalTestsService.update_test)(
            patient,
            doctor,
            data.get("test_id"),
            data,
            doctor_id,
            active_case["id"],
        )
        if not updated:
            yield "❌ Test not found."
            return
    elif action_key == "DELETE_TEST":
        deleted = await sync_to_async(ClinicalTestsService.delete_test)(
            patient, doctor, data.get("test_id"), doctor_id, active_case["id"]
        )
        if not deleted:
            yield "❌ Test not found."
            return
    elif action_key == "ATTACH_CASE_FILE":
        file_id = data.get("file_id")
        if not file_id:
            yield "❌ file_id is required."
            return
        updated = await sync_to_async(ClinicalTestsService.attach_case_file)(
            patient,
            doctor,
            data.get("test_id"),
            file_id,
            doctor_id,
            active_case["id"],
        )
        if not updated:
            yield "❌ Test or case file not found."
            return
    elif action_key == "DELETE_ATTACHMENT":
        test_id = data.get("test_id")
        if not test_id:
            yield "❌ test_id is required."
            return
        deleted = await sync_to_async(ClinicalTestsService.remove_test_file)(
            patient,
            doctor,
            test_id,
            data.get("attachment_id"),
            doctor_id,
            active_case["id"],
        )
        if not deleted:
            yield "❌ Test or attachment not found."
            return
    else:
        yield (
            f"❌ Unknown action '{action}'. "
            "Supported actions: LIST, ADD_TEST, UPDATE_SUMMARY, UPDATE_TEST, "
            "DELETE_TEST, ATTACH_CASE_FILE, DELETE_ATTACHMENT."
        )
        return
    await sync_to_async(CaseService.touch_case)(patient, doctor_id, active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ Clinical tests updated."


@tool
async def get_test_result_details(run_context: RunContext, test_id: str) -> ToolResult | str:
    """
    Read one test's saved result and load any available attachment bytes into the current run context.

    Use this whenever the user asks what an attached PDF/image contains, including follow-up requests
    like "the PDF too", "check again", or "reopen the file". Do not claim you lack a PDF/file tool for
    test attachments while this tool is available. If an attachment exists but is not loadable, report
    that the specific attachment could not be loaded from storage.
    """
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "tests")
    if blocked:
        return blocked
    if not patient:
        return "Error: No visitor selected."
    active_case = await _get_active_case(patient, doctor)
    test = await sync_to_async(ClinicalTestsService.get_test)(
        patient,
        test_id,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
    )
    if not test:
        return "❌ Test not found."
    if test_has_loadable_attachments(test):
        return build_test_attachment_tool_result(test)
    payload = await sync_to_async(ClinicalTestsService.read_test_result_bundle)(
        patient,
        test_id,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
    )
    if not payload:
        return "❌ Test not found."
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))


@tool
async def get_test_attachment_details(run_context: RunContext, test_id: str, attachment_id: str) -> ToolResult | str:
    """
    Read one specific saved test attachment and load only that attachment into the current run context.

    Use this when the user refers to a specific attachment type or file inside a test, such as "the PDF",
    "the image", or a specific attachment filename. If the selected attachment exists but cannot be loaded,
    report that exact attachment as unavailable instead of describing another attachment from the same test.
    """
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "tests")
    if blocked:
        return blocked
    if not patient:
        return "Error: No visitor selected."
    active_case = await _get_active_case(patient, doctor)
    test = await sync_to_async(ClinicalTestsService.get_test)(
        patient,
        test_id,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
    )
    if not test:
        return "❌ Test not found."
    attachment = await sync_to_async(ClinicalTestsService.get_test_attachment)(
        patient,
        test_id,
        attachment_id,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
    )
    if not attachment:
        return "❌ Attachment not found."
    return build_test_attachment_tool_result(test, attachment_id=attachment_id)


@tool
async def list_case_files(
    run_context: RunContext,
    page: int = 1,
    page_size: int = 10,
    query: str = "",
    file_type: str = "",
    readable_only: bool = False,
    sort: str = "recent",
) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "files")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    payload = await sync_to_async(CaseFilesService.list_files)(
        patient,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
        page=page,
        page_size=page_size,
        query=query or None,
        file_type=file_type or None,
        readable_only=readable_only,
        sort=sort or "recent",
    )
    yield json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def search_case_files(
    run_context: RunContext,
    query: str,
    page: int = 1,
    page_size: int = 5,
    file_id: str = "",
) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "files")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    active_case = await _get_active_case(patient, doctor)
    payload = await sync_to_async(CaseFilesService.search_files)(
        patient,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
        query=query,
        page=page,
        page_size=page_size,
        file_id=file_id or None,
    )
    yield json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def read_case_file(
    run_context: RunContext,
    file_id: str,
    mode: str = "excerpt",
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    chunk_start: Optional[int] = None,
    chunk_count: int = 3,
    query: str = "",
) -> ToolResult | str:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "files")
    if blocked:
        return blocked
    if not patient:
        return "Error: No visitor selected."
    active_case = await _get_active_case(patient, doctor)
    payload = await sync_to_async(CaseFilesService.read_file)(
        patient,
        int(active_case.get("doctor_id") or doctor.id),
        active_case["id"],
        file_id=file_id,
        mode=mode,
        page=page,
        page_size=page_size,
        chunk_start=chunk_start,
        chunk_count=chunk_count,
        query=query or None,
    )
    if not payload:
        return "❌ File not found."
    file_record = payload.get("file") if isinstance(payload, dict) else None
    if case_file_is_loadable(file_record):
        return build_case_file_tool_result(file_record, payload=payload)
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))


@tool
async def get_case_file_details(run_context: RunContext, file_id: str) -> ToolResult | str:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "files")
    if blocked:
        return blocked
    if not patient:
        return "Error: No visitor selected."
    active_case = await _get_active_case(patient, doctor)
    payload = await sync_to_async(CaseFilesService.get_file_details)(patient, int(active_case.get("doctor_id") or doctor.id), active_case["id"], file_id)
    if not payload:
        return "❌ File not found."
    if case_file_is_loadable(payload):
        return build_case_file_tool_result(payload)
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))


@tool
async def update_forms_tests_analysis(run_context: RunContext, analysis_text: str, case_id: Optional[str] = None) -> AsyncGenerator[Any, None]:
    patient = await _get_active_patient()
    doctor = await _get_active_doctor(run_context)
    blocked = _tool_family_error(doctor, "analysis")
    if blocked:
        yield blocked
        return
    if not patient:
        yield "Error: No visitor selected."
        return
    requested_case_id = str(case_id).strip() if case_id else None
    active_case = await _get_active_case(patient, doctor, requested_case_id)
    editable_error = await _ensure_case_editable(patient, doctor, active_case["id"])
    if editable_error:
        yield editable_error
        return
    await sync_to_async(ProfileService.update_forms_tests_analysis)(patient, analysis_text, int(active_case.get("doctor_id") or doctor.id), active_case["id"])
    yield await _emit_canvas_refresh(run_context, patient, doctor, active_case["id"])
    yield "✅ تحلیل بالینی تست‌ها و فرم‌ها ذخیره شد."


class VaniaExpertToolFactory(BaseCapability):
    def get_tools(self, user, session_id) -> List[Any]:
        tools = [
            get_my_expert_profile,
            get_active_visitor_profile,
            list_accessible_visitors,
            select_visitor,
            list_accessible_cases,
            get_case_snapshot,
            create_case,
            rename_case,
            delete_case,
            select_case,
            update_clinical_summary,
            manage_roadmap,
            finalize_session_report,
            add_rescue_task,
            manage_rescue_task,
            manage_medications,
            get_current_medications,
            prescribe_resource,
            get_form_schema,
            submit_clinical_form,
            manage_clinical_tests,
            get_test_result_details,
            get_test_attachment_details,
            list_case_files,
            search_case_files,
            read_case_file,
            get_case_file_details,
            update_forms_tests_analysis,
        ]
        policy = get_policy_for_user(user)
        return [
            item
            for item in tools
            if is_tool_family_allowed(policy, TOOL_FAMILY_BY_NAME.get(_resolve_tool_name(item), "profiles"))
        ]
