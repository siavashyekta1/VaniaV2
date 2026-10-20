import os
import uuid
import logging
import re
import unicodedata
import base64
import mimetypes
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from openai import OpenAI
from core.ai_provider import get_openai_transport_kwargs
from pypdf import PdfReader

from users.models import UserContextEntry
from users.services import user_context_manager
from .tests_catalog import TEST_CATALOG, TEST_CATALOG_BY_ID
from .context_scope import migrate_legacy_to_scoped_once, migrate_doctor_scoped_to_case_once, build_scoped_key
from .case_service import build_case_scoped_key

logger = logging.getLogger(__name__)

# Optional extractors for better Persian PDF handling.
try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

try:
    import fitz  # type: ignore
except Exception:
    fitz = None


class ClinicalTestsService:
    CONTEXT_KEY = "clinical_tests"
    MAX_EXTRACTED_TEXT_CHARS = 24000
    ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    ATTACHMENT_EXCERPT_CHARS = 600

    @staticmethod
    def list_catalog() -> List[Dict[str, Any]]:
        return TEST_CATALOG

    @staticmethod
    def get_interactive_attempt(patient, clinical_test_id: str):
        if not clinical_test_id:
            return None
        from .models import EsanjTestAttempt

        return (
            EsanjTestAttempt.objects.filter(user=patient, clinical_test_id=clinical_test_id)
            .order_by("-started_at")
            .first()
        )

    @staticmethod
    def summarize_interactive_attempt(attempt) -> str:
        if not attempt:
            return ""
        if attempt.status != getattr(attempt.Status, "COMPLETED", "COMPLETED"):
            return ""
        result = {
            "test_title": attempt.test_title,
            "status": attempt.status,
            "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
            "result": attempt.result_json or {},
            "grading": attempt.grading_json or {},
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    @staticmethod
    def _sync_interactive_attempt_fields(patient, test: Dict[str, Any]) -> Dict[str, Any]:
        if test.get("source") != "interactive":
            return test
        attempt = ClinicalTestsService.get_interactive_attempt(patient, test.get("id") or "")
        if not attempt:
            test.setdefault("interactive_status", "ASSIGNED")
            test.setdefault("interactive_attempt_id", None)
            test.setdefault("completed_at", None)
            return test

        test["interactive_attempt_id"] = str(attempt.id)
        test["interactive_status"] = attempt.status
        test["completed_at"] = attempt.completed_at.isoformat() if attempt.completed_at else None
        if attempt.status == attempt.Status.COMPLETED:
            summary = ClinicalTestsService.summarize_interactive_attempt(attempt)
            test["result_text"] = summary
            test["result_summary"] = summary
        return test

    @staticmethod
    def _get_state(patient, doctor_id=None, case_id=None) -> Dict[str, Any]:
        if doctor_id and case_id:
            entry = migrate_doctor_scoped_to_case_once(
                patient=patient,
                doctor_id=doctor_id,
                case_id=case_id,
                base_key=ClinicalTestsService.CONTEXT_KEY,
                default_factory=lambda: {"tests": []},
            )
        elif doctor_id:
            entry = migrate_legacy_to_scoped_once(
                patient=patient,
                doctor_id=doctor_id,
                base_key=ClinicalTestsService.CONTEXT_KEY,
                default_factory=lambda: {"tests": []},
            )
        else:
            entry = user_context_manager.get_context(patient, ClinicalTestsService.CONTEXT_KEY)
        if entry and isinstance(entry.data, dict):
            tests = entry.data.get("tests", [])
            if isinstance(tests, list):
                return {"tests": tests}
        return {"tests": []}

    @staticmethod
    def get_tests(patient, doctor_id=None, case_id=None) -> List[Dict[str, Any]]:
        state = ClinicalTestsService._get_state(patient, doctor_id=doctor_id, case_id=case_id)
        tests = state.get("tests", [])
        for test in tests:
            ClinicalTestsService._normalize_test_record(test)
            ClinicalTestsService._sync_interactive_attempt_fields(patient, test)
        tests.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return tests

    @staticmethod
    def save_tests(patient, tests: List[Dict[str, Any]], source=UserContextEntry.SourceType.USER, creator=None, doctor_id=None, case_id=None):
        key = build_case_scoped_key(ClinicalTestsService.CONTEXT_KEY, doctor_id, case_id) if doctor_id and case_id else build_scoped_key(ClinicalTestsService.CONTEXT_KEY, doctor_id) if doctor_id else ClinicalTestsService.CONTEXT_KEY
        user_context_manager.set_singleton_context(
            user=patient,
            key=key,
            data={"tests": tests},
            source=source,
            creator=creator,
        )

    @staticmethod
    def add_test(
        patient,
        created_by,
        catalog_id: Optional[int] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        result_summary: Optional[str] = None,
        doctor_id=None,
        case_id=None,
        source: Optional[str] = None,
        interactive_test_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        now_iso = datetime.now().isoformat()

        normalized_source = "interactive" if source == "interactive" or interactive_test_id else "manual"
        resolved_title = title or ""
        resolved_url = url or ""
        if normalized_source == "interactive":
            catalog_id = None
            resolved_url = ""
            result_summary = ""
        elif catalog_id:
            item = TEST_CATALOG_BY_ID.get(int(catalog_id))
            if item:
                resolved_title = item["title"]
                resolved_url = item["url"]

        new_test = {
            "id": str(uuid.uuid4()),
            "source": normalized_source,
            "catalog_id": int(catalog_id) if catalog_id else None,
            "interactive_test_id": int(interactive_test_id) if interactive_test_id else None,
            "interactive_status": "ASSIGNED" if normalized_source == "interactive" else None,
            "interactive_attempt_id": None,
            "assigned_to_user_id": int(getattr(patient, "id", 0) or 0) if normalized_source == "interactive" else None,
            "assigned_by_user_id": int(getattr(created_by, "id", 0) or 0) if normalized_source == "interactive" else None,
            "completed_at": None,
            "title": resolved_title,
            "url": resolved_url,
            "result_text": (result_summary or "").strip(),
            "result_summary": (result_summary or "").strip(),
            "attachments": [],
            "file_path": None,
            "file_name": None,
            "file_uploaded_at": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "submitted_by_doctor_id": int(doctor_id or getattr(created_by, "id", 0) or 0),
            "case_id": case_id,
        }
        tests.insert(0, new_test)
        ClinicalTestsService.save_tests(patient, tests, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return new_test

    @staticmethod
    def update_test(patient, created_by, test_id: str, payload: Dict[str, Any], doctor_id=None, case_id=None) -> Optional[Dict[str, Any]]:
        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        updated = None
        for test in tests:
            if test.get("id") == test_id:
                for key in ["title", "url", "result_text", "result_summary"]:
                    if test.get("source") == "interactive" and key in {"result_text", "result_summary"}:
                        continue
                    if key in payload:
                        if key in {"result_text", "result_summary"}:
                            next_text = (payload.get(key) or "").strip()
                            test["result_text"] = next_text
                            test["result_summary"] = next_text
                        else:
                            test[key] = payload.get(key)
                if "catalog_id" in payload:
                    catalog_id = payload.get("catalog_id")
                    test["catalog_id"] = int(catalog_id) if catalog_id else None
                    if catalog_id and int(catalog_id) in TEST_CATALOG_BY_ID:
                        item = TEST_CATALOG_BY_ID[int(catalog_id)]
                        test["title"] = item["title"]
                        test["url"] = item["url"]
                for key in ["interactive_status", "interactive_attempt_id", "completed_at"]:
                    if key in payload and test.get("source") == "interactive":
                        test[key] = payload.get(key)
                test["updated_at"] = datetime.now().isoformat()
                ClinicalTestsService._normalize_test_record(test)
                updated = test
                break

        if updated is None:
            return None

        ClinicalTestsService.save_tests(patient, tests, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return updated

    @staticmethod
    def _delete_file_if_exists(file_path: Optional[str]):
        if file_path and default_storage.exists(file_path):
            default_storage.delete(file_path)

    @staticmethod
    def _save_attachment_copy(patient, source_name: str, content_bytes: bytes) -> str:
        extension = os.path.splitext(source_name or "")[1].lower()
        if extension not in ClinicalTestsService.ALLOWED_ATTACHMENT_EXTENSIONS:
            raise ValueError("Unsupported file type.")
        safe_name = f"{uuid.uuid4().hex}{extension}"
        relative_path = os.path.join("clinical_tests", str(patient.id), safe_name)
        return default_storage.save(relative_path, ContentFile(content_bytes))

    @staticmethod
    def delete_test(patient, created_by, test_id: str, doctor_id=None, case_id=None) -> bool:
        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        filtered = []
        removed = False
        for test in tests:
            if test.get("id") == test_id:
                removed = True
                attachments = test.get("attachments", [])
                if isinstance(attachments, list):
                    for attachment in attachments:
                        if isinstance(attachment, dict):
                            ClinicalTestsService._delete_file_if_exists(attachment.get("file_path"))
                ClinicalTestsService._delete_file_if_exists(test.get("file_path"))
                continue
            filtered.append(test)

        if not removed:
            return False

        ClinicalTestsService.save_tests(patient, filtered, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return True

    @staticmethod
    def attach_test_file(
        patient,
        created_by,
        test_id: str,
        uploaded_file,
        doctor_id=None,
        case_id=None,
    ) -> Optional[Dict[str, Any]]:
        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        target = None
        for test in tests:
            if test.get("id") == test_id:
                target = test
                break

        if target is None:
            return None

        saved_path = ClinicalTestsService._save_attachment_copy(patient, uploaded_file.name or "", uploaded_file.read())

        attachments = target.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        attachments.append({
            "id": str(uuid.uuid4()),
            "file_path": saved_path,
            "file_name": uploaded_file.name,
            "file_uploaded_at": datetime.now().isoformat(),
            "content_type": mimetypes.guess_type(uploaded_file.name)[0] or "application/octet-stream",
        })
        target["attachments"] = attachments
        target["updated_at"] = datetime.now().isoformat()
        ClinicalTestsService._normalize_test_record(target)

        ClinicalTestsService.save_tests(patient, tests, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return target

    @staticmethod
    def attach_case_file(
        patient,
        created_by,
        test_id: str,
        file_id: str,
        doctor_id=None,
        case_id=None,
    ) -> Optional[Dict[str, Any]]:
        from .case_files_service import CaseFilesService

        case_file = CaseFilesService.get_file(patient, int(doctor_id), case_id, file_id) if doctor_id and case_id else None
        if not case_file:
            return None

        storage_path = case_file.get("storage_path")
        source_name = case_file.get("original_file_name") or case_file.get("name") or ""
        if not storage_path or not source_name or not default_storage.exists(storage_path):
            return None

        with default_storage.open(storage_path, "rb") as existing_file:
            file_bytes = existing_file.read()

        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        target = next((test for test in tests if test.get("id") == test_id), None)
        if target is None:
            return None

        saved_path = ClinicalTestsService._save_attachment_copy(patient, source_name, file_bytes)
        attachments = target.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        attachments.append({
            "id": str(uuid.uuid4()),
            "file_path": saved_path,
            "file_name": source_name,
            "file_uploaded_at": datetime.now().isoformat(),
            "content_type": case_file.get("content_type") or mimetypes.guess_type(source_name)[0] or "application/octet-stream",
        })
        target["attachments"] = attachments
        target["updated_at"] = datetime.now().isoformat()
        ClinicalTestsService._normalize_test_record(target)
        ClinicalTestsService.save_tests(patient, tests, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return target

    @staticmethod
    def remove_test_file(patient, created_by, test_id: str, attachment_id: Optional[str] = None, doctor_id=None, case_id=None) -> bool:
        tests = ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id)
        changed = False
        for test in tests:
            if test.get("id") == test_id:
                attachments = test.get("attachments", [])
                if not isinstance(attachments, list):
                    attachments = []
                target_attachment = None
                if attachment_id:
                    target_attachment = next((item for item in attachments if item.get("id") == attachment_id), None)
                elif attachments:
                    target_attachment = attachments[0]
                if target_attachment is None and test.get("file_path"):
                    target_attachment = {
                        "file_path": test.get("file_path"),
                        "file_name": test.get("file_name"),
                    }
                if target_attachment is None:
                    return False
                ClinicalTestsService._delete_file_if_exists(target_attachment.get("file_path"))
                if attachment_id:
                    test["attachments"] = [item for item in attachments if item.get("id") != attachment_id]
                elif attachments:
                    test["attachments"] = attachments[1:]
                else:
                    test["attachments"] = []
                test["updated_at"] = datetime.now().isoformat()
                ClinicalTestsService._normalize_test_record(test)
                changed = True
                break

        if not changed:
            return False

        ClinicalTestsService.save_tests(patient, tests, creator=created_by, doctor_id=doctor_id, case_id=case_id)
        return True

    @staticmethod
    def get_test(patient, test_id: str, doctor_id=None, case_id=None) -> Optional[Dict[str, Any]]:
        for test in ClinicalTestsService.get_tests(patient, doctor_id=doctor_id, case_id=case_id):
            if test.get("id") == test_id:
                return test
        return None

    @staticmethod
    def get_test_attachment(patient, test_id: str, attachment_id: Optional[str] = None, doctor_id=None, case_id=None) -> Optional[Dict[str, Any]]:
        test = ClinicalTestsService.get_test(patient, test_id, doctor_id=doctor_id, case_id=case_id)
        if not test:
            return None
        attachments = test.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        if attachment_id:
            return next((item for item in attachments if item.get("id") == attachment_id), None)
        if attachments:
            return attachments[0]
        if test.get("file_path"):
            return {
                "id": "legacy-file",
                "file_path": test.get("file_path"),
                "file_name": test.get("file_name"),
                "file_uploaded_at": test.get("file_uploaded_at"),
                "content_type": mimetypes.guess_type(test.get("file_name") or "")[0] or "application/octet-stream",
            }
        return None

    @staticmethod
    def _normalize_test_record(test: Dict[str, Any]) -> Dict[str, Any]:
        source = test.get("source") or ("interactive" if test.get("interactive_test_id") else "manual")
        test["source"] = source
        if source == "interactive":
            test["catalog_id"] = None
            test["url"] = ""
            test["interactive_test_id"] = int(test.get("interactive_test_id") or 0) or None
            test.setdefault("interactive_status", "ASSIGNED")
            test.setdefault("interactive_attempt_id", None)
            test.setdefault("assigned_to_user_id", None)
            test.setdefault("assigned_by_user_id", None)
            test.setdefault("completed_at", None)
        attachments = test.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        normalized_attachments = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            normalized_attachments.append({
                "id": item.get("id") or str(uuid.uuid4()),
                "file_path": item.get("file_path"),
                "file_name": item.get("file_name"),
                "file_uploaded_at": item.get("file_uploaded_at"),
                "content_type": item.get("content_type") or mimetypes.guess_type(item.get("file_name") or "")[0] or "application/octet-stream",
            })
        if not normalized_attachments and test.get("file_path"):
            normalized_attachments.append({
                "id": "legacy-file",
                "file_path": test.get("file_path"),
                "file_name": test.get("file_name"),
                "file_uploaded_at": test.get("file_uploaded_at"),
                "content_type": mimetypes.guess_type(test.get("file_name") or "")[0] or "application/octet-stream",
            })
        test["attachments"] = normalized_attachments
        if "result_text" not in test:
            test["result_text"] = test.get("result_summary", "") or ""
        test["result_summary"] = test.get("result_text", "") or ""
        primary_attachment = normalized_attachments[0] if normalized_attachments else None
        test["file_path"] = primary_attachment.get("file_path") if primary_attachment else None
        test["file_name"] = primary_attachment.get("file_name") if primary_attachment else None
        test["file_uploaded_at"] = primary_attachment.get("file_uploaded_at") if primary_attachment else None
        return test

    @staticmethod
    def update_interactive_assignment_from_attempt(patient, attempt, creator=None) -> Optional[Dict[str, Any]]:
        clinical_test_id = getattr(attempt, "clinical_test_id", "") or ""
        if not clinical_test_id:
            return None
        tests = ClinicalTestsService.get_tests(patient, doctor_id=attempt.doctor_id, case_id=attempt.case_id or None)
        updated = None
        for test in tests:
            if test.get("id") != clinical_test_id:
                continue
            test["source"] = "interactive"
            test["interactive_attempt_id"] = str(attempt.id)
            test["interactive_status"] = attempt.status
            test["completed_at"] = attempt.completed_at.isoformat() if attempt.completed_at else None
            test["result_text"] = ClinicalTestsService.summarize_interactive_attempt(attempt)
            test["result_summary"] = test["result_text"]
            test["updated_at"] = datetime.now().isoformat()
            ClinicalTestsService._normalize_test_record(test)
            updated = test
            break
        if updated is None:
            return None
        ClinicalTestsService.save_tests(patient, tests, creator=creator or patient, doctor_id=attempt.doctor_id, case_id=attempt.case_id or None)
        return updated

    @staticmethod
    def read_test_result_bundle(patient, test_id: str, doctor_id=None, case_id=None) -> Optional[Dict[str, Any]]:
        test = ClinicalTestsService.get_test(patient, test_id, doctor_id=doctor_id, case_id=case_id)
        if not test:
            return None
        attachments = []
        for attachment in test.get("attachments", []) or []:
            attachments.append(ClinicalTestsService.read_test_attachment_bundle(attachment))
        payload = {
            "id": test.get("id"),
            "title": test.get("title"),
            "url": test.get("url"),
            "catalog_id": test.get("catalog_id"),
            "source": test.get("source"),
            "interactive_test_id": test.get("interactive_test_id"),
            "interactive_status": test.get("interactive_status"),
            "interactive_attempt_id": test.get("interactive_attempt_id"),
            "completed_at": test.get("completed_at"),
            "result_text": test.get("result_text", ""),
            "case_id": test.get("case_id"),
            "attachments": attachments,
        }
        if test.get("source") == "interactive":
            attempt = ClinicalTestsService.get_interactive_attempt(patient, test.get("id") or "")
            payload["interactive_result"] = {
                "status": getattr(attempt, "status", test.get("interactive_status")) if attempt else test.get("interactive_status"),
                "json": getattr(attempt, "result_json", {}) if attempt else {},
                "grading": getattr(attempt, "grading_json", {}) if attempt else {},
            }
        return payload

    @staticmethod
    def read_test_attachment_bundle(attachment: Dict[str, Any]) -> Dict[str, Any]:
        storage_path = attachment.get("file_path")
        file_name = attachment.get("file_name") or ""
        content_type = attachment.get("content_type")

        payload: Dict[str, Any] = {
            "id": attachment.get("id"),
            "file_name": file_name,
            "content_type": content_type,
            "file_uploaded_at": attachment.get("file_uploaded_at"),
            "extracted_text": "",
            "extraction_status": "FAILED",
            "text_stats": {
                "readable": False,
                "total_chars": 0,
                "total_chunks": 0,
                "total_pages": 0,
            },
            "excerpt": "",
            "pages": [],
        }

        if not storage_path or not default_storage.exists(storage_path):
            return payload

        from .case_files_service import CaseFilesService

        extracted = CaseFilesService.extract_file(storage_path, file_name, content_type)
        pages = extracted.get("content", {}).get("pages", []) or []
        combined_text = "\n\n".join(
            (item.get("text") or "").strip()
            for item in pages
            if isinstance(item, dict) and (item.get("text") or "").strip()
        ).strip()
        excerpt = combined_text[:ClinicalTestsService.ATTACHMENT_EXCERPT_CHARS].strip()

        payload.update({
            "extracted_text": combined_text[:ClinicalTestsService.MAX_EXTRACTED_TEXT_CHARS],
            "extraction_status": extracted.get("status") or "FAILED",
            "text_stats": extracted.get("text_stats") or payload["text_stats"],
            "excerpt": excerpt,
            "pages": [
                {
                    "page_number": item.get("page_number"),
                    "text": (item.get("text") or "")[:ClinicalTestsService.ATTACHMENT_EXCERPT_CHARS],
                }
                for item in pages[:3]
                if isinstance(item, dict)
            ],
        })
        return payload

    @staticmethod
    def extract_attachment_text(attachment: Dict[str, Any]) -> str:
        return ClinicalTestsService.read_test_attachment_bundle(attachment).get("extracted_text", "")

    @staticmethod
    def extract_image_text(storage_path: str, content_type: Optional[str] = None) -> str:
        api_key = getattr(settings, "OPENAI_API_KEY", None)
        if not api_key:
            return ""
        try:
            with default_storage.open(storage_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            mime_type = content_type or mimetypes.guess_type(storage_path)[0] or "image/png"
            client = OpenAI(api_key=api_key, **get_openai_transport_kwargs())
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": "متن موجود در تصویر را با دقت استخراج کن. فقط متن قابل مشاهده را برگردان و اگر متن معناداری وجود ندارد، رشته خالی برگردان.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "متن و اعداد موجود در این تصویر نتیجه تست را استخراج کن."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                        ],
                    },
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            return (content or "").strip()
        except Exception as exc:
            logger.warning("Image extraction failed for %s: %s", storage_path, exc)
            return ""

    @staticmethod
    def extract_pdf_text(storage_path: str) -> str:
        extractors = [
            ("pypdf", ClinicalTestsService._extract_with_pypdf),
            ("pdfplumber", ClinicalTestsService._extract_with_pdfplumber),
            ("fitz", ClinicalTestsService._extract_with_fitz),
        ]

        for extractor_name, extractor in extractors:
            try:
                raw_text = extractor(storage_path)
            except Exception as e:
                logger.warning(f"PDF extractor '{extractor_name}' failed for {storage_path}: {e}")
                continue

            cleaned = ClinicalTestsService._sanitize_extracted_text(raw_text)
            if ClinicalTestsService._is_text_quality_acceptable(cleaned):
                final_text = ClinicalTestsService._truncate_on_paragraph(cleaned, ClinicalTestsService.MAX_EXTRACTED_TEXT_CHARS)
                logger.info(f"PDF extraction successful via '{extractor_name}' for {storage_path}")
                return final_text
            if cleaned:
                logger.info(f"PDF extraction via '{extractor_name}' rejected due to low text quality for {storage_path}")

        logger.warning(f"All PDF extraction strategies failed or low-quality for {storage_path}")
        return ""

    @staticmethod
    def _extract_with_pypdf(storage_path: str) -> str:
        try:
            with default_storage.open(storage_path, "rb") as f:
                reader = PdfReader(f)
                text_chunks: List[str] = []
                for page in reader.pages:
                    extracted = page.extract_text() or ""
                    if extracted:
                        text_chunks.append(extracted)
            return "\n".join(text_chunks)
        except Exception:
            raise

    @staticmethod
    def _extract_with_pdfplumber(storage_path: str) -> str:
        if pdfplumber is None:
            return ""
        with default_storage.open(storage_path, "rb") as f:
            text_chunks: List[str] = []
            with pdfplumber.open(f) as pdf:
                for page in pdf.pages:
                    extracted = page.extract_text() or ""
                    if extracted:
                        text_chunks.append(extracted)
            return "\n".join(text_chunks)

    @staticmethod
    def _extract_with_fitz(storage_path: str) -> str:
        if fitz is None:
            return ""
        with default_storage.open(storage_path, "rb") as f:
            data = f.read()
        text_chunks: List[str] = []
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            for page in doc:
                extracted = page.get_text("text") or ""
                if extracted:
                    text_chunks.append(extracted)
        finally:
            doc.close()
        return "\n".join(text_chunks)

    @staticmethod
    def _sanitize_extracted_text(text: str) -> str:
        if not text:
            return ""
        normalized = unicodedata.normalize("NFC", text)

        # Strip invisible/formatting characters that frequently pollute Persian PDF extraction.
        invisible_chars = [
            "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff", "\u2066", "\u2067", "\u2068", "\u2069"
        ]
        for ch in invisible_chars:
            normalized = normalized.replace(ch, " ")

        # Remove control chars but keep line breaks/tabs.
        normalized = "".join(ch for ch in normalized if ch == "\n" or ch == "\t" or ord(ch) >= 32)

        # Normalize line endings and trim whitespace around lines.
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in normalized.split("\n")]

        filtered_lines: List[str] = []
        for line in lines:
            if not line:
                filtered_lines.append("")
                continue
            # Drop lines dominated by replacement chars or symbols.
            replacement_count = line.count("�")
            symbol_count = len(re.findall(r"[^\w\u0600-\u06FF\s]", line))
            meaningful_chars = len(re.findall(r"[\u0600-\u06FFA-Za-z0-9]", line))
            if replacement_count > max(2, len(line) // 8):
                continue
            if meaningful_chars == 0:
                continue
            if symbol_count > meaningful_chars * 3:
                continue
            filtered_lines.append(line)

        cleaned = "\n".join(filtered_lines)
        # Collapse repeated punctuation and whitespace noise.
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"([!?.،؛:])\1{2,}", r"\1", cleaned)
        return cleaned.strip()

    @staticmethod
    def _is_text_quality_acceptable(text: str) -> bool:
        if not text:
            return False
        if len(text) < 120:
            return False
        meaningful = len(re.findall(r"[\u0600-\u06FFA-Za-z0-9]", text))
        total_non_space = len(re.sub(r"\s+", "", text))
        if total_non_space == 0:
            return False
        ratio = meaningful / total_non_space
        return ratio >= 0.35

    @staticmethod
    def _truncate_on_paragraph(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        paragraphs = text.split("\n\n")
        out: List[str] = []
        current_len = 0
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            chunk_len = len(p) + (2 if out else 0)
            if current_len + chunk_len > limit:
                break
            out.append(p)
            current_len += chunk_len
        if out:
            return "\n\n".join(out).strip()
        return text[:limit].strip()

    @staticmethod
    def summarize_test_result(test_title: str, test_text: str) -> str:
        api_key = getattr(settings, "OPENAI_API_KEY", None)
        if not api_key:
            logger.warning("OPENAI_API_KEY is missing; skipping automatic test summary.")
            return ""

        try:
            client = OpenAI(api_key=api_key, **get_openai_transport_kwargs())
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": "شما یک دستیار بالینی برای روانشناس هستید. خروجی باید فارسی، حرفه‌ای، خلاصه و قابل استفاده در پرونده باشد.",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"نام تست: {test_title}\n\n"
                            "بر اساس متن نتیجه تست زیر، یک علت مراجع و مشاهدات کوتاه (حداکثر ۸ خط) بنویس. "
                            "خروجی باید شامل: یافته‌های اصلی، تفسیر محتاطانه، و نکات قابل پیگیری در جلسه بعد باشد. "
                            "از ادعاهای قطعی بدون شواهد خودداری کن.\n\n"
                            f"متن تست:\n{test_text}"
                        ),
                    },
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            return (content or "").strip()
        except Exception as e:
            logger.warning(f"Failed to generate AI summary for test: {e}")
            return ""
