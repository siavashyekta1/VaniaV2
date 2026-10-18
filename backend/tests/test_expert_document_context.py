import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from capabilities.vania_expert.document_context import build_document_context


class ExpertDocumentContextTests(TestCase):
    @patch("capabilities.vania_expert.document_context.cache")
    @patch("capabilities.vania_expert.document_context.ClinicalTestsService.read_test_attachment_bundle")
    def test_document_text_is_available_before_tools_and_truncation_is_explicit(self, read, cache):
        cache.get.return_value = None
        read.return_value = {"extracted_text": "abcdefgh", "extraction_status": "READY"}
        context = build_document_context(SimpleNamespace(id=1), 2, "case-a", [{"id":"test-a", "attachments":[{"id":"file-a"}]}], [], budget=4)
        payload = json.loads(context[context.index('{'):])
        self.assertEqual(payload["case_id"], "case-a")
        self.assertEqual(payload["documents"][0]["text"], "abcd")
        self.assertEqual(payload["documents"][0]["coverage"], "partial")

    @patch("capabilities.vania_expert.document_context.cache")
    @patch("capabilities.vania_expert.document_context.ClinicalTestsService.read_test_attachment_bundle")
    def test_failed_extraction_never_claims_document_was_read(self, read, cache):
        cache.get.return_value = None
        read.return_value = {"extracted_text":"", "extraction_status":"FAILED"}
        context = build_document_context(SimpleNamespace(id=1),2,"case-a",[{"id":"a","attachments":[{"id":"b"}]}],[])
        payload = json.loads(context[context.index('{'):])
        self.assertEqual(payload["documents"][0]["coverage"],"unread")
