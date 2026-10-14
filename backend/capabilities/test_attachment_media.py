import json
import mimetypes
import os
from typing import Any, Dict, Optional

from django.core.files.storage import default_storage

from agno.media import File as AgnoFile
from agno.media import Image
from agno.tools.function import ToolResult


def build_test_attachment_tool_result(
    test: Dict[str, Any],
    *,
    include_result_text: bool = True,
    attachment_id: Optional[str] = None,
) -> ToolResult:
    attachments = test.get("attachments", []) or []
    if attachment_id:
        attachments = [
            item
            for item in attachments
            if isinstance(item, dict) and str(item.get("id") or "") == str(attachment_id)
        ]
    images = []
    files = []
    attachment_items = []
    extracted_attachments = []
    loaded_attachment_ids = []
    unloaded_attachments = []

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        file_name = attachment.get("file_name") or "attachment"
        content_type = attachment.get("content_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        storage_path = attachment.get("file_path")
        extension = os.path.splitext(file_name)[1].lower().lstrip(".")

        attachment_items.append(
            {
                "id": attachment.get("id"),
                "file_name": file_name,
                "content_type": content_type,
                "file_uploaded_at": attachment.get("file_uploaded_at"),
                "loaded_into_context": bool(storage_path and default_storage.exists(storage_path)),
            }
        )

        if not storage_path or not default_storage.exists(storage_path):
            unloaded_attachments.append(
                {
                    "id": attachment.get("id"),
                    "file_name": file_name,
                    "content_type": content_type,
                    "reason": "missing_from_storage",
                }
            )
            continue

        if extension == "pdf":
            # Responses + Agno 2.3.2 turns content-backed files into a separate
            # "Take note..." turn and can lose the document contents before
            # the assistant's final response. Prefer Vania's existing local
            # extractor for text PDFs and keep the raw-file path as a fallback
            # for scanned/image-only PDFs.
            from vania_core.tests_service import ClinicalTestsService

            extracted = ClinicalTestsService.read_test_attachment_bundle(attachment)
            extracted_text = (extracted.get("extracted_text") or "").strip()
            if extracted.get("extraction_status") == "READY" and extracted_text:
                loaded_attachment_ids.append(str(attachment.get("id") or ""))
                attachment_items[-1]["inspection_mode"] = "extracted_text"
                extracted_attachments.append(
                    {
                        "id": attachment.get("id"),
                        "file_name": file_name,
                        "content_type": content_type,
                        "extraction_status": extracted.get("extraction_status"),
                        "text_stats": extracted.get("text_stats") or {},
                        "extracted_text": extracted_text,
                    }
                )
                continue

        with default_storage.open(storage_path, "rb") as f:
            content = f.read()
        loaded_attachment_ids.append(str(attachment.get("id") or ""))
        attachment_items[-1]["inspection_mode"] = "original_media"

        if content_type.startswith("image/"):
            images.append(
                Image(
                    content=content,
                    mime_type=content_type,
                    format=extension or None,
                )
            )
        else:
            files.append(
                AgnoFile(
                    content=content,
                    mime_type=content_type,
                    file_type=extension or None,
                    filename=file_name,
                    name=file_name,
                )
            )

    payload: Dict[str, Any] = {
        "id": test.get("id"),
        "title": test.get("title"),
        "url": test.get("url"),
        "catalog_id": test.get("catalog_id"),
        "case_id": test.get("case_id"),
        "attachment_count": len(attachment_items),
        "requested_attachment_id": attachment_id,
        "attachments": attachment_items,
        "loaded_attachment_ids": loaded_attachment_ids,
        "unloaded_attachments": unloaded_attachments,
        "extracted_attachments": extracted_attachments,
        "inspection_note": (
            "Attachments under extracted_attachments were read locally and their extracted_text is available "
            "for direct analysis in this run. Other attachments with loaded_into_context=true are available "
            "as original media. "
            "Do not infer the contents of attachments listed under unloaded_attachments."
        ),
    }
    if include_result_text:
        payload["result_text"] = test.get("result_text", "")

    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        images=images or None,
        files=files or None,
    )


def build_case_file_tool_result(
    file_record: Dict[str, Any],
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    file_name = (
        file_record.get("original_file_name")
        or file_record.get("name")
        or "case-file"
    )
    content_type = (
        file_record.get("content_type")
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
    storage_path = file_record.get("storage_path")
    extension = os.path.splitext(file_name)[1].lower().lstrip(".")

    image = None
    file_blob = None
    loaded_into_context = bool(storage_path and default_storage.exists(storage_path))
    unavailable_reason = None

    if loaded_into_context:
        with default_storage.open(storage_path, "rb") as f:
            content = f.read()
        if content_type.startswith("image/"):
            image = Image(
                content=content,
                mime_type=content_type,
                format=extension or None,
            )
        else:
            file_blob = AgnoFile(
                content=content,
                mime_type=content_type,
                file_type=extension or None,
                filename=file_name,
                name=file_name,
            )
    else:
        unavailable_reason = "missing_from_storage"

    base_payload: Dict[str, Any] = dict(payload or {})
    base_payload["file"] = {
        "id": file_record.get("id"),
        "name": file_record.get("name"),
        "original_file_name": file_name,
        "description": file_record.get("description") or "",
        "content_type": content_type,
        "file_extension": file_record.get("file_extension") or (f".{extension}" if extension else ""),
        "uploaded_at": file_record.get("uploaded_at"),
        "extraction_status": file_record.get("extraction_status"),
        "text_stats": file_record.get("text_stats") or {},
        "loaded_into_context": loaded_into_context,
        "unavailable_reason": unavailable_reason,
    }
    base_payload["inspection_note"] = (
        "Only files with loaded_into_context=true are available for direct inspection in this run. "
        "Do not infer the contents of files that could not be loaded from storage."
    )

    return ToolResult(
        content=json.dumps(base_payload, ensure_ascii=False, indent=2),
        images=[image] if image else None,
        files=[file_blob] if file_blob else None,
    )


def case_file_is_loadable(file_record: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(file_record, dict):
        return False
    storage_path = file_record.get("storage_path")
    return bool(storage_path and default_storage.exists(storage_path))


def test_has_loadable_attachments(test: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(test, dict):
        return False
    for attachment in test.get("attachments", []) or []:
        if not isinstance(attachment, dict):
            continue
        storage_path = attachment.get("file_path")
        if storage_path and default_storage.exists(storage_path):
            return True
    return False
