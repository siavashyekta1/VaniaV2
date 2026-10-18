# backend/capabilities/vania_doctor/canvas.py
from typing import Dict, Any
from capabilities.base import BaseCanvas
from capabilities.registry import register_canvas

@register_canvas
class PatientManagerCanvas(BaseCanvas):
    """
    The main case dashboard for Experts.
    It displays the currently selected visitor's profile, session history,
    active tasks, and filled forms.
    """
    component_key = "VANIA_PATIENT_MANAGER"
    name = "مدیریت مراجع"
    slug = "vania-patient-manager-v1"
    description = "A comprehensive dashboard for managing a specific visitor's case data."

    @classmethod
    def get_default_state(cls) -> Dict[str, Any]:
        return {
            "is_active": False,
            "active_view": "BASE",
            "active_tab": "CASE_OVERVIEW",
            "patient_profile": None,
            "base_profile": {
                "form": {},
                "forms": [],
                "tests": [],
            },
            "cases": [],
            "selected_case_id": None,
            "selected_case": None,
            "tests_catalog": [],       # Static catalog used to prescribe tests
            "available_forms": [],
            "ui_signal": None,
        }

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "active_tab": {
                    "type": "string", 
                    "enum": ["CASE_OVERVIEW", "ROADMAP", "RESCUENET", "MEDICATIONS", "APPENDIX", "FILES"]
                },
                "is_active": {"type": "boolean"},
                "active_view": {"type": "string"},
                "selected_case_id": {"type": ["string", "null"]},
                "patient_profile": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "phone": {"type": "string"}
                    }
                }
            }
        }
