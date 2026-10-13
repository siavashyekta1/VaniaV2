# backend/agents/factory.py
import logging
import time
import json
from typing import List, Any, Optional
from django.conf import settings
from django.db import transaction

# --- Agno Imports ---
from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.qdrant import Qdrant
from agno.knowledge.embedder.openai import OpenAIEmbedder

# --- Service Imports ---
from services.models import AgentService
from services.tool_factory import ToolFactory
from services.rag_service import get_sanitized_table_name, get_session_knowledge, session_knowledge_exists
from services.models_canvas import CanvasType, CanvasInstance
from services.access_service import access_service
from users.roles import is_expert, is_visitor
from core.ai_provider import get_agno_openai_kwargs
from core.ai_models import (
    get_expert_responses_reasoning_effort,
    is_native_reasoning_model,
    requires_none_for_chat_tool_calling,
    resolve_chat_model,
    resolve_reasoning_effort,
    should_use_expert_responses,
)
# --- Capability System ---
from capabilities.registry import CapabilityRegistry

# --- Context ---
from .context import resource_context
from .global_profile_tools import get_global_profile_tools
from .models import SanitizedOpenAIChat, SanitizedOpenAIResponses
from .profile_context import build_default_profile_context, get_session_data_for_profile_context
from .storage import get_session_safe

# --- Agent Import ---
from .service_agent import ServiceAgent
from .session_metadata import apply_session_metadata_defaults, get_session_knowledge_flag, set_session_knowledge_metadata
from .session_summary import TimedSessionSummaryManager
from fastapi import Request

# Configure Logger
logger = logging.getLogger(__name__)

VISITOR_SELF_CONTEXT_AGENT_SLUGS = {
    "HAM-moraje",
    "HAM-motalee",
    "HAM-shoghli",
    "HAM-tahsili",
    "ravanyar",
}

EXPERT_CASE_CONTEXT_AGENT_SLUGS = {
    "ravanyar-motekhases",
    "supervisor-mashaghel",
}


def _resolve_active_capabilities(service: AgentService, user) -> list[str]:
    active_caps = list(service.capabilities or [])
    if (
        service.slug in VISITOR_SELF_CONTEXT_AGENT_SLUGS
        and is_visitor(user)
        and "vania_visitor" not in active_caps
    ):
        active_caps.append("vania_visitor")
        logger.warning(
            "Agent '%s' is missing vania_visitor in DB; enabling visitor self-context capability at runtime for user %s.",
            service.slug,
            getattr(user, "id", "unknown"),
        )
    if (
        service.slug in EXPERT_CASE_CONTEXT_AGENT_SLUGS
        and is_expert(user)
        and "vania_expert" not in active_caps
    ):
        active_caps.append("vania_expert")
        logger.warning(
            "Agent '%s' is missing vania_expert in DB; enabling expert case-context capability at runtime for user %s.",
            service.slug,
            getattr(user, "id", "unknown"),
        )
    return active_caps


def _build_session_selection_context(session_data: Optional[dict]) -> list[str]:
    if not isinstance(session_data, dict):
        return []

    lines: list[str] = []

    visitor_id = session_data.get("visitor_id") or session_data.get("patient_id")
    visitor_name = session_data.get("visitor_name") or session_data.get("patient_name")
    if visitor_id or visitor_name:
        label = visitor_name or "Unknown"
        suffix = f" (ID: {visitor_id})" if visitor_id is not None else ""
        lines.append(f"Active visitor from frontend state: {label}{suffix}")

    expert_id = session_data.get("selected_expert_id") or session_data.get("selected_doctor_id")
    expert_name = session_data.get("selected_expert_name") or session_data.get("selected_doctor_name")
    if expert_id or expert_name:
        label = expert_name or "Unknown"
        suffix = f" (ID: {expert_id})" if expert_id is not None else ""
        lines.append(f"Active expert from frontend state: {label}{suffix}")

    case_id = session_data.get("selected_case_id")
    case_title = session_data.get("selected_case_title") or session_data.get("case_title") or session_data.get("case_name")
    if case_id or case_title:
        label = case_title or "Untitled case"
        suffix = f" (ID: {case_id})" if case_id else ""
        lines.append(f"Active case from frontend state: {label}{suffix}")

    case_doctor_name = session_data.get("selected_case_doctor_name")
    if case_doctor_name:
        lines.append(f"Active case doctor label in UI: {case_doctor_name}")

    case_doctor_profession = (
        session_data.get("selected_case_doctor_profession_label")
        or session_data.get("selected_case_doctor_profession_slug")
    )
    if case_doctor_profession:
        lines.append(f"Active case profession label in UI: {case_doctor_profession}")

    return lines


def create_agent_for_service(user, service_slug: str, session_id: str, request: Optional[Request] = None ) -> ServiceAgent:
    """
    Modular Factory: Instantiates a ServiceAgent by aggregating tools, instructions, 
    and UI states from code-defined Capabilities.
    """
    start_time = time.time()
    logger.info(f"🏭 [Factory] Starting creation of agent '{service_slug}' for User {user.id}")

    # =========================================================================
    # 1. Fetch Service Configuration & Determine Access
    # =========================================================================
    try:
        # [FIX] Removed 'interaction_forms' from prefetch_related
        service = AgentService.objects.prefetch_related(
            'knowledge_bases', 
            'custom_tools'
        ).get(slug=service_slug, is_active=True)
    except AgentService.DoesNotExist:
        logger.error(f"❌ [Factory] Service '{service_slug}' not found or is inactive.")
        raise ValueError(f"Service '{service_slug}' not found.")
    
    # Access & Model Swapping Logic
    has_access, _ = access_service.check_permission(user, service.slug)
    is_demo_mode = not has_access
    
    demo_conf = service.demo_config or {}
    model_selection = resolve_chat_model(
        service_model_id=service.model_id,
        is_demo_mode=is_demo_mode,
        demo_model_override=demo_conf.get("model_override"),
    )
    target_model_id = model_selection.model_id
    is_swapped_model = model_selection.source != "agent_service"

    logger.info(
        "[Factory] Model selected: agent_slug=%s session_id=%s model=%s source=%s demo=%s",
        service.slug,
        session_id,
        target_model_id,
        model_selection.source,
        is_demo_mode,
    )

    active_caps = _resolve_active_capabilities(service, user)
    
    # Retrieve the Scoped Context ID (e.g. Patient ID) set by Middleware
    resource_id = resource_context.get()

    # =========================================================================
    # 2. Canvas & Capability Hydration (UI State Pre-loading)
    # =========================================================================
    try:
        target_canvas_keys = CapabilityRegistry.get_canvases_for_domains(active_caps)
        
        if target_canvas_keys:
            with transaction.atomic():
                for key in target_canvas_keys:
                    try:
                        ctype = CanvasType.objects.get(component_key=key)
                        
                        # [CHANGED] Removed "if resource_id:" check.
                        # We allow capabilities to provide state even without a specific resource ID
                        # (e.g. The Patient Capability uses the logged-in 'user' as the resource).
                        start_state = CapabilityRegistry.get_initial_state_for_domains(
                            active_caps, user, session_id, resource_id, canvas_key=key
                        )
                        
                        # Fallback to the Canvas Definition's default state
                        final_state = start_state if start_state else ctype.default_state

                        CanvasInstance.objects.get_or_create(
                            session_id=session_id,
                            canvas_def=ctype,
                            defaults={
                                'current_state': final_state,
                                'is_visible': True
                            }
                        )
                    except CanvasType.DoesNotExist:
                        logger.warning(f"⚠️ [Factory] Canvas definition '{key}' missing in DB. Run sync_definitions.")
    except Exception as e:
        logger.warning(f"⚠️ [Factory] Canvas hydration failed: {e}")

    # =========================================================================
    # 3. Storage Engine
    # =========================================================================
    if "sqlite" in settings.DATABASE_CONNECTION_STRING:
        storage_instance = SqliteDb(
            db_file=settings.DATABASE_CONNECTION_STRING.replace("sqlite:///", ""),
            session_table="agent_sessions"
        )
    else:
        storage_instance = PostgresDb(
            db_url=settings.DATABASE_CONNECTION_STRING,
            session_table="agent_sessions"
        )

    # =========================================================================
    # 4. Tool Injection
    # =========================================================================
    agent_tools = ToolFactory.get_all_tools(service, user)
    agent_tools.extend(get_global_profile_tools())
    
    if active_caps:
        dynamic_tools = CapabilityRegistry.get_tools_for_domains(
            domains=active_caps,
            user=user,
            session_id=session_id
        )
        agent_tools.extend(dynamic_tools)
        logger.info(f"   [Factory] 🔌 Injected tools for: {active_caps}")

    # =========================================================================
    # 5. Prompt Engineering & Instructions
    # =========================================================================
    dynamic_instructions = []
    
    # A. User Context
    try:
        storage_for_context = get_session_safe(storage_instance, session_id, str(user.id))
        session_data_for_context = get_session_data_for_profile_context(
            user,
            session_id,
            getattr(storage_for_context, "session_data", None) if storage_for_context else None,
        )
        profile_context_block = build_default_profile_context(
            user,
            session_id=session_id,
            session_data=session_data_for_context,
        )
        if profile_context_block:
            dynamic_instructions.append(profile_context_block)
        if session_data_for_context:
            session_selection_context = _build_session_selection_context(session_data_for_context)
            if session_selection_context:
                dynamic_instructions.append(
                    "\n### ACTIVE FRONTEND SELECTIONS (System Injected)\n"
                    + "\n".join([f"- {line}" for line in session_selection_context])
                )
    except Exception as e:
        logger.warning(f"⚠️ [Factory] Failed to inject profile/session context: {e}")
        
    # B. Capability General Instructions
    cap_instructions = CapabilityRegistry.get_prompt_additions_for_domains(active_caps, user)
    if cap_instructions:
        dynamic_instructions.append(cap_instructions)

    # C. [HOOK] Resource-Specific Context Injection
    # If a Resource ID is locked, ask capabilities for context (e.g. "Active Patient: Ali")
    if resource_id:
        logger.info(f"   [Factory] 🔒 Context Locked to Resource: {resource_id}")
        resource_prompt = CapabilityRegistry.get_context_prompt_for_domains(
            active_caps, user, resource_id
        )
        if resource_prompt:
            dynamic_instructions.append(resource_prompt)
            logger.debug(f"   [Factory] Injected Resource Context ({len(resource_prompt)} chars)")

    # =========================================================================
    # 6. Hybrid Reasoning & Model Sanitization
    # =========================================================================
    is_native_reasoning = is_native_reasoning_model(target_model_id)
    use_expert_responses = should_use_expert_responses(
        audience=service.audience,
        model_id=target_model_id,
    )
    
    llm_kwargs = {"id": target_model_id, **get_agno_openai_kwargs()}
    
    user_effort_override = request.headers.get("x-reasoning-effort") if request else None
    user_reasoning_override = request.headers.get("x-enable-reasoning") if request else None

    requested_effort = (
        get_expert_responses_reasoning_effort()
        if use_expert_responses
        else user_effort_override or service.reasoning_effort
    )
    final_effort = resolve_reasoning_effort(
        target_model_id,
        requested_effort,
        use_responses_api=use_expert_responses,
    )
    if final_effort:
        llm_kwargs["reasoning_effort"] = final_effort
    if (
        not use_expert_responses
        and requires_none_for_chat_tool_calling(target_model_id)
        and requested_effort != "none"
    ):
        logger.info(
            "[Factory] Forced reasoning_effort=none for Chat Completions tool compatibility: "
            "agent_slug=%s session_id=%s requested_effort=%s",
            service.slug,
            session_id,
            requested_effort or "default",
        )
    
    use_agno_reasoning_wrapper = False 
    if not is_native_reasoning and not is_swapped_model:
        final_enable_reasoning = (user_reasoning_override == 'true') if user_reasoning_override is not None else service.enable_reasoning
        if final_enable_reasoning:
            use_agno_reasoning_wrapper = True

    if use_expert_responses:
        # Keep provider-side response storage disabled for sensitive expert workflows.
        llm_model = SanitizedOpenAIResponses(**llm_kwargs, store=False)
    else:
        llm_model = SanitizedOpenAIChat(**llm_kwargs)

    # =========================================================================
    # 7. Knowledge Base (RAG)
    # =========================================================================
    agent_knowledge = None
    has_session_knowledge = False
    try:
        session = get_session_safe(storage_instance, session_id, str(user.id))
    except Exception as e:
        session = None
        logger.warning(f"⚠️ [Factory] Session lookup failed for {session_id}: {e}")

    if session:
        knowledge_flag = get_session_knowledge_flag(session)
        apply_session_metadata_defaults(session)
        if knowledge_flag is None:
            try:
                has_session_knowledge = session_knowledge_exists(session_id)
                set_session_knowledge_metadata(session, has_session_knowledge, 1 if has_session_knowledge else 0)
                if hasattr(storage_instance, "upsert_session"):
                    storage_instance.upsert_session(session=session)
                else:
                    storage_instance.upsert(session=session)
            except Exception as e:
                logger.warning(f"⚠️ [Factory] Session knowledge backfill failed for {session_id}: {e}")
        else:
            has_session_knowledge = knowledge_flag

    if has_session_knowledge:
        try:
            agent_knowledge = get_session_knowledge(session_id)
            dynamic_instructions.append(
                "### ATTACHED FILE KNOWLEDGE (System Injected)\n"
                "If the user asks about an uploaded or attached file in this thread, you must use the "
                "`search_knowledge_base` tool before answering."
            )
        except Exception as e:
            logger.error(f"❌ [Factory] Session knowledge initialization failed: {e}")
    else:
        kb = service.knowledge_bases.first()
        if kb:
            try:
                table_name = get_sanitized_table_name(kb.name)
                vector_db = Qdrant(
                    collection=table_name,
                    url=settings.QDRANT_URL,
                    api_key=settings.QDRANT_API_KEY,
                    embedder=OpenAIEmbedder(id="text-embedding-3-small", **get_agno_openai_kwargs()),
                )
                agent_knowledge = Knowledge(vector_db=vector_db)
            except Exception as e:
                logger.error(f"❌ [Factory] Knowledge initialization failed: {e}")

    # =========================================================================
    # 7.5 Session Summary Manager
    # =========================================================================
    summary_manager = None
    if getattr(service, 'enable_session_summaries', False):
        summary_manager = TimedSessionSummaryManager(
            model=SanitizedOpenAIChat(id="gpt-4o-mini", **get_agno_openai_kwargs()),
            log_prefix=f"[Run {session_id}]",
        )
        logger.debug(f"   [Factory] 🧠 Session Summaries enabled for {session_id}")

    # =========================================================================
    # 8. ServiceAgent Instantiation
    # =========================================================================
    agent_instance = ServiceAgent(
        user=user,
        service_config=service,
        session_id=session_id,
        
        # LLM & Tools
        model=llm_model,
        storage=storage_instance, 
        tools=agent_tools,
        
        # Intelligence
        knowledge=agent_knowledge,
        search_knowledge=True if agent_knowledge else False,
        reasoning=use_agno_reasoning_wrapper,
        
        # Session Summaries
        session_summary_manager=summary_manager,
        
        # System Meta
        add_datetime_to_context=True,
        timezone_identifier="Asia/Tehran",
        
        # Prompts
        extra_instructions="\n".join(dynamic_instructions)
    )

    elapsed = time.time() - start_time
    logger.info(
        "✅ [Factory] Agent '%s' built in %.4fs. Model: %s (Source: %s, Swapped: %s, Transport: %s, Reasoning effort: %s)",
        service.name,
        elapsed,
        target_model_id,
        model_selection.source,
        is_swapped_model,
        "responses" if use_expert_responses else "chat_completions",
        final_effort or "provider-default",
    )
    
    return agent_instance
