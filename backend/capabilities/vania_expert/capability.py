import json
import logging
from typing import Any, Dict, List, Optional

from capabilities.base import BaseCapability
from capabilities.registry import register_capability
from agents.context import selected_case_context
from users.models import CustomUser
from vania_core.case_service import CaseService
from vania_core.case_files_service import CaseFilesService
from vania_core.profession_policy import (
    build_canvas_policy_payload,
    filter_form_definitions,
    filter_tests_catalog,
    get_policy_for_user,
    sanitize_expert_case_payload,
)
from vania_core.services import (
    RoadmapService,
    AppendixService,
    SessionService,
    TaskService,
    ProfileService,
)
from vania_core.medication_service import MedicationService
from vania_core.tests_catalog import TEST_CATALOG
from vania_core.models import EsanjTestAccessRule
from vania_core.esanj_serializers import user_can_access_esanj_rule

from .forms import ALL_FORMS_LIST
from .document_context import build_document_context

logger = logging.getLogger(__name__)


@register_capability("vania_expert")
class VaniaExpertCapability(BaseCapability):
    def get_tools(self, user: Any, session_id: str) -> List[Any]:
        from .tools import VaniaExpertToolFactory
        return VaniaExpertToolFactory().get_tools(user, session_id)

    def get_system_prompt_additions(self, user: Any) -> str:
        policy = get_policy_for_user(user)
        return f"""
### VANIA EXPERT CAPABILITY: SHARED BASE PROFILE + CASE CONTRACT
You operate on two layers of patient data:

1. Shared base profile
- Shared between the patient and all linked experts.
- `BASE_PROFILE_V1` is the visitor-owned shared profile.
- Base profile read/write actions are not tied to a selected case.

2. Case data (پرونده)
- Every state-changing action for summary, analysis, roadmap, tasks, appendix, sessions, non-base forms, and tests belongs to the active case.
- Medication prescriptions also belong to the active case and are shared with the visitor for reading.
- A patient may have multiple cases with the same expert.
- Case-scoped actions require an active case.
- If no visitor or no case is currently active, you are still allowed to browse the expert's accessible visitors and their accessible cases first, then activate the correct visitor/case in the workspace.

Privacy rules:
- `BASE_PROFILE_V1` is shared.
- Non-base forms and tests are visible only to the patient and the submitting expert.
- Tool-based updates synchronize the shared workspace state.
- Test results may include free-text notes, attached PDF/image files, or structured results from an interactive test.
- When you need the contents of a test result file, use `get_test_result_details` instead of guessing from metadata.
- When the user points to one specific attachment inside a test, such as "the PDF", "the image", or a filename, use `get_test_attachment_details` with that attachment's id.
- If the user asks to inspect, read, reopen, retry, or check a test attachment again, call `get_test_result_details` again for that test.
- Do not say you lack a file/PDF-reading tool for test attachments when `get_test_result_details` is available. If an attachment is not loadable, say that specific attachment could not be loaded from storage.
- To add a new test entry, use `manage_clinical_tests` with action `ADD_TEST`; for an interactive test, use the ID from AVAILABLE INTERACTIVE TESTS as `interactive_test_id`.
- To write or edit the text result of one specific test, use `manage_clinical_tests` with action `UPDATE_SUMMARY` and include `test_id` plus `result_summary` in `data`.
- Do not use `update_forms_tests_analysis` for a single test result. That tool updates the case-level combined analysis panel, not the selected test item.
- For medication updates, the saved fields are `drug_name`, `dosage`, `timing`, `duration`, `usage_instructions`, and `notes`.
- If the user's medication details come in other shapes such as frequency, route, start/end dates, indication, or side effects, map them into those saved fields instead of inventing new schema keys.
- Use `get_my_expert_profile` for your own detailed profile and `get_active_visitor_profile` for the visitor's full shared profile when the brief context is not enough.
- Case files are shared within the active case between the expert and visitor.
- Use `list_accessible_visitors` to browse the visitors this expert can access.
- Use `select_visitor` to make one accessible visitor active in the current workspace, optionally with a specific case.

### GROUNDING AND PREFLIGHT RULES
Before deciding what to do, ground yourself in the current workspace state.

1. Check state first when there is any ambiguity.
- If the request depends on which case is active, whether a case exists, what is already saved, or what role/profession restrictions apply, inspect the current state with tools before answering.
- Prefer checking instead of assuming.

2. Use these tools as your first-line state checks.
- `list_accessible_visitors` when no visitor is active yet, or when you need to browse which visitors the expert can access.
- `select_visitor` when the user wants to open, switch to, or browse one specific accessible visitor.
- `list_accessible_cases` when you need to know which cases exist or which case should be used.
- `get_case_snapshot` when you need the current case contents, saved fields, roadmap, tests, tasks, or files.
- `get_active_visitor_profile` when you need the visitor's full shared profile or base information.
- `get_my_expert_profile` when you need your own expert identity, specialty, or profile context.

3. Read before write when the latest case state matters.
- Before updating summaries, analysis, roadmap, tests, files, or other case artifacts, check the current case with `get_case_snapshot` if there is any chance the latest saved state affects the correct action.
- For direct field-writing requests where the user names the destination clearly, you may write immediately, but if anything is unclear you must inspect first.

4. Do not say you cannot do something until after preflight.
- Do not say a case, user, file, test, or field is unavailable until you have checked the relevant state with the appropriate tool.
- If a tool confirms the limitation, explain that specific limitation briefly.
- If a tool fails, inspect the error, correct the payload if possible, and retry once with a clear fix before giving up.

5. If the user refers to "this", "that field", "the active case", "the current patient", or similar shorthand:
- Resolve it from current case/user state with tools first.
- Then perform the action.

6. Browsing is allowed even before a case is active.
- If the workspace has no active case, do not stop there.
- First browse accessible visitors or cases with tools.
- Then either activate the right visitor/case or answer from the accessible state you just inspected.

### PERSIAN UI TERM MAP
- `اطلاعات پایه` = shared base profile / `BASE_PROFILE_V1`
- `پرونده` = active case overview / `CASE_OVERVIEW`
- `سند پشتیبان` = roadmap tab / `ROADMAP` / `نقشه راه`
- `تور نجات` = rescue net tasks
- `پیوست اندیشه` = thought appendix / prescribed resources
- `شیوه و مصرف دارو` = case medication plan
- `فایل‌ها` = shared case files
- `علت مراجع و مشاهدات` = clinical summary / observations area
- `تحلیل بالینی تست‌ها و فرم‌ها` = case-level forms/tests analysis panel / `forms_tests_analysis`
- `متخصص فعال` = selected doctor / selected expert for the active case
- `پرونده فعال` = selected case title and metadata
- `مراجع فعال` = selected visitor / patient context

Treat the frontend labels above as the source of truth.
In this workspace, `سند پشتیبان` is the same feature as the roadmap tab (`ROADMAP`) and is not a separate tool family.

### ROADMAP TOOL CONTRACT
For roadmap / `سند پشتیبان` work, use only these exact tool operations:
- Read roadmap: `manage_roadmap(action="SNAPSHOT")`
- Change phase: `manage_roadmap(action="SET_PHASE", data={{"phase": "PHASE_1_ANALYSIS" | "PHASE_2_APPROACHES" | "PHASE_3_SELECTION" | "PHASE_4_PROTOCOL" | "PHASE_5_EXECUTION" | "PHASE_6_APPENDIX"}})`
- Add a roadmap session: `manage_roadmap(action="ADD_SESSION", data={{"title": "...", "instructions": "...", "scheduled_date": "YYYY-MM-DD"}})`
- Update treatment approaches: `manage_roadmap(action="UPDATE_STRATEGY", data={{"approaches": ["...", "..."]}})`
- Select the active roadmap session: `manage_roadmap(action="SET_ACTIVE_SESSION", data={{"session_number": 1}})`
- Delete a roadmap session: `manage_roadmap(action="DELETE_SESSION", data={{"session_number": 1}})`
- Finalize a completed session report: `finalize_session_report(session_number=1, topic="...", summary="...", smart_goals=[...], swot={{...}}, flashcards=[...], private_notes="...")`

Do not invent roadmap actions such as `INIT`, `SET_PHASE_1`, or `SET_TREATMENT_APPROACHES_EMPTY`.
If the user asks to "build/fill the support document" (`سند پشتیبان`), that usually means:
1. set the phase if needed,
2. update treatment approaches if needed,
3. add or manage sessions with `manage_roadmap`,
4. and use `finalize_session_report` only when they want a completed session report.

Session workflow:
- To create or schedule a future session, use `manage_roadmap(action="ADD_SESSION", ...)`.
- To mark a session as the one currently being worked on, use `manage_roadmap(action="SET_ACTIVE_SESSION", ...)`.
- To save a completed session report, use `finalize_session_report`.
- Do not use `finalize_session_report` just to create a future session placeholder.
- When the user asks to complete/finalize a session and does not explicitly limit the contents, treat that as a request for a full structured report.
- A full structured report should normally include `summary`, `smart_goals`, `swot`, and `flashcards`.

Accepted `finalize_session_report` content shapes:
- `swot` may use the four sections `Strengths`, `Weaknesses`, `Opportunities`, `Threats` or lowercase equivalents.
- `flashcards` may be a list of strings, `{{"title": "...", "content": "..."}}`, or `{{"front": "...", "back": "..."}}`.

### RESCUE NET TOOL CONTRACT
For `تور نجات`, use only these exact dimensions:
- `PERSONAL`
- `EMOTIONAL`
- `RELATIONSHIP`
- `FRIENDSHIP`
- `CAREER`
- `INTELLECTUAL`
- `ENVIRONMENT`
- `RECREATION`
- `SOLITUDE`

Do not invent freeform or localized dimension values such as `selfcare`, `thoughts`, `social`, or `سایر`.

### APPENDIX TOOL CONTRACT
For `پیوست اندیشه`, use `prescribe_resource` with only these exact `type` values:
- `BOOK`
- `MOVIE`
- `POEM`

Do not send localized or freeform values such as `کتاب`, `فیلم`, or `شعر` in the tool payload.

### PROFESSION-SCOPED ACCESS
{policy.get("prompt_addition", "").strip()}
"""

    def _resolve_selected_case_id(self) -> Optional[str]:
        return selected_case_context.get()

    def _get_explicit_selected_case(self, patient: CustomUser, user: CustomUser) -> Optional[Dict[str, Any]]:
        selected_case_id = self._resolve_selected_case_id()
        if not selected_case_id:
            return None
        return CaseService.get_accessible_case_for_expert(patient, user, selected_case_id)

    def _extract_active_goals(self, patient: CustomUser, doctor_id: int, case_id: str) -> List[str]:
        sessions = SessionService.get_patient_history(patient, viewer_role="DOCTOR", doctor_id=doctor_id, case_id=case_id)
        for session in sessions:
            goals = session.get("smart_goals")
            if isinstance(goals, list) and goals:
                return [str(goal).strip() for goal in goals if str(goal).strip()]
        return []

    def _build_case_payload(self, patient: CustomUser, doctor_id: int, case_id: str) -> Dict[str, Any]:
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_id, case_id=case_id)
        appendix = AppendixService.get_library(patient, doctor_id=doctor_id, case_id=case_id)
        medications = MedicationService.get_plan(patient, doctor_id=doctor_id, case_id=case_id)
        return {
            "clinical_summary": ProfileService.get_summary(patient, doctor_id=doctor_id, case_id=case_id),
            "summary_voice_notes": ProfileService.get_summary_voice_notes(patient, doctor_id=doctor_id, case_id=case_id),
            "forms_tests_analysis": ProfileService.get_forms_tests_analysis(patient, doctor_id=doctor_id, case_id=case_id),
            "roadmap_data": roadmap.model_dump(),
            "appendix_data": appendix.model_dump(),
            "medications": [item.model_dump() for item in medications.medications],
            "tasks": TaskService.get_patient_tasks(patient, doctor_id=doctor_id, case_id=case_id),
            "sessions": SessionService.get_patient_history(patient, viewer_role="DOCTOR", doctor_id=doctor_id, case_id=case_id),
            "active_goals": self._extract_active_goals(patient, doctor_id, case_id),
            "forms": CaseService.get_visible_form_entries(patient, viewer_role="EXPERT", viewer_doctor_id=doctor_id, case_id=case_id),
            "tests": CaseService.get_visible_tests(patient, viewer_role="EXPERT", viewer_doctor_id=doctor_id, case_id=case_id),
            "files": CaseFilesService.get_files(patient, doctor_id=doctor_id, case_id=case_id),
        }

    def get_context_prompt(self, user: Any, resource_id: str) -> str:
        if not resource_id:
            return ""
        try:
            patient = CustomUser.objects.get(pk=resource_id)
            accessible_cases = CaseService.get_accessible_cases_for_expert(patient, user)
            selected_case = self._get_explicit_selected_case(patient, user)
            storage_doctor_id = int((selected_case or {}).get("doctor_id") or user.id)
            base_profile = CaseService.get_latest_base_profile_entry(patient)
            base_lines = []
            base_payload: Dict[str, Any] = {}
            if base_profile and isinstance(base_profile.data, dict):
                data = base_profile.data
                base_payload = {
                    key: value
                    for key, value in data.items()
                    if value not in (None, "", [], {})
                }
                base_lines.extend([
                    f"- Name: {data.get('full_name', patient.full_name or patient.phone_number)}",
                    f"- Mobile: {data.get('mobile_phone', patient.phone_number)}",
                    f"- Email: {data.get('email', 'N/A')}",
                    f"- Birth date: {data.get('birth_date', 'N/A')}",
                    f"- Education: {data.get('education_level', 'N/A')}",
                    f"- Job: {data.get('job_status', 'N/A')} {data.get('job_title', '')}".strip(),
                    f"- Marital status: {data.get('marital_status', 'N/A')}",
                ])
            case_forms = CaseService.get_visible_form_entries(
                patient,
                viewer_role="EXPERT",
                viewer_doctor_id=storage_doctor_id,
                case_id=selected_case["id"] if selected_case else None,
            )
            case_tests = CaseService.get_visible_tests(
                patient,
                viewer_role="EXPERT",
                viewer_doctor_id=storage_doctor_id,
                case_id=selected_case["id"] if selected_case else None,
            )
            case_tasks = (
                TaskService.get_patient_tasks(patient, doctor_id=storage_doctor_id, case_id=selected_case["id"])
                if selected_case
                else []
            )
            case_files = (
                CaseFilesService.get_files(patient, doctor_id=storage_doctor_id, case_id=selected_case["id"])
                if selected_case
                else []
            )
            document_context = (
                build_document_context(patient, storage_doctor_id, selected_case["id"], case_tests, case_files)
                if selected_case else ""
            )
            policy = get_policy_for_user(user)
            allowed_forms = filter_form_definitions(ALL_FORMS_LIST, getattr(getattr(user, "expert_profession", None), "slug", None))
            allowed_tests = filter_tests_catalog(TEST_CATALOG, getattr(getattr(user, "expert_profession", None), "slug", None))
            interactive_tests = [
                rule
                for rule in EsanjTestAccessRule.objects.filter(is_active=True)
                .prefetch_related("eligible_expert_professions")
                .order_by("esanj_test_id")
                if user_can_access_esanj_rule(user, rule)
            ]
            return f"""
### ACTIVE PATIENT
- Visitor: {patient.full_name or patient.phone_number} (ID: {patient.id})
- Active case: {f"{selected_case['title']} (ID: {selected_case['id']})" if selected_case else "No case selected"}
- Access mode: {"Owner" if selected_case and selected_case.get("can_edit") else "Read-only shared case" if selected_case else "Base profile scope"}
- Accessible cases: {len(accessible_cases)}

### SHARED BASE PROFILE
{chr(10).join(base_lines) if base_lines else "- No shared base profile recorded yet. Fill `BASE_PROFILE_V1` when needed."}
- Full shared profile payload: {json.dumps(base_payload, ensure_ascii=False)}

### CASE CONTEXT
- Case forms visible to you: {len(case_forms)}
- Case tests visible to you: {len(case_tests)}
- Rescue net tasks in the active case: {len(case_tasks)}
- Case files visible to you: {len(case_files)}
- Base profile is shared; all other forms/tests are private to the patient and the submitting expert.
- Use file tools for document exploration instead of inferring from filenames.
{document_context}

### AVAILABLE FORMS
{chr(10).join([f"- `{f['key']}` | {f['title']}" for f in allowed_forms]) or "- No case forms available beyond shared base profile."}

### AVAILABLE TESTS
{chr(10).join([f"- `{t['id']}` | {t['title']}" for t in allowed_tests]) or "- No catalog-based tests available for your profession."}

### AVAILABLE INTERACTIVE TESTS
{chr(10).join([f"- `{t.esanj_test_id}` | {t.title}" for t in interactive_tests]) or "- No interactive tests available for your profession."}

### PROFESSION ACCESS
- Profession policy: {policy.get('profession_slug') or 'unknown'}
- Visible expert tabs: {", ".join(policy.get("expert_tabs", [])) or "none"}
- Test mode: {policy.get("test_mode", "disabled")}
- Restriction note: {policy.get("prompt_addition", "").strip()}
"""
        except Exception as exc:
            logger.error("Failed to build expert context: %s", exc, exc_info=True)
            return "### SYSTEM ERROR: Could not load patient context."

    def get_initial_canvas_state(
        self,
        user: Any,
        session_id: str,
        resource_id: str,
        canvas_key: str,
    ) -> Optional[Dict[str, Any]]:
        if canvas_key != "VANIA_PATIENT_MANAGER" or not resource_id:
            return None
        try:
            patient = CustomUser.objects.get(pk=resource_id)
            accessible_cases = CaseService.get_accessible_cases_for_expert(patient, user)
            selected_case = self._get_explicit_selected_case(patient, user)
            storage_doctor_id = int((selected_case or {}).get("doctor_id") or user.id)
            policy_payload = build_canvas_policy_payload(
                getattr(getattr(user, "expert_profession", None), "slug", None),
                viewer="expert",
                form_definitions=ALL_FORMS_LIST,
            )
            allowed_form_keys = policy_payload["allowed_form_keys"]
            available_forms = filter_form_definitions(ALL_FORMS_LIST, getattr(getattr(user, "expert_profession", None), "slug", None))
            selected_case_payload = (
                sanitize_expert_case_payload(
                    self._build_case_payload(patient, storage_doctor_id, selected_case["id"]),
                    getattr(getattr(user, "expert_profession", None), "slug", None),
                    allowed_form_keys,
                )
                if selected_case
                else None
            )
            return {
                "is_active": True,
                "active_view": "CASES" if selected_case else "BASE",
                "active_tab": policy_payload["visible_tabs"][0] if selected_case and policy_payload["visible_tabs"] else "CASE_OVERVIEW",
                "patient_profile": CaseService.build_patient_profile(patient),
                "base_profile": {
                    "form": CaseService.get_latest_base_profile_entry(patient).data if CaseService.get_latest_base_profile_entry(patient) else {},
                    "forms": sanitize_expert_case_payload(
                        {
                            "forms": CaseService.get_visible_form_entries(patient, viewer_role="EXPERT", viewer_doctor_id=storage_doctor_id),
                            "tests": CaseService.get_visible_tests(patient, viewer_role="EXPERT", viewer_doctor_id=storage_doctor_id),
                        },
                        getattr(getattr(user, "expert_profession", None), "slug", None),
                        allowed_form_keys,
                    )["forms"],
                    "tests": sanitize_expert_case_payload(
                        {
                            "forms": [],
                            "tests": CaseService.get_visible_tests(patient, viewer_role="EXPERT", viewer_doctor_id=storage_doctor_id),
                        },
                        getattr(getattr(user, "expert_profession", None), "slug", None),
                        allowed_form_keys,
                    )["tests"],
                },
                "cases": accessible_cases,
                "selected_case_id": selected_case["id"] if selected_case else None,
                "selected_case": {
                    "id": selected_case["id"],
                    "title": selected_case["title"],
                    "doctor_id": selected_case.get("doctor_id"),
                    "doctor_name": selected_case.get("doctor_name"),
                    "doctor_profession_slug": selected_case.get("doctor_profession_slug"),
                    "doctor_profession_label": selected_case.get("doctor_profession_label"),
                    "can_edit": selected_case.get("can_edit", True),
                    "is_read_only": selected_case.get("is_read_only", False),
                    **policy_payload,
                    **selected_case_payload,
                } if selected_case and selected_case_payload else None,
                "tests_catalog": filter_tests_catalog(TEST_CATALOG, getattr(getattr(user, "expert_profession", None), "slug", None)),
                "available_forms": available_forms,
                **policy_payload,
                "ui_signal": None,
            }
        except Exception as exc:
            logger.error("Expert canvas hydration failed: %s", exc, exc_info=True)
            return None

    def get_default_canvases(self) -> List[str]:
        return ["VANIA_PATIENT_MANAGER"]
