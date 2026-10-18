"""Resolve a thread's explicit collaboration scope before any model/tool execution."""
from fastapi import HTTPException


def resolve_thread_scope(saved, requested, has_history=False):
    keys = ("visitor_id", "expert_id", "case_id")
    saved = {k: str(saved[k]) for k in keys if saved.get(k) not in (None, "")}
    requested = {k: str(requested[k]) for k in keys if requested.get(k) not in (None, "")}
    if has_history:
        for key in keys:
            if key in saved and key in requested and saved[key] != requested[key]:
                raise HTTPException(409, "این گفتگو متعلق به مراجع یا پرونده دیگری است. گفتگوی جدید باز کنید.")
    return {**saved, **requested}
