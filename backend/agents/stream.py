# backend/agents/stream.py
import uuid
import logging
import json
import asyncio
from typing import AsyncGenerator, Any

from fastapi import Request
from asgiref.sync import sync_to_async

# --- Agno Imports ---
from agno.agent import RunEvent
from agno.run.agent import CustomEvent

# --- AG-UI Protocol Imports ---
from ag_ui.core import (
    RunAgentInput, 
    EventType, 
    RunStartedEvent, 
    RunFinishedEvent, 
    RunErrorEvent,
    TextMessageStartEvent, 
    TextMessageContentEvent, 
    TextMessageEndEvent,
    ToolCallStartEvent, 
    ToolCallArgsEvent, 
    ToolCallEndEvent, 
    ToolCallResultEvent,
    CustomEvent as AguiCustomEvent 
)
from ag_ui.encoder import EventEncoder

# --- Local Module Imports ---
from .utils import (
    parse_multimodal_input,
    extract_tool_info,
    extract_ui_attachment_metadata,
    build_branch_history_prompt,
    clean_internal_prompt_content,
    safe_get,
    safe_serialize,
)
from .storage import get_storage, get_session_safe
from agents.naming import title_generator
from .session_metadata import apply_session_metadata_defaults, get_session_knowledge_flag, set_session_knowledge_metadata
from services.rag_service import render_session_knowledge_context, session_knowledge_exists
from .service_agent import InsufficientCreditsError
from .tool_result_sanitizer import sanitize_tool_result_content

# Configure Logger
logger = logging.getLogger(__name__)

TOOL_ONLY_CHAT_AGENT_SLUGS = {"vania-expert-assistant"}
active_naming_tasks: set[str] = set()
ASSISTANT_OUTPUT_COMPLETE_SENTINEL = "__assistant_output_complete__"


def _extract_naming_messages(messages: list | None) -> list[dict[str, Any]]:
    """
    Build the smallest useful snapshot for title generation.
    We only need the opening user message, so naming can start in parallel
    with the first agent run instead of waiting for the full turn to finish.
    """
    if not messages:
        return []

    formatted_msgs: list[dict[str, Any]] = []
    for message in messages:
        msg = safe_serialize(message)
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and content:
            formatted_msgs.append({"role": "user", "content": content})
            break

    return formatted_msgs


async def background_naming_task(thread_id: str, user_id: str, agent_messages: list):
    """
    Runs in the background and updates the session title without blocking chat.
    """
    logger.info(f"🏷️ [Background] Starting auto-naming for {thread_id}...")
    try:
        from users.models import CustomUser
        user = await CustomUser.objects.aget(id=user_id) 
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, thread_id, user_id)
        
        if not session:
            logger.warning(f"⚠️ [Background] Session {thread_id} not found, skipping naming.")
            return

        s_data = safe_serialize(session)
        current_name = "New Conversation"
        if 'session_data' in s_data and s_data['session_data']:
            current_name = s_data['session_data'].get('name', "New Conversation")
        
        is_generic_name = current_name in ["New Conversation", "Untitled Session", "Untitled", "گفتگوی جدید"]
        
        if not agent_messages and hasattr(session, 'get_messages'):
            try: agent_messages = session.get_messages()
            except: pass
            
        user_msg_count = 0
        if agent_messages:
            for m in agent_messages:
                role = getattr(m, 'role', None)
                if not role and isinstance(m, dict):
                    role = m.get('role')
                
                if role == 'user':
                    user_msg_count += 1
        
        is_first_turn = user_msg_count <= 1

        if is_generic_name or is_first_turn:
            if agent_messages and len(agent_messages) > 0:
                formatted_msgs = []
                for m in agent_messages:
                    m_dict = safe_serialize(m)
                    formatted_msgs.append({
                        'role': m_dict.get('role'), 
                        'content': m_dict.get('content')
                    })
                
                # Naming is best-effort background work and should not occupy the
                # thread-sensitive executor that the main run may still need for
                # session persistence and teardown.
                new_title = await sync_to_async(title_generator.generate_title, thread_sensitive=False)(
                    formatted_msgs, 
                    user, 
                    thread_id
                )
                
                if new_title and new_title not in ["گفتگوی جدید", "Untitled"]:
                    if not session.session_data: session.session_data = {}
                    session.session_data["name"] = new_title
                    session.session_data["session_name"] = new_title
                    
                    if hasattr(storage, 'upsert_session'):
                        await sync_to_async(storage.upsert_session)(session=session)
                    else:
                        await sync_to_async(storage.upsert)(session=session)
                    
                    logger.info(f"✅ [Background] Renamed {thread_id} to '{new_title}'")
                else:
                    logger.info(f"   [Background] Generated title was generic, skipped.")
            else:
                logger.info("   [Background] Not enough messages to name yet.")
        else:
            logger.info(f"   [Background] Session '{current_name}' already named and has {user_msg_count} turns. Skipping.")

    except Exception as e:
        logger.error(f"❌ [Background] Naming failed: {e}")
    finally:
        active_naming_tasks.discard(thread_id)


def schedule_background_naming(thread_id: str, user_id: str, agent_messages: list) -> None:
    """
    Fire-and-forget wrapper so title generation never blocks the main run.
    Only one naming task per thread is allowed at a time.
    """
    if thread_id in active_naming_tasks:
        logger.info(f"🏷️ [Background] Naming already scheduled for {thread_id}, skipping duplicate trigger.")
        return

    active_naming_tasks.add(thread_id)
    asyncio.create_task(background_naming_task(thread_id, user_id, agent_messages))


async def persist_ui_attachments(agent, thread_id: str, attachment_metadata: list[dict], message_id: str | None = None):
    try:
        storage = get_storage()
        session = await sync_to_async(get_session_safe)(storage, thread_id, str(agent.user.id))
        if not session:
            return None

        if not session.session_data:
            session.session_data = {}

        if attachment_metadata:
            attachment_history = session.session_data.get("ui_attachments") or []
            attachment_history.append({"message_id": message_id, "attachments": attachment_metadata})
            session.session_data["ui_attachments"] = attachment_history

            if hasattr(storage, "upsert_session"):
                await sync_to_async(storage.upsert_session)(session=session)
            else:
                await sync_to_async(storage.upsert)(session=session)
        return session
    except Exception as e:
        logger.error(f"❌ [Stream] Failed to persist UI attachment metadata: {e}", exc_info=True)
        return None


async def resolve_session_knowledge_flag(agent, thread_id: str, session=None) -> bool:
    storage = get_storage()
    current_session = session
    if current_session is None:
        current_session = await sync_to_async(get_session_safe)(storage, thread_id, str(agent.user.id))
    if not current_session:
        return False

    knowledge_flag = get_session_knowledge_flag(current_session)
    if knowledge_flag is not None:
        return knowledge_flag

    apply_session_metadata_defaults(current_session)
    try:
        has_knowledge = await sync_to_async(session_knowledge_exists)(thread_id)
        set_session_knowledge_metadata(current_session, has_knowledge, 1 if has_knowledge else 0)
        if hasattr(storage, "upsert_session"):
            await sync_to_async(storage.upsert_session)(session=current_session)
        else:
            await sync_to_async(storage.upsert)(session=current_session)
        return has_knowledge
    except Exception as e:
        logger.warning(f"⚠️ [Stream] Session knowledge backfill failed for {thread_id}: {e}")
        return False


def _clean_message_list_internal_prompts(messages: list | None) -> bool:
    changed = False
    for message in messages or []:
        role = safe_get(message, "role")
        if role != "user":
            continue

        content = safe_get(message, "content")
        cleaned_content = clean_internal_prompt_content(content)
        if cleaned_content == content:
            continue

        if isinstance(message, dict):
            message["content"] = cleaned_content
        else:
            try:
                setattr(message, "content", cleaned_content)
            except Exception:
                continue
        changed = True
    return changed


async def clean_persisted_internal_prompts(agent, thread_id: str) -> None:
    """
    Agno persists the exact message passed to arun. When we wrap the user turn
    with branch-history context, replace that stored internal prompt with the
    real user-visible message before history is read back by the UI.
    """
    changed = False
    if hasattr(agent, "memory") and agent.memory and hasattr(agent.memory, "messages"):
        changed = _clean_message_list_internal_prompts(agent.memory.messages) or changed
    if hasattr(agent, "messages"):
        changed = _clean_message_list_internal_prompts(agent.messages) or changed

    storage = get_storage()
    session = await sync_to_async(get_session_safe)(storage, thread_id, str(agent.user.id))
    if not session:
        return

    if hasattr(session, "memory") and session.memory is not None and hasattr(session.memory, "messages"):
        changed = _clean_message_list_internal_prompts(session.memory.messages) or changed
    elif hasattr(session, "messages"):
        changed = _clean_message_list_internal_prompts(session.messages) or changed

    if changed:
        if hasattr(storage, "upsert_session"):
            await sync_to_async(storage.upsert_session)(session=session)
        else:
            await sync_to_async(storage.upsert)(session=session)


async def agui_stream_generator(
    agent, 
    input_data: RunAgentInput, 
    request: Request = None,
    is_demo_user: bool = False
) -> AsyncGenerator[str, None]:
    """
    Active Stream Generator:
    Runs the agent in a background task and monitors the client connection concurrently.
    If the client disconnects, the agent task is cancelled immediately.
    """
    from services.usage import demo_usage_service
    
    encoder = EventEncoder()
    thread_id = input_data.thread_id
    run_id = input_data.run_id
    assistant_msg_id = str(uuid.uuid4())
    
    # Track state for events
    always_suppress_text_output = bool(getattr(agent, "suppress_plaintext_response", False))
    state = {
        "is_text_started": False,
        "current_tool_id": None,
        "suppress_text_output": bool(
            always_suppress_text_output
            or getattr(getattr(agent, "service_config", None), "slug", None) in TOOL_ONLY_CHAT_AGENT_SLUGS
        ),
        "always_suppress_text_output": always_suppress_text_output,
        "tool_called": False,
        "buffered_text": [],
    }
    
    logger.info(f"🌊 [Stream] Starting Active Generator for Run: {run_id} (Thread: {thread_id})")

    naming_messages = _extract_naming_messages(input_data.messages)
    if naming_messages:
        schedule_background_naming(thread_id, str(agent.user.id), naming_messages)

    yield encoder.encode(
        RunStartedEvent(
            type=EventType.RUN_STARTED,
            thread_id=thread_id,
            run_id=run_id,
            input=input_data 
        )
    )

    # 1. Create a Queue to bridge the Agent's output to the Stream
    output_queue = asyncio.Queue()
    idle_output_complete_task: asyncio.Task | None = None
    output_complete_emitted = False

    def cancel_idle_output_complete_task() -> None:
        nonlocal idle_output_complete_task
        if idle_output_complete_task and not idle_output_complete_task.done():
            idle_output_complete_task.cancel()
        idle_output_complete_task = None

    def schedule_idle_output_complete() -> None:
        nonlocal idle_output_complete_task, output_complete_emitted
        if output_complete_emitted or state["current_tool_id"]:
            return

        cancel_idle_output_complete_task()

        async def emit_when_idle() -> None:
            nonlocal output_complete_emitted
            try:
                await asyncio.sleep(1.0)
                if output_complete_emitted or state["current_tool_id"]:
                    return
                output_complete_emitted = True
                await output_queue.put(ASSISTANT_OUTPUT_COMPLETE_SENTINEL)
            except asyncio.CancelledError:
                return

        idle_output_complete_task = asyncio.create_task(emit_when_idle())
    
    # 2. Define the Background Agent Task
    async def run_agent_task():
        try:
            prompt, images, files = parse_multimodal_input(input_data)
            branch_history_prompt = build_branch_history_prompt(input_data.messages or [])
            ui_attachments = extract_ui_attachment_metadata(input_data)
            last_message = (input_data.messages or [])[-1] if input_data.messages else None
            last_message_id = safe_get(last_message, "id") if last_message else None
            session = await persist_ui_attachments(
                agent,
                thread_id,
                ui_attachments,
                str(last_message_id) if last_message_id else None,
            )

            retrieved_file_context = ""
            if prompt and await resolve_session_knowledge_flag(agent, thread_id, session):
                retrieved_file_context = await sync_to_async(render_session_knowledge_context)(thread_id, prompt)

            final_prompt = prompt
            if branch_history_prompt:
                final_prompt = f"{branch_history_prompt}\n\n<current_user_message>\n{prompt}\n</current_user_message>"

            original_read_chat_history = getattr(agent, "read_chat_history", None)
            original_add_history_to_context = getattr(agent, "add_history_to_context", None)
            original_add_session_summary_to_context = getattr(agent, "add_session_summary_to_context", None)
            original_num_history_runs = getattr(agent, "num_history_runs", None)
            if original_read_chat_history is not None:
                agent.read_chat_history = False
            if original_add_history_to_context is not None:
                agent.add_history_to_context = False
            if original_add_session_summary_to_context is not None:
                agent.add_session_summary_to_context = False
            if original_num_history_runs is not None:
                agent.num_history_runs = 0

            try:
                # Run the agent and put chunks into queue
                # stream_events=True ensures we get Tool Calls, Content, and CustomEvents
                async for chunk in agent.arun(
                    message=final_prompt,
                    images=images if images else None,
                    files=files if files else None,
                    retrieved_file_context=retrieved_file_context or None,
                    stream=True, 
                    stream_events=True
                ):
                    await output_queue.put(chunk)
            finally:
                if original_read_chat_history is not None:
                    agent.read_chat_history = original_read_chat_history
                if original_add_history_to_context is not None:
                    agent.add_history_to_context = original_add_history_to_context
                if original_add_session_summary_to_context is not None:
                    agent.add_session_summary_to_context = original_add_session_summary_to_context
                if original_num_history_runs is not None:
                    agent.num_history_runs = original_num_history_runs

            await clean_persisted_internal_prompts(agent, thread_id)
            
            # Signal completion
            await output_queue.put("DONE")
            
        except asyncio.CancelledError:
            logger.warning(f"🛑 [StreamTask] Agent execution cancelled.")
            raise
        except Exception as e:
            logger.error(f"❌ [StreamTask] Error: {e}", exc_info=True)
            await output_queue.put(e) # Pass error to main loop

    # 3. Start Agent
    agent_task = asyncio.create_task(run_agent_task())

    # 4. Helper for Request Disconnect Polling
    async def wait_for_disconnect():
        if not request:
            return False
        while True:
            if await request.is_disconnected():
                return True
            await asyncio.sleep(0.5)

    disconnect_task = asyncio.create_task(wait_for_disconnect())

    try:
        # 5. Main Event Loop
        while True:
            queue_task = asyncio.create_task(output_queue.get())
            
            done, pending = await asyncio.wait(
                [queue_task, disconnect_task], 
                return_when=asyncio.FIRST_COMPLETED
            )

            # --- Scenario A: Client Disconnected ---
            if disconnect_task in done:
                logger.warning(f"🛑 [Stream] Client Disconnected. Killing Agent Task.")
                agent_task.cancel() # Kill the LLM/Tools
                
                # Update DB state explicitly
                try:
                    await sync_to_async(agent.cancel_run)(run_id)
                except Exception as e:
                    logger.error(f"   [Stream] Failed to mark DB cancelled: {e}")
                break

            # --- Scenario B: Data Received ---
            if queue_task in done:
                item = queue_task.result()
                
                if item == ASSISTANT_OUTPUT_COMPLETE_SENTINEL:
                    logger.info("🎨 [Stream] Emitting idle output-complete event.")
                    yield encoder.encode(
                        AguiCustomEvent(
                            type=EventType.CUSTOM,
                            name="assistant_output_complete",
                            value={"thread_id": thread_id, "run_id": run_id},
                        )
                    )
                    continue

                if item == "DONE":
                    break
                
                if isinstance(item, InsufficientCreditsError):
                    yield encoder.encode(
                        AguiCustomEvent(
                            type=EventType.CUSTOM,
                            name="billing_required",
                            value={
                                "title": item.title,
                                "message": "برای ادامه، یک طرح یا بسته اعتبار تهیه کنید.",
                                "detail": item.message,
                                "source": "chat",
                            },
                        )
                    )
                    break

                if isinstance(item, Exception):
                    yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=str(item)))
                    break

                # Process the chunk
                chunk = item 
                
                # --- CASE A: Native Agno Custom Events (e.g. CanvasUpdateEvent) ---
                # Check for direct object or wrapper depending on Agno version nuances
                custom_evt = None
                if isinstance(chunk, CustomEvent):
                    custom_evt = chunk
                elif hasattr(chunk, 'event_type') and chunk.event_type == "custom_event":
                    # Hypothetical wrapper handling if Agno internals wrap it
                    custom_evt = getattr(chunk, 'event', None)

                if custom_evt:
                    logger.info(f"🎨 [Stream] Emitting Custom Event: {custom_evt.name}")
                    event_obj = AguiCustomEvent(
                        type=EventType.CUSTOM,
                        name=custom_evt.name,
                        value=custom_evt.value
                    )
                    yield encoder.encode(event_obj)
                    continue

                # --- CASE B: Raw String ---
                if not hasattr(chunk, "event"):
                    if isinstance(chunk, str) and chunk.strip():
                        if state["suppress_text_output"]:
                            state["buffered_text"].append(chunk)
                        else:
                            if not state["is_text_started"]:
                                yield encoder.encode(TextMessageStartEvent(
                                    type=EventType.TEXT_MESSAGE_START, message_id=assistant_msg_id, role="assistant"
                                ))
                                state["is_text_started"] = True
                            yield encoder.encode(TextMessageContentEvent(
                                type=EventType.TEXT_MESSAGE_CONTENT, message_id=assistant_msg_id, delta=chunk
                            ))
                    continue

                # --- CASE C: Standard Events ---
                if chunk.event == RunEvent.run_content:
                    if chunk.content:
                        if state["suppress_text_output"]:
                            state["buffered_text"].append(str(chunk.content))
                        else:
                            if not state["is_text_started"]:
                                yield encoder.encode(TextMessageStartEvent(
                                    type=EventType.TEXT_MESSAGE_START, message_id=assistant_msg_id, role="assistant"
                                ))
                                state["is_text_started"] = True
                            
                            yield encoder.encode(TextMessageContentEvent(
                                type=EventType.TEXT_MESSAGE_CONTENT, message_id=assistant_msg_id, delta=str(chunk.content)
                            ))

                elif chunk.event == RunEvent.tool_call_started:
                    cancel_idle_output_complete_task()
                    tc_id, tc_name, tc_args = extract_tool_info(chunk)
                    if not tc_id or tc_id == "unknown_id": tc_id = str(uuid.uuid4())
                    state["current_tool_id"] = tc_id
                    state["tool_called"] = True
                    
                    yield encoder.encode(ToolCallStartEvent(
                        type=EventType.TOOL_CALL_START, tool_call_id=tc_id, tool_call_name=tc_name, parent_message_id=assistant_msg_id
                    ))
                    
                    tc_args_str = "{}"
                    if tc_args:
                        if isinstance(tc_args, (dict, list)):
                            try: tc_args_str = json.dumps(tc_args, ensure_ascii=False)
                            except: tc_args_str = str(tc_args)
                        else: tc_args_str = str(tc_args)

                    yield encoder.encode(ToolCallArgsEvent(
                        type=EventType.TOOL_CALL_ARGS, tool_call_id=tc_id, delta=tc_args_str
                    ))

                elif chunk.event == RunEvent.tool_call_completed:
                    tc_id, tc_name, _ = extract_tool_info(chunk)
                    if (not tc_id or tc_id == "unknown_id") and state["current_tool_id"]:
                        tc_id = state["current_tool_id"]
                    
                    if not tc_id: continue

                    raw_content = None
                    tool_obj = getattr(chunk, "tool", None)
                    if tool_obj: raw_content = getattr(tool_obj, "result", None)
                    
                    if raw_content is None:
                        output_obj = getattr(chunk, "tool_output", None)
                        if output_obj: raw_content = getattr(output_obj, "content", output_obj)

                    if raw_content is None:
                        outputs_list = getattr(chunk, "tool_outputs", None)
                        if outputs_list and isinstance(outputs_list, list) and len(outputs_list) > 0:
                            raw_content = getattr(outputs_list[0], "content", outputs_list[0])

                    if raw_content is None:
                        tc_obj = getattr(chunk, "tool_call", None)
                        if tc_obj: raw_content = getattr(tc_obj, "result", None)

                    content_str = sanitize_tool_result_content(raw_content)

                    yield encoder.encode(ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT, message_id=assistant_msg_id, tool_call_id=tc_id, content=content_str, role="tool"
                    ))
                    
                    yield encoder.encode(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tc_id))
                    state["current_tool_id"] = None

        # --- Completion Logic ---
        if state["suppress_text_output"] and not state["tool_called"] and not state["always_suppress_text_output"]:
            buffered_text = "".join(state["buffered_text"]).strip()
            if buffered_text:
                yield encoder.encode(TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START, message_id=assistant_msg_id, role="assistant"
                ))
                state["is_text_started"] = True
                yield encoder.encode(TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT, message_id=assistant_msg_id, delta=buffered_text
                ))

        if state["is_text_started"]:
            yield encoder.encode(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_msg_id))

        # This event changes the frontend's run state to completed. Emitting it
        # during a pause between reasoning/tool calls causes later events to be
        # ignored and leaves the UI out of sync. Emit it exactly once, only
        # after the agent has actually finished producing output.
        if not output_complete_emitted:
            output_complete_emitted = True
            yield encoder.encode(
                AguiCustomEvent(
                    type=EventType.CUSTOM,
                    name="assistant_output_complete",
                    value={"thread_id": thread_id, "run_id": run_id},
                )
            )

        if is_demo_user:
            try:
                await demo_usage_service.increment_usage(agent.user, agent.service_config)
            except Exception as usage_err:
                logger.error(f"⚠️ [Stream] Failed to increment demo usage: {usage_err}")

        latest_messages = []
        if hasattr(agent, "memory") and agent.memory and hasattr(agent.memory, "messages"):
            latest_messages = agent.memory.messages or []
        elif hasattr(agent, "messages"):
            latest_messages = agent.messages or []

        # Re-apply naming after the run has fully persisted. Some downstream
        # session saves can overwrite the early background rename with the
        # initial generic title, so we schedule one more non-blocking pass here.
        final_naming_messages = latest_messages or naming_messages
        if final_naming_messages:
            schedule_background_naming(thread_id, str(agent.user.id), final_naming_messages)
                
        yield encoder.encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=thread_id,
                run_id=run_id
            )
        )
        logger.info(f"🏁 [Stream] Run {run_id} Completed.")

    except (GeneratorExit, asyncio.CancelledError):
        logger.warning(f"🛑 [Stream] Generator Closed/Cancelled.")
        agent_task.cancel()
        await sync_to_async(agent.cancel_run)(run_id)
    except Exception as e:
        logger.error(f"❌ [Stream] Critical Loop Error: {e}", exc_info=True)
        agent_task.cancel()
        yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=str(e)))
    finally:
        # Cleanup tasks
        cancel_idle_output_complete_task()
        if not agent_task.done():
            agent_task.cancel()
        if not disconnect_task.done():
            disconnect_task.cancel()
