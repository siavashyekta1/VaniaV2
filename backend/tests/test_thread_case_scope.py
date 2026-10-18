from unittest import TestCase
from fastapi import HTTPException
from agents.case_scope import resolve_thread_scope


class ThreadCaseScopeTests(TestCase):
    def test_missing_headers_restore_bound_scope(self):
        scope = {"visitor_id": "1", "expert_id": "2", "case_id": "case-a"}
        self.assertEqual(resolve_thread_scope(scope, {}, True), scope)

    def test_same_visitor_different_case_is_rejected(self):
        with self.assertRaises(HTTPException) as result:
            resolve_thread_scope({"visitor_id": "1", "case_id": "a"}, {"visitor_id": 1, "case_id": "b"}, True)
        self.assertEqual(result.exception.status_code, 409)

    def test_different_visitor_is_rejected(self):
        with self.assertRaises(HTTPException):
            resolve_thread_scope({"visitor_id": "1"}, {"visitor_id": "3"}, True)

    def test_new_thread_can_bind_scope(self):
        self.assertEqual(resolve_thread_scope({}, {"visitor_id": 1, "case_id": "a"}), {"visitor_id": "1", "case_id": "a"})
