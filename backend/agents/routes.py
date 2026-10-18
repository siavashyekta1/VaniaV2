import logging
import time
import os
import tempfile
import uuid 
import json 
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from asgiref.sync import sync_to_async
from django.db import connection
from openai import OpenAI
from agno.media import File as AgnoFile

# --- Agno Imports ---
from agno.agent import AgentSession, Agent 
from ag_ui.core import RunAgentInput

# --- Project Imports ---
from agents.auth import get_current_user
from agents.factory import create_agent_for_service
from agents.case_scope import resolve_thread_scope
from agents.context import resource_context, selected_doctor_context, selected_case_context
from users.models import CustomUser
from billing.services import process_service_charge
from billing.models import BillingConfig

# --- Module Imports ---
from .schemas import SessionCreate, SessionUpdate
from .storage import get_storage, get_session_safe
from .stream import agui_stream_generator
from .tool_result_sanitizer import sanitize_tool_result_content
from .utils import safe_serialize, build_branch_history_messages, clean_internal_prompt_content
from .session_metadata import adjust_session_knowledge_file_count, apply_session_metadata_defaults

from services.access_service import access_service
from services.models import AgentService, SharedLink 
from core.ai_provider import get_openai_client_kwargs, get_transcription_model_id
from services.rag_service import ingest_session_file, remove_session_file

from enum import Enum
class SessionType(str, Enum):
    AGENT = "agent"

# --- Logging Setup ---
logger = logging.getLogger(__name__)
router = APIRouter()

TOOL_ONLY_CHAT_AGENT_SLUGS = {"vania-expert-assistant"}

ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024
ATTACHMENT_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/jpg"}
ATTACHMENT_ALLOWED_DOC_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/acrobat",
    "applications/vnd.pdf",
    "text/pdf",
    "text/x-pdf",
    "application/octet-stream",
}


def _normalize_session_state_aliases(session_state):
    if not isinstance(session_state, dict):
        return {}

    out = dict(session_state)
    visitor_id = out.get("visitor_id") or out.get("patient_id")
    expert_id = out.get("selected_expert_id") or out.get("selected_doctor_id")
    visitor_name = out.get("visitor_name") or out.get("patient_name")
    expert_name = out.get("selected_expert_name") or out.get("selected_doctor_name")
    case_title = out.get("selected_case_title") or out.get("case_title") or out.get("case_name")

    if visitor_id is not None:
        out["visitor_id"] = visitor_id
        out["patient_id"] = visitor_id
    if expert_id is not None:
        out["selected_expert_id"] = expert_id
        out["selected_doctor_id"] = expert_id
    if visitor_name:
        out["visitor_name"] = visitor_name
        out["patient_name"] = visitor_name
    if expert_name:
        out["selected_expert_name"] = expert_name
        out["selected_doctor_name"] = expert_name
    if case_title:
        out["selected_case_title"] = case_title
        out["case_title"] = case_title
        out["case_name"] = case_title

    return out


def _should_hide_assistant_text(agent_slug: Optional[str], tool_calls: Optional[list]) -> bool:
    return bool(agent_slug in TOOL_ONLY_CHAT_AGENT_SLUGS and tool_calls)


def _get_session_display_name(session_data: Optional[dict], fallback: str = "New Conversation") -> str:
    if not isinstance(session_data, dict):
        return fallback
    return (
        session_data.get("name")
        or session_data.get("session_name")
        or fallback
    )


def _coerce_session_data(session_data: Any) -> dict:
    serialized = safe_serialize(session_data) or {}
    if isinstance(serialized, str):
        try:
            parsed = json.loads(serialized)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return serialized if isinstance(serialized, dict) else {}


def _get_agent_sessions_table_name() -> Optional[str]:
    """
    Resolve Agno's session table without assuming the schema name.

    Production stores it under ai.agent_sessions, while local/dev setups may use
    a plain agent_sessions table or SQLite through Agno's adapter.
    """
    if connection.vendor != "postgresql":
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(
                to_regclass('ai.agent_sessions')::text,
                to_regclass('agent_sessions')::text
            )
            """
        )
        row = cursor.fetchone()

    table_name = row[0] if row else None
    if table_name not in {"ai.agent_sessions", "agent_sessions", "public.agent_sessions"}:
        return None
    return table_name


def _list_session_metadata_from_db(
    *,
    user_id: str,
    limit: int,
    page: int,
    agent_id: Optional[str] = None,
) -> Optional[list[dict[str, Any]]]:
    """
    Fast path for session lists.

    The Agno storage adapter returns full session objects, including the `runs`
    JSONB column. Some older users have hundreds of sessions and gigabytes of
    compressed run data, so metadata lists must avoid selecting that column.
    """
    table_name = _get_agent_sessions_table_name()
    if not table_name:
        return None

    offset = (page - 1) * limit
    params: list[Any] = [str(user_id), SessionType.AGENT.value]
    agent_filter = ""
    if agent_id:
        agent_filter = "AND (agent_id = %s OR session_data->>'agent_id' = %s)"
        params.extend([agent_id, agent_id])

    params.extend([limit, offset])

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                session_id,
                agent_id,
                session_data,
                created_at
            FROM {table_name}
            WHERE user_id = %s
              AND session_type = %s
              {agent_filter}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        rows = cursor.fetchall()

    results = []
    for session_id, row_agent_id, session_data, created_at in rows:
        session_data = _coerce_session_data(session_data)
        resolved_agent_id = session_data.get("agent_id") or row_agent_id
        results.append({
            "session_id": session_id,
            "session_name": _get_session_display_name(session_data, session_id),
            "agent_id": resolved_agent_id,
            "created_at": created_at,
        })

    return results

# ==========================================
# SESSION MANAGEMENT ROUTES
# ==========================================

@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100), 
    page: int = Query(1, ge=1), 
    agent_id: Optional[str] = Query(None),
    user: CustomUser = Depends(get_current_user)
):
    """
    Lists chat sessions for the authenticated user.
    """
    request_id = int(time.time() * 1000)
    logger.info(f"📋 [ListSessions] [ReqID:{request_id}] Request received for User {user.id}")

    try:
        fast_results = await sync_to_async(_list_session_metadata_from_db)(
            user_id=str(user.id),
            limit=limit,
            page=page,
            agent_id=agent_id,
        )
        if fast_results is not None:
            return JSONResponse(content=fast_results)

        storage = get_storage()
        
        # Ensure table exists
        if hasattr(storage, 'create'): 
            await sync_to_async(storage.create)()

        # Fetch Sessions
        try:
            sessions = await sync_to_async(storage.get_sessions)(
                user_id=str(user.id), 
                session_type=SessionType.AGENT
            )
        except TypeError:
            sessions = await sync_to_async(storage.get_sessions)(user_id=str(user.id))
        
        # Sort & Paginate
        sessions.sort(key=lambda x: getattr(x, 'created_at', 0) or 0, reverse=True)

        if agent_id:
            filtered_sessions = []
            for s in sessions:
                s_dict = safe_serialize(s)
                session_data = s_dict.get('session_data') or {}
                row_agent_id = s_dict.get('agent_id')
                resolved_agent_id = session_data.get('agent_id') if isinstance(session_data, dict) else None
                if row_agent_id == agent_id or resolved_agent_id == agent_id:
                    filtered_sessions.append(s)
            sessions = filtered_sessions
        
        start = (page - 1) * limit
        end = start + limit
        paginated = sessions[start:end] if start < len(sessions) else []
        
        # Serialize
        results = []
        for s in paginated:
            s_dict = safe_serialize(s)
            session_data = s_dict.get('session_data') or {}
            
            display_name = s.session_id
            agent_slug = s_dict.get('agent_id') 
            
            if session_data:
                display_name = _get_session_display_name(session_data, display_name)
                if 'agent_id' in session_data:
                    agent_slug = session_data['agent_id']

            results.append({
                "session_id": s.session_id,
                "session_name": display_name,
                "agent_id": agent_slug,
                "created_at": s.created_at
            })
            
        return JSONResponse(content=results)

    except Exception as e:
        logger.error(f"❌ [ListSessions] Error: {e}", exc_info=True)
        return JSONResponse(content=[], status_code=200)


@router.get("/sessions/{session_id}")
async def get_session_history(
    session_id: str, 
    user: CustomUser = Depends(get_current_user)
):
    """
    Retrieves the full chat history and session metadata.
    """
    try:
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, session_id, str(user.id))
        
        session_name = "New Conversation"
        session_state = {}
        session_data = {}
        if session:
            s_data = safe_serialize(session)
            if 'session_data' in s_data:
                session_data = s_data['session_data'] or {}
                session_name = _get_session_display_name(session_data, "New Conversation")
                apply_session_metadata_defaults(session_data)
                session_state = _normalize_session_state_aliases({
                    "agent_id": session_data.get("agent_id"),
                    "visitor_id": session_data.get("visitor_id"),
                    "patient_id": session_data.get("patient_id"),
                    "visitor_name": session_data.get("visitor_name"),
                    "patient_name": session_data.get("patient_name"),
                    "selected_expert_id": session_data.get("selected_expert_id"),
                    "selected_doctor_id": session_data.get("selected_doctor_id"),
                    "selected_expert_name": session_data.get("selected_expert_name"),
                    "selected_doctor_name": session_data.get("selected_doctor_name"),
                    "selected_case_id": session_data.get("selected_case_id"),
                    "selected_case_title": session_data.get("selected_case_title"),
                    "selected_case_doctor_name": session_data.get("selected_case_doctor_name"),
                    "selected_case_doctor_profession_slug": session_data.get("selected_case_doctor_profession_slug"),
                    "selected_case_doctor_profession_label": session_data.get("selected_case_doctor_profession_label"),
                })
                attachment_history = session_data.get("ui_attachments") or []
            else:
                attachment_history = []
        else:
            attachment_history = []

        if not session: 
            return JSONResponse(content={
                "chat_history": [],
                "session_name": "گفتگوی جدید",
                "session_state": {}
            })

        history = []
        raw_messages = []

        if hasattr(session, 'memory') and session.memory and hasattr(session.memory, 'messages'):
            raw_messages = session.memory.messages
        elif hasattr(session, 'messages'):
            raw_messages = session.messages
        else:
            s_dict = safe_serialize(session)
            if 'memory' in s_dict and 'messages' in s_dict['memory']:
                raw_messages = s_dict['memory']['messages']
            elif 'messages' in s_dict:
                raw_messages = s_dict['messages']

        if not raw_messages and hasattr(session, 'get_messages'):
            raw_messages = session.get_messages()

        serialized_messages = []
        user_messages = []
        attachment_history_by_message_id = {}
        legacy_attachment_history = []
        for entry in attachment_history:
            if not isinstance(entry, dict):
                continue
            message_id = entry.get("message_id")
            if message_id:
                attachment_history_by_message_id[str(message_id)] = entry.get("attachments", []) or []
            else:
                legacy_attachment_history.append(entry)

        for msg in raw_messages:
            m_data = safe_serialize(msg)
            if not isinstance(m_data, dict):
                continue
            serialized_messages.append(m_data)
            role = m_data.get('role')
            if role == 'user':
                user_messages.append(m_data)

        legacy_attachment_start_index = max(0, len(user_messages) - len(legacy_attachment_history))
        legacy_attachment_index = 0
        user_message_index = 0

        for m_data in serialized_messages:
            role = m_data.get('role')
            if role == 'model':
                role = 'assistant'
            if role == 'function':
                role = 'tool'

            content = m_data.get('content')
            if role == "user":
                content = clean_internal_prompt_content(content)

            item = {
                "role": role,
                "content": content,
                "created_at": m_data.get('created_at') or m_data.get('timestamp')
            }

            if role == "user":
                explicit_attachments = m_data.get("attachmentsMeta")
                if isinstance(explicit_attachments, list) and explicit_attachments:
                    item["attachments"] = explicit_attachments
                else:
                    message_id = m_data.get("id")
                    if message_id and str(message_id) in attachment_history_by_message_id:
                        item["attachments"] = attachment_history_by_message_id[str(message_id)]
                    elif user_message_index >= legacy_attachment_start_index and legacy_attachment_index < len(legacy_attachment_history):
                        attachment_entry = legacy_attachment_history[legacy_attachment_index]
                        item["attachments"] = attachment_entry.get("attachments", []) if isinstance(attachment_entry, dict) else []
                        legacy_attachment_index += 1
                    else:
                        item["attachments"] = []
                user_message_index += 1

            if role == 'assistant':
                item["tool_calls"] = m_data.get('tool_calls')
                if _should_hide_assistant_text(session_data.get("agent_id"), item["tool_calls"]):
                    item["content"] = ""
            
            if role == 'tool':
                item["tool_call_id"] = m_data.get('tool_call_id')
                if isinstance(item["content"], (dict, list)):
                    try:
                        import json
                        item["content"] = json.dumps(item["content"], ensure_ascii=False)
                    except:
                        item["content"] = str(item["content"])
                item["content"] = sanitize_tool_result_content(item["content"])

            history.append(item)

        history.sort(key=lambda x: x.get('created_at') or 0)
        
        return JSONResponse(content={
            "chat_history": history,
            "session_name": session_name,
            "session_state": session_state
        })
        
    except Exception as e:
        logger.error(f"❌ [GetHistory] Error processing {session_id}: {e}", exc_info=True)
        return JSONResponse(content={"chat_history": [], "session_name": "Error"}, status_code=200)


@router.post("/sessions")
async def create_session(
    session_data: SessionCreate, 
    user: CustomUser = Depends(get_current_user)
):
    """
    Creates a new session record in the DB manually.
    """
    logger.info(f"✨ [CreateSession] Creating '{session_data.session_name}' (ID: {session_data.session_id})")

    try:
        storage = get_storage()
        if hasattr(storage, 'create'): await sync_to_async(storage.create)()

        existing = await sync_to_async(get_session_safe)(storage, session_data.session_id, str(user.id))
        if existing: 
            return JSONResponse(content={"status": "exists"})

        now = int(time.time())
        normalized_session_state = _normalize_session_state_aliases(session_data.session_state)
        new_session = AgentSession(
            session_id=session_data.session_id,
            user_id=str(user.id),
            session_data=apply_session_metadata_defaults({
                "name": session_data.session_name,
                "session_name": session_data.session_name,
                "agent_id": normalized_session_state.get("agent_id"),
                **normalized_session_state
            }),
            created_at=now,
            updated_at=now
        )
        
        try: new_session.session_type = SessionType.AGENT
        except: pass

        if hasattr(storage, 'upsert_session'):
            await sync_to_async(storage.upsert_session)(session=new_session)
        else:
            await sync_to_async(storage.upsert)(session=new_session)
            
        return JSONResponse(content={"status": "created"})

    except Exception as e:
        logger.error(f"❌ [CreateSession] Error: {e}")
        return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=500)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, 
    update_data: SessionUpdate, 
    user: CustomUser = Depends(get_current_user)
):
    try:
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, session_id, str(user.id))
        
        if session:
            if not session.session_data:
                session.session_data = {}
            apply_session_metadata_defaults(session)
            if update_data.session_name is not None:
                session.session_data["name"] = update_data.session_name
                session.session_data["session_name"] = update_data.session_name
            if update_data.session_state:
                session.session_data.update(_normalize_session_state_aliases(update_data.session_state))
            
            if hasattr(storage, 'upsert_session'):
                await sync_to_async(storage.upsert_session)(session=session)
            else:
                await sync_to_async(storage.upsert)(session=session)
            
            return JSONResponse(content={"status": "updated"})
        
        return JSONResponse(content={"status": "not_found"})
        
    except Exception as e:
        logger.error(f"❌ [RenameSession] Error: {e}")
        return JSONResponse(content={"status": "error"}, status_code=500)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, 
    user: CustomUser = Depends(get_current_user)
):
    try:
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, session_id, str(user.id))
        
        if not session:
            return JSONResponse(content={"status": "not_found"}, status_code=404)

        if hasattr(storage, 'delete_session'):
            await sync_to_async(storage.delete_session)(session_id=session_id)
        elif hasattr(storage, 'delete'):
             await sync_to_async(storage.delete)(session_id=session_id)
        
        return JSONResponse(content={"status": "deleted"})
        
    except Exception as e:
        logger.error(f"❌ [DeleteSession] Error: {e}")
        return JSONResponse(content={"status": "error"}, status_code=500)


# ==========================================
# CANCEL RUN ENDPOINT
# ==========================================

@router.post("/runs/{run_id}/cancel")
async def cancel_run_endpoint(
    run_id: str, 
    user: CustomUser = Depends(get_current_user)
):
    """
    Explicitly cancels a run. 
    This updates the database state immediately, regardless of the stream status.
    """
    try:
        storage = get_storage()
        
        # 1. Verify ownership (Security)
        session = await sync_to_async(get_session_safe)(storage, run_id, str(user.id))
        
        # Fallback: Check if run_id is actually a session_id
        if not session:
             session = await sync_to_async(get_session_safe)(storage, run_id, str(user.id))
        
        if not session:
            raise HTTPException(status_code=404, detail="Run or Session not found")

        # 2. Perform Cancellation
        dummy_agent = Agent(storage=storage, session_id=run_id)
        
        await sync_to_async(dummy_agent.cancel_run)(run_id)
        
        logger.info(f"🛑 [CancelAPI] User {user.id} marked run {run_id} as cancelled.")
        return {"status": "cancelled"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [CancelAPI] Failed to cancel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# SHARING ENDPOINTS
# ==========================================

@router.post("/share/{session_id}")
async def create_share_link(
    session_id: str,
    user: CustomUser = Depends(get_current_user)
):
    """
    Generates a public read-only link for a specific session.
    """
    try:
        # 1. Verify Session Ownership
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, session_id, str(user.id))
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # 2. Extract Metadata
        s_data = safe_serialize(session)
        title = "Conversation"
        agent_slug = ""
        
        if 'session_data' in s_data:
            title = s_data['session_data'].get('name', title)
            agent_slug = s_data['session_data'].get('agent_id', agent_slug)

        # 3. Create or Get Link
        link, created = await SharedLink.objects.aget_or_create(
            session_id=session_id,
            created_by=user,
            defaults={
                "title": title,
                "agent_slug": agent_slug,
                "is_active": True
            }
        )
        
        return {
            "share_id": str(link.id),
            "url": f"/share/{link.id}" # Frontend path
        }

    except Exception as e:
        logger.error(f"❌ [Share] Creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create share link")


@router.get("/share/{token}")
async def get_shared_chat(token: str):
    """
    Public Endpoint: Returns sanitized chat history for a shared link.
    No Authentication required (Bypassed by Middleware).
    """
    try:
        # 1. Validate Token
        try:
            link = await SharedLink.objects.aget(id=token, is_active=True)
        except SharedLink.DoesNotExist:
            raise HTTPException(status_code=404, detail="Link not found or expired")

        # 2. Increment View Count
        @sync_to_async
        def increment_views(obj):
            obj.views_count += 1
            obj.save(update_fields=['views_count'])
        
        await increment_views(link)

        # 3. Fetch Session Data
        storage = get_storage()
        
        # Direct fetch, bypassing ownership check
        try:
            session = await sync_to_async(storage.get_session)(session_id=link.session_id, session_type=SessionType.AGENT)
        except TypeError:
            session = await sync_to_async(storage.get_session)(session_id=link.session_id)

        if not session:
            raise HTTPException(status_code=404, detail="Original session deleted")

        # 4. Sanitize Messages
        history = []
        raw_messages = []
        session_payload = safe_serialize(session)
        shared_agent_slug = ((session_payload.get("session_data") or {}) if isinstance(session_payload, dict) else {}).get("agent_id")

        if hasattr(session, 'memory') and session.memory and hasattr(session.memory, 'messages'):
            raw_messages = session.memory.messages
        elif hasattr(session, 'messages'):
            raw_messages = session.messages
        else:
            s_dict = safe_serialize(session)
            if 'memory' in s_dict and 'messages' in s_dict['memory']:
                raw_messages = s_dict['memory']['messages']
            elif 'messages' in s_dict:
                raw_messages = s_dict['messages']

        # [FIX] Fallback for Agno v2: use get_messages() if direct access fails
        if not raw_messages and hasattr(session, 'get_messages'):
            try:
                raw_messages = session.get_messages()
            except Exception as e:
                logger.warning(f"⚠️ [Share] Failed to call get_messages(): {e}")

        for msg in raw_messages:
            m_data = safe_serialize(msg)
            if not isinstance(m_data, dict): continue
            
            role = m_data.get('role')
            if role == 'model': role = 'assistant'
            if role == 'function': role = 'tool'
            
            # Skip system messages for public view
            if role == 'system': continue

            content = m_data.get('content')
            if role == "user":
                content = clean_internal_prompt_content(content)

            item = {
                "role": role,
                "content": content,
                "created_at": m_data.get('created_at') or m_data.get('timestamp')
            }

            if role == 'assistant':
                item["tool_calls"] = m_data.get('tool_calls')
                if _should_hide_assistant_text(shared_agent_slug, item["tool_calls"]):
                    item["content"] = ""
            
            if role == 'tool':
                item["tool_call_id"] = m_data.get('tool_call_id')
                content = item["content"]
                if isinstance(content, (dict, list)):
                    try: item["content"] = json.dumps(content, ensure_ascii=False)
                    except: item["content"] = str(content)
                item["content"] = sanitize_tool_result_content(item["content"])

            history.append(item)

        history.sort(key=lambda x: x.get('created_at') or 0)

        return {
            "title": link.title,
            "agent_slug": link.agent_slug,
            "created_at": link.created_at,
            "chat_history": history
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [Share] Fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Internal Error")


# ==========================================
# AG-UI STREAMING ENDPOINT
# ==========================================

@router.post("/agui")
async def agui_chat_endpoint(
    request: Request,
    input_data: RunAgentInput,
    agent_id: str = Query(..., description="Service Slug"),
    user: CustomUser = Depends(get_current_user)
):
    """
    Main entry point for Chat Streaming.
    Enforces Demo Limits before starting the agent.
    """
    from services.usage import demo_usage_service
    
    req_start = time.time()
    
    try:
        thread_id = input_data.thread_id or f"sess_{user.id}_{agent_id}"
        
        try:
            service = await AgentService.objects.aget(slug=agent_id)
        except AgentService.DoesNotExist:
            raise HTTPException(status_code=404, detail="Agent service not found")

        has_access, _ = await sync_to_async(access_service.check_permission)(user, agent_id)

        if not has_access:
            can_proceed, reason = await demo_usage_service.check_limits(user, service, thread_id)
            if not can_proceed:
                logger.warning(f"⛔ [AGUI] Access Denied for User {user.id} on {agent_id}: {reason}")
                raise HTTPException(status_code=403, detail=reason)

        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, thread_id, str(user.id))
        if service.audience == "EXPERT":
            from vania_core.case_service import CaseService
            data = dict(getattr(session, "session_data", None) or {})
            saved_scope = data.get("bound_case_scope") or {
                "visitor_id": data.get("visitor_id") or data.get("patient_id"),
                "expert_id": data.get("selected_expert_id") or data.get("selected_doctor_id"),
                "case_id": data.get("selected_case_id"),
            }
            scope = resolve_thread_scope(saved_scope, {
                "visitor_id": resource_context.get(),
                "expert_id": selected_doctor_context.get(),
                "case_id": selected_case_context.get(),
            }, has_history=bool(getattr(session, "runs", None)))
            if scope.get("case_id"):
                if not scope.get("visitor_id"):
                    raise HTTPException(409, "برای این پرونده ابتدا مراجع را انتخاب کنید.")
                patient = await CustomUser.objects.aget(pk=scope["visitor_id"])
                case = await sync_to_async(CaseService.get_accessible_case_for_expert)(
                    patient, user, scope["case_id"]
                )
                if not case:
                    raise HTTPException(403, "پرونده انتخاب‌شده برای این مراجع در دسترس نیست.")
                if scope.get("expert_id") and str(case.get("doctor_id")) != scope["expert_id"]:
                    raise HTTPException(409, "متخصص انتخاب‌شده با مالک پرونده مطابقت ندارد.")
                scope["expert_id"] = str(case["doctor_id"])
            resource_context.set(scope.get("visitor_id"))
            selected_doctor_context.set(scope.get("expert_id"))
            selected_case_context.set(scope.get("case_id"))
            data["bound_case_scope"] = scope
            data.update({
                "visitor_id": scope.get("visitor_id"),
                "patient_id": scope.get("visitor_id"),
                "selected_expert_id": scope.get("expert_id"),
                "selected_doctor_id": scope.get("expert_id"),
                "selected_case_id": scope.get("case_id"),
            })
            if scope.get("case_id"):
                data["selected_case_title"] = case.get("title")
            if session is None:
                session = AgentSession(session_id=thread_id, user_id=str(user.id), session_data=data,
                                       created_at=int(time.time()), updated_at=int(time.time()))
            else:
                session.session_data = data
            if hasattr(storage, "upsert_session"):
                await sync_to_async(storage.upsert_session)(session=session)
            else:
                await sync_to_async(storage.upsert)(session=session)
        if session and input_data.messages:
            branch_messages = build_branch_history_messages(input_data.messages)
            if branch_messages:
                existing_messages = []
                if hasattr(session, "memory") and session.memory is not None and hasattr(session.memory, "messages"):
                    existing_messages = session.memory.messages or []
                elif hasattr(session, "messages"):
                    existing_messages = session.messages or []

                # Avoid wiping a persisted thread down to the latest user message when the client
                # only posts the newest turn. Full branch sync is still allowed for edits/reloads.
                should_replace_history = len(branch_messages) > 1 or not existing_messages
                if should_replace_history:
                    if hasattr(session, "memory") and session.memory is not None:
                        session.memory.messages = branch_messages
                    else:
                        session.messages = branch_messages
                    if hasattr(storage, "upsert_session"):
                        await sync_to_async(storage.upsert_session)(session=session)
                    else:
                        await sync_to_async(storage.upsert)(session=session)

        agent = await sync_to_async(create_agent_for_service)(
            user, agent_id, thread_id, request=request
        )
        
        stream_gen = agui_stream_generator(agent, input_data, request, is_demo_user=not has_access)
        
        return StreamingResponse(
            stream_gen,
            media_type="text/event-stream"
        )

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - req_start
        logger.error(f"❌ [AGUI] Endpoint Error (after {duration:.2f}s): {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Internal Server Error in Agent Runtime: {str(e)}"
        )
        
        
@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...), 
    user: CustomUser = Depends(get_current_user)
):
    """
    Receives audio blob, sends to OpenAI Whisper, calculates cost, 
    charges the user, and returns text.
    """
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        client = OpenAI(**get_openai_client_kwargs())
        transcription_model = get_transcription_model_id()
        
        with open(tmp_path, "rb") as audio_file:
            transcript_response = client.audio.transcriptions.create(
                model=transcription_model,
                file=audio_file,
                language="fa",
                response_format="verbose_json", 
                prompt="the user is talking in Persian language in a therapy session. there might be multiple speakers."
            )
        
        text = transcript_response.text
        duration_seconds = getattr(transcript_response, 'duration', 0.0)
        
        config = await sync_to_async(BillingConfig.load)()
        cost_per_min = config.transcription_cost_per_minute
        
        duration_minutes = Decimal(duration_seconds) / Decimal(60)
        total_cost = duration_minutes * cost_per_min
        
        total_cost = max(total_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal("0.10"))

        charge_result = await sync_to_async(process_service_charge)(
            user=user,
            amount=total_cost,
            description=f"Transcription: {duration_seconds:.1f}s"
        )

        if not charge_result['success']:
            logger.warning(f"User {user.id} failed to pay for transcription. Cost: {total_cost}")
            raise HTTPException(
                status_code=402, 
                detail=f"اعتبار کافی نیست. هزینه تبدیل صوت: {total_cost} سرمایه گفتگو."
            )

        return {
            "text": text,
            "duration": duration_seconds,
            "cost": float(total_cost)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [Transcribe] Error: {e}")
        raise HTTPException(status_code=500, detail="Transcription failed")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _validate_attachment_file(filename: str | None, size: int, content_type: str | None) -> None:
    if not filename:
        raise HTTPException(status_code=400, detail="فایل نامعتبر است.")
    if size <= 0 or size > ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="حجم فایل مجاز نیست.")

    content_type = content_type or "application/octet-stream"
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        if content_type not in ATTACHMENT_ALLOWED_DOC_TYPES:
            raise HTTPException(status_code=400, detail="فقط فایل PDF مجاز است.")
        return

    if content_type in ATTACHMENT_ALLOWED_IMAGE_TYPES:
        return

    raise HTTPException(status_code=400, detail="فقط تصویر و PDF مجاز است.")


@router.post("/attachments/prepare")
async def prepare_attachment(
    thread_id: str = Form(...),
    agent_id: str = Form(...),
    attachment_id: str = Form(...),
    file: UploadFile = File(...),
    user: CustomUser = Depends(get_current_user),
):
    try:
        storage = get_storage()
        if hasattr(storage, "create"):
            await sync_to_async(storage.create)()

        session = await sync_to_async(get_session_safe)(storage, thread_id, str(user.id))
        if not session:
            now = int(time.time())
            new_session = AgentSession(
                session_id=thread_id,
                user_id=str(user.id),
                session_data=apply_session_metadata_defaults({"name": "New Conversation", "agent_id": agent_id}),
                created_at=now,
                updated_at=now,
            )
            try:
                new_session.session_type = SessionType.AGENT
            except Exception:
                pass
            if hasattr(storage, "upsert_session"):
                await sync_to_async(storage.upsert_session)(session=new_session)
            else:
                await sync_to_async(storage.upsert)(session=new_session)

        content = await file.read()
        _validate_attachment_file(file.filename, len(content), file.content_type)
        mime = file.content_type or "application/octet-stream"
        lower_name = (file.filename or "").lower()

        if lower_name.endswith(".pdf"):
            agno_file = AgnoFile(
                content=content,
                file_type="pdf",
                path=file.filename,
                name=file.filename,
            )
            ok = await sync_to_async(ingest_session_file)(thread_id, agno_file, attachment_id)
            if not ok:
                raise HTTPException(
                    status_code=500,
                    detail="فایل آپلود شد اما متن PDF قابل پردازش نبود. می‌توانید فایل را دوباره امتحان کنید یا نسخه دیگری از PDF را بارگذاری کنید.",
                )
            session = await sync_to_async(get_session_safe)(storage, thread_id, str(user.id))
            if session:
                adjust_session_knowledge_file_count(session, 1)
                if hasattr(storage, "upsert_session"):
                    await sync_to_async(storage.upsert_session)(session=session)
                else:
                    await sync_to_async(storage.upsert)(session=session)

        return {
            "attachment_id": attachment_id,
            "name": file.filename,
            "content_type": mime,
            "kind": "image" if mime.startswith("image/") else "file",
            "prepared": True,
            "processed_on_server": lower_name.endswith(".pdf"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [AttachmentPrepare] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="آماده‌سازی فایل انجام نشد")


@router.delete("/attachments/{attachment_id}")
async def remove_prepared_attachment(
    attachment_id: str,
    thread_id: str = Query(...),
    user: CustomUser = Depends(get_current_user),
):
    try:
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, thread_id, str(user.id))
        if not session:
            return {"status": "not_found"}

        removed = await sync_to_async(remove_session_file)(thread_id, attachment_id)
        if removed:
            adjust_session_knowledge_file_count(session, -1)
            if hasattr(storage, "upsert_session"):
                await sync_to_async(storage.upsert_session)(session=session)
            else:
                await sync_to_async(storage.upsert)(session=session)
        return {"status": "deleted" if removed else "not_found"}
    except Exception as e:
        logger.error(f"❌ [AttachmentDelete] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="حذف فایل آماده‌شده انجام نشد")
