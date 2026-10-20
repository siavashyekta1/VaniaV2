import base64
import mimetypes
import os
import re
import unicodedata
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core.files.storage import default_storage

from openai import OpenAI
from core.ai_provider import get_openai_transport_kwargs
from pypdf import PdfReader

from users.models import ContextDefinition, UserContextEntry
from users.services import user_context_manager
from users.roles import is_expert
from .case_service import build_case_scoped_key

try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

try:
    import fitz  # type: ignore
except Exception:
    fitz = None

class CaseFilesService:
    METADATA_KEY = "case_files"
    CONTENT_KEY = "case_file_content"

    SUPPORTED_UPLOAD_EXTENSIONS = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".txt",
        ".docx",
        ".doc",
    }
    READABLE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".docx"}
    MAX_EXCERPT_CHARS = 600
    MAX_SEARCH_EXCERPT_CHARS = 260
    MAX_CHUNK_CHARS = 1400

    @staticmethod
    def _metadata_context_key(doctor_id: int, case_id: str) -> str:
        return build_case_scoped_key(CaseFilesService.METADATA_KEY, doctor_id, case_id)

    @staticmethod
    def _content_context_key(doctor_id: int, case_id: str, file_id: str) -> str:
        compact_id = (file_id or "").replace("-", "")[:12]
        return build_case_scoped_key(f"{CaseFilesService.CONTENT_KEY}_{compact_id}", doctor_id, case_id)

    @staticmethod
    def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(record or {})
        payload["description"] = payload.get("description") or ""
        payload["content_type"] = payload.get("content_type") or mimetypes.guess_type(payload.get("original_file_name") or "")[0] or "application/octet-stream"
        payload["file_extension"] = (payload.get("file_extension") or os.path.splitext(payload.get("original_file_name") or "")[1].lower() or "").lower()
        payload["extraction_status"] = payload.get("extraction_status") or "PENDING"
        text_stats = payload.get("text_stats") if isinstance(payload.get("text_stats"), dict) else {}
        payload["text_stats"] = {
            "readable": bool(text_stats.get("readable")),
            "total_chars": int(text_stats.get("total_chars") or 0),
            "total_chunks": int(text_stats.get("total_chunks") or 0),
            "total_pages": int(text_stats.get("total_pages") or 0),
        }
        return payload

    @staticmethod
    def get_files(patient, doctor_id: int, case_id: str) -> List[Dict[str, Any]]:
        entry = user_context_manager.get_context(patient, CaseFilesService._metadata_context_key(int(doctor_id), case_id))
        if not entry or not isinstance(entry.data, dict):
            return []
        records = entry.data.get("files", [])
        if not isinstance(records, list):
            return []
        normalized = [CaseFilesService._normalize_record(item) for item in records if isinstance(item, dict)]
        normalized.sort(key=lambda item: item.get("uploaded_at", ""), reverse=True)
        return normalized

    @staticmethod
    def save_files(patient, doctor_id: int, case_id: str, files: List[Dict[str, Any]], creator=None, source=UserContextEntry.SourceType.USER):
        user_context_manager.set_singleton_context(
            user=patient,
            key=CaseFilesService._metadata_context_key(int(doctor_id), case_id),
            data={"files": files},
            source=source,
            creator=creator,
        )

    @staticmethod
    def _get_content_entry(patient, doctor_id: int, case_id: str, file_id: str):
        return user_context_manager.get_context(patient, CaseFilesService._content_context_key(int(doctor_id), case_id, file_id))

    @staticmethod
    def get_file_content(patient, doctor_id: int, case_id: str, file_id: str) -> Dict[str, Any]:
        entry = CaseFilesService._get_content_entry(patient, int(doctor_id), case_id, file_id)
        if entry and isinstance(entry.data, dict):
            return entry.data
        return {"chunks": [], "pages": []}

    @staticmethod
    def save_file_content(patient, doctor_id: int, case_id: str, file_id: str, data: Dict[str, Any], creator=None, source=UserContextEntry.SourceType.SYSTEM):
        user_context_manager.set_singleton_context(
            user=patient,
            key=CaseFilesService._content_context_key(int(doctor_id), case_id, file_id),
            data=data,
            source=source,
            creator=creator,
        )

    @staticmethod
    def _archive_context_key(patient, key: str):
        definition = ContextDefinition.objects.filter(key=key).first()
        if not definition:
            return
        UserContextEntry.objects.filter(user=patient, definition=definition, is_active=True).update(is_active=False)

    @staticmethod
    def get_file(patient, doctor_id: int, case_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in CaseFilesService.get_files(patient, doctor_id, case_id) if item.get("id") == file_id), None)

    @staticmethod
    def _store_upload(patient, uploaded_file) -> str:
        extension = os.path.splitext(uploaded_file.name or "")[1].lower()
        safe_name = f"{uuid.uuid4().hex}{extension}"
        relative_path = os.path.join("case_files", str(patient.id), safe_name)
        return default_storage.save(relative_path, uploaded_file)

    @staticmethod
    def create_file(patient, created_by, doctor_id: int, case_id: str, uploaded_file, name: str, description: str = "") -> Dict[str, Any]:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("File name is required.")
        extension = os.path.splitext(uploaded_file.name or "")[1].lower()
        if extension not in CaseFilesService.SUPPORTED_UPLOAD_EXTENSIONS:
            raise ValueError("Unsupported file type.")

        saved_path = CaseFilesService._store_upload(patient, uploaded_file)
        now_iso = datetime.now().isoformat()
        content_type = mimetypes.guess_type(uploaded_file.name or "")[0] or "application/octet-stream"
        file_id = uuid.uuid4().hex
        metadata = CaseFilesService._normalize_record({
            "id": file_id,
            "name": cleaned_name,
            "description": (description or "").strip(),
            "original_file_name": uploaded_file.name,
            "storage_path": saved_path,
            "content_type": content_type,
            "size_bytes": getattr(uploaded_file, "size", 0) or 0,
            "uploaded_at": now_iso,
            "uploaded_by_user_id": int(getattr(created_by, "id", 0) or 0),
            "uploaded_by_role": "EXPERT" if is_expert(created_by) else "VISITOR",
            "case_id": case_id,
            "doctor_id": int(doctor_id),
            "file_extension": extension,
            "extraction_status": "PENDING",
            "text_stats": {},
        })

        extracted = CaseFilesService.extract_file(saved_path, uploaded_file.name, content_type)
        metadata["extraction_status"] = extracted["status"]
        metadata["text_stats"] = extracted["text_stats"]

        files = CaseFilesService.get_files(patient, doctor_id, case_id)
        files.insert(0, metadata)
        CaseFilesService.save_files(patient, doctor_id, case_id, files, creator=created_by)
        CaseFilesService.save_file_content(patient, doctor_id, case_id, file_id, extracted["content"], creator=created_by)
        return metadata

    @staticmethod
    def delete_file(patient, created_by, doctor_id: int, case_id: str, file_id: str) -> bool:
        files = CaseFilesService.get_files(patient, doctor_id, case_id)
        remaining = []
        removed = None
        for item in files:
            if item.get("id") == file_id:
                removed = item
            else:
                remaining.append(item)
        if removed is None:
            return False
        storage_path = removed.get("storage_path")
        if storage_path and default_storage.exists(storage_path):
            default_storage.delete(storage_path)
        CaseFilesService.save_files(patient, doctor_id, case_id, remaining, creator=created_by)
        CaseFilesService._archive_context_key(patient, CaseFilesService._content_context_key(doctor_id, case_id, file_id))
        return True

    @staticmethod
    def list_files(
        patient,
        doctor_id: int,
        case_id: str,
        page: int = 1,
        page_size: int = 10,
        query: Optional[str] = None,
        file_type: Optional[str] = None,
        readable_only: bool = False,
        sort: str = "recent",
    ) -> Dict[str, Any]:
        records = CaseFilesService.get_files(patient, doctor_id, case_id)
        q = (query or "").strip().lower()
        if q:
            records = [
                item for item in records
                if q in (item.get("name") or "").lower()
                or q in (item.get("description") or "").lower()
                or q in (item.get("original_file_name") or "").lower()
            ]
        if file_type:
            ft = file_type.strip().lower()
            records = [item for item in records if (item.get("file_extension") or "").lower().lstrip(".") == ft.lstrip(".")]
        if readable_only:
            records = [item for item in records if item.get("text_stats", {}).get("readable")]
        if sort == "name":
            records.sort(key=lambda item: (item.get("name") or "").lower())
        elif sort == "oldest":
            records.sort(key=lambda item: item.get("uploaded_at", ""))
        else:
            records.sort(key=lambda item: item.get("uploaded_at", ""), reverse=True)

        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, min(int(page_size or 10), 50))
        total = len(records)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        return {
            "items": records[start:end],
            "pagination": {
                "page": safe_page,
                "page_size": safe_page_size,
                "total": total,
                "total_pages": max(1, (total + safe_page_size - 1) // safe_page_size) if total else 1,
            },
        }

    @staticmethod
    def get_file_details(patient, doctor_id: int, case_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        record = CaseFilesService.get_file(patient, doctor_id, case_id, file_id)
        if not record:
            return None
        content = CaseFilesService.get_file_content(patient, doctor_id, case_id, file_id)
        return {
            **record,
            "page_count": len(content.get("pages", []) or []),
            "chunk_count": len(content.get("chunks", []) or []),
            "readable": bool(record.get("text_stats", {}).get("readable")),
        }

    @staticmethod
    def search_files(patient, doctor_id: int, case_id: str, query: str, page: int = 1, page_size: int = 5, file_id: Optional[str] = None) -> Dict[str, Any]:
        q = (query or "").strip().lower()
        if not q:
            return {"items": [], "pagination": {"page": 1, "page_size": page_size, "total": 0, "total_pages": 1}}

        records = CaseFilesService.get_files(patient, doctor_id, case_id)
        if file_id:
            records = [item for item in records if item.get("id") == file_id]

        matches: List[Dict[str, Any]] = []
        for record in records:
            content = CaseFilesService.get_file_content(patient, doctor_id, case_id, record["id"])
            for chunk in content.get("chunks", []) or []:
                text = (chunk.get("text") or "").strip()
                idx = text.lower().find(q)
                if idx < 0:
                    continue
                excerpt = CaseFilesService._bounded_excerpt(text, idx, CaseFilesService.MAX_SEARCH_EXCERPT_CHARS)
                matches.append({
                    "file_id": record["id"],
                    "file_name": record.get("name"),
                    "original_file_name": record.get("original_file_name"),
                    "chunk_index": chunk.get("chunk_index"),
                    "page_number": chunk.get("page_number"),
                    "excerpt": excerpt,
                })

        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, min(int(page_size or 5), 20))
        total = len(matches)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        return {
            "items": matches[start:end],
            "pagination": {
                "page": safe_page,
                "page_size": safe_page_size,
                "total": total,
                "total_pages": max(1, (total + safe_page_size - 1) // safe_page_size) if total else 1,
            },
        }

    @staticmethod
    def read_file(
        patient,
        doctor_id: int,
        case_id: str,
        file_id: str,
        mode: str = "excerpt",
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        chunk_start: Optional[int] = None,
        chunk_count: int = 3,
        query: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        record = CaseFilesService.get_file(patient, doctor_id, case_id, file_id)
        if not record:
            return None
        content = CaseFilesService.get_file_content(patient, doctor_id, case_id, file_id)
        chunks = content.get("chunks", []) or []
        pages = content.get("pages", []) or []

        if query:
            results = CaseFilesService.search_files(patient, doctor_id, case_id, query, page=1, page_size=max(1, min(chunk_count, 5)), file_id=file_id)
            return {
                "file": CaseFilesService.get_file_details(patient, doctor_id, case_id, file_id),
                "mode": "search",
                "results": results["items"],
                "total_pages": len(pages),
                "total_chunks": len(chunks),
            }

        if page is not None:
            safe_page_size = max(1, min(int(page_size or 1), 5))
            start_page = max(1, int(page))
            selected_pages = [item for item in pages if start_page <= int(item.get("page_number") or 0) < start_page + safe_page_size]
            return {
                "file": CaseFilesService.get_file_details(patient, doctor_id, case_id, file_id),
                "mode": "page",
                "pages": selected_pages,
                "total_pages": len(pages),
                "total_chunks": len(chunks),
            }

        safe_chunk_start = max(0, int(chunk_start or 0))
        safe_chunk_count = max(1, min(int(chunk_count or 3), 5))
        selected_chunks = chunks[safe_chunk_start:safe_chunk_start + safe_chunk_count]
        if mode == "excerpt":
            selected_chunks = [
                {
                    **item,
                    "text": CaseFilesService._truncate_text(item.get("text") or "", CaseFilesService.MAX_EXCERPT_CHARS),
                }
                for item in selected_chunks
            ]
        return {
            "file": CaseFilesService.get_file_details(patient, doctor_id, case_id, file_id),
            "mode": mode,
            "chunks": selected_chunks,
            "total_pages": len(pages),
            "total_chunks": len(chunks),
        }

    @staticmethod
    def extract_file(storage_path: str, original_file_name: str, content_type: Optional[str] = None) -> Dict[str, Any]:
        extension = os.path.splitext(original_file_name or "")[1].lower()
        if extension not in CaseFilesService.READABLE_EXTENSIONS:
            return {
                "status": "UNSUPPORTED",
                "text_stats": {"readable": False, "total_chars": 0, "total_chunks": 0, "total_pages": 0},
                "content": {"chunks": [], "pages": []},
            }

        try:
            if extension == ".pdf":
                pages = CaseFilesService._extract_pdf_pages(storage_path)
            elif extension in {".png", ".jpg", ".jpeg", ".webp"}:
                image_text = CaseFilesService._extract_image_text(storage_path, content_type)
                pages = [{"page_number": 1, "text": image_text}] if image_text else []
            elif extension == ".txt":
                pages = [{"page_number": 1, "text": CaseFilesService._extract_txt_text(storage_path)}]
            elif extension == ".docx":
                pages = [{"page_number": 1, "text": CaseFilesService._extract_docx_text(storage_path)}]
            else:
                pages = []
        except Exception:
            pages = []

        clean_pages = []
        for item in pages:
            text = CaseFilesService._sanitize_text(item.get("text") or "")
            if text:
                clean_pages.append({"page_number": int(item.get("page_number") or len(clean_pages) + 1), "text": text})

        if not clean_pages:
            return {
                "status": "FAILED",
                "text_stats": {"readable": True, "total_chars": 0, "total_chunks": 0, "total_pages": 0},
                "content": {"chunks": [], "pages": []},
            }

        chunks = CaseFilesService._chunk_pages(clean_pages)
        return {
            "status": "READY",
            "text_stats": {
                "readable": True,
                "total_chars": sum(len(item.get("text") or "") for item in clean_pages),
                "total_chunks": len(chunks),
                "total_pages": len(clean_pages),
            },
            "content": {"pages": clean_pages, "chunks": chunks},
        }

    @staticmethod
    def _extract_pdf_pages(storage_path: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            with default_storage.open(storage_path, "rb") as f:
                reader = PdfReader(f)
                for index, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    results.append({"page_number": index, "text": text})
        except Exception:
            results = []

        if CaseFilesService._page_list_has_quality(results):
            return results

        if pdfplumber is not None:
            try:
                with default_storage.open(storage_path, "rb") as f:
                    candidate = []
                    with pdfplumber.open(f) as pdf:
                        for index, page in enumerate(pdf.pages, start=1):
                            candidate.append({"page_number": index, "text": page.extract_text() or ""})
                    if CaseFilesService._page_list_has_quality(candidate):
                        return candidate
            except Exception:
                pass

        if fitz is not None:
            try:
                with default_storage.open(storage_path, "rb") as f:
                    data = f.read()
                doc = fitz.open(stream=data, filetype="pdf")
                try:
                    candidate = []
                    for index, page in enumerate(doc, start=1):
                        candidate.append({"page_number": index, "text": page.get_text("text") or ""})
                    if CaseFilesService._page_list_has_quality(candidate):
                        return candidate
                finally:
                    doc.close()
            except Exception:
                pass

        return results

    @staticmethod
    def _extract_txt_text(storage_path: str) -> str:
        with default_storage.open(storage_path, "rb") as f:
            data = f.read()
        for encoding in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
            try:
                return data.decode(encoding)
            except Exception:
                continue
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_docx_text(storage_path: str) -> str:
        with default_storage.open(storage_path, "rb") as f:
            data = f.read()
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                xml_bytes = archive.read("word/document.xml")
        except Exception:
            return ""

        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return ""

        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: List[str] = []
        for para in root.findall(".//w:p", namespace):
            texts = [node.text or "" for node in para.findall(".//w:t", namespace)]
            joined = "".join(texts).strip()
            if joined:
                paragraphs.append(joined)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _extract_image_text(storage_path: str, content_type: Optional[str] = None) -> str:
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
                            {"type": "text", "text": "متن و اعداد موجود در این تصویر را استخراج کن."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                        ],
                    },
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            return (content or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _page_list_has_quality(pages: List[Dict[str, Any]]) -> bool:
        combined = "\n".join(item.get("text") or "" for item in pages)
        cleaned = CaseFilesService._sanitize_text(combined)
        if len(cleaned) < 80:
            return False
        meaningful = len(re.findall(r"[\u0600-\u06FFA-Za-z0-9]", cleaned))
        total_non_space = len(re.sub(r"\s+", "", cleaned))
        return total_non_space > 0 and meaningful / total_non_space >= 0.3

    @staticmethod
    def _sanitize_text(text: str) -> str:
        if not text:
            return ""
        normalized = unicodedata.normalize("NFC", text)
        for ch in ["\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff", "\u2066", "\u2067", "\u2068", "\u2069"]:
            normalized = normalized.replace(ch, " ")
        normalized = "".join(ch for ch in normalized if ch == "\n" or ch == "\t" or ord(ch) >= 32)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in normalized.split("\n")]
        cleaned = "\n".join(line for line in lines if line)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _chunk_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        for page in pages:
            page_number = int(page.get("page_number") or 1)
            paragraphs = [item.strip() for item in (page.get("text") or "").split("\n\n") if item.strip()]
            if not paragraphs:
                continue
            current = ""
            for para in paragraphs:
                next_text = f"{current}\n\n{para}".strip() if current else para
                if len(next_text) <= CaseFilesService.MAX_CHUNK_CHARS:
                    current = next_text
                    continue
                if current:
                    chunks.append({"chunk_index": len(chunks), "page_number": page_number, "text": current})
                if len(para) <= CaseFilesService.MAX_CHUNK_CHARS:
                    current = para
                    continue
                start = 0
                while start < len(para):
                    piece = para[start:start + CaseFilesService.MAX_CHUNK_CHARS]
                    chunks.append({"chunk_index": len(chunks), "page_number": page_number, "text": piece.strip()})
                    start += CaseFilesService.MAX_CHUNK_CHARS
                current = ""
            if current:
                chunks.append({"chunk_index": len(chunks), "page_number": page_number, "text": current})
        return chunks

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        clean = (text or "").strip()
        if len(clean) <= limit:
            return clean
        return clean[:limit].rstrip() + "..."

    @staticmethod
    def _bounded_excerpt(text: str, match_index: int, window: int) -> str:
        start = max(0, match_index - window // 3)
        end = min(len(text), match_index + (window * 2 // 3))
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        return snippet
