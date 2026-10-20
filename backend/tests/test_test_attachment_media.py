import json
from unittest import TestCase, mock

from capabilities.test_attachment_media import build_test_attachment_tool_result


class TestAttachmentMediaTests(TestCase):
    @mock.patch("vania_core.tests_service.ClinicalTestsService.read_test_attachment_bundle")
    @mock.patch("capabilities.test_attachment_media.default_storage.exists", return_value=True)
    def test_text_pdf_uses_local_extraction_instead_of_responses_file_upload(
        self,
        _mock_exists,
        mock_read_bundle,
    ):
        mock_read_bundle.return_value = {
            "extraction_status": "READY",
            "extracted_text": "متن استخراج‌شده گزارش آزمون",
            "text_stats": {"total_pages": 2, "total_chars": 31},
        }
        test = {
            "id": "test-1",
            "title": "آزمون",
            "attachments": [
                {
                    "id": "attachment-1",
                    "file_name": "report.pdf",
                    "content_type": "application/pdf",
                    "file_path": "clinical_tests/1/report.pdf",
                }
            ],
        }

        result = build_test_attachment_tool_result(test)
        payload = json.loads(result.content)

        self.assertIsNone(result.files)
        self.assertEqual(payload["attachments"][0]["inspection_mode"], "extracted_text")
        self.assertEqual(
            payload["extracted_attachments"][0]["extracted_text"],
            "متن استخراج‌شده گزارش آزمون",
        )

    @mock.patch("vania_core.tests_service.ClinicalTestsService.read_test_attachment_bundle")
    @mock.patch("capabilities.test_attachment_media.default_storage.open")
    @mock.patch("capabilities.test_attachment_media.default_storage.exists", return_value=True)
    def test_scanned_pdf_falls_back_to_original_file(
        self,
        _mock_exists,
        mock_open,
        mock_read_bundle,
    ):
        mock_read_bundle.return_value = {
            "extraction_status": "FAILED",
            "extracted_text": "",
            "text_stats": {},
        }
        mock_open.return_value.__enter__.return_value.read.return_value = b"%PDF-scan"
        test = {
            "id": "test-2",
            "title": "اسکن",
            "attachments": [
                {
                    "id": "attachment-2",
                    "file_name": "scan.pdf",
                    "content_type": "application/pdf",
                    "file_path": "clinical_tests/1/scan.pdf",
                }
            ],
        }

        result = build_test_attachment_tool_result(test)
        payload = json.loads(result.content)

        self.assertIsNotNone(result.files)
        self.assertEqual(result.files[0].filename, "scan.pdf")
        self.assertEqual(payload["attachments"][0]["inspection_mode"], "original_media")
