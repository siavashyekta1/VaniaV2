# backend/agents/service_agent.py
import logging
import inspect
import json
import re
import traceback
import time
import asyncio
from decimal import Decimal
from typing import Optional, List, Any, AsyncGenerator

from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async

# --- Agno Imports ---
from agno.agent import Agent, RunEvent, AgentSession, RunOutput
from agno.models.message import Message

# --- Project Imports ---
from billing.services import process_usage_charge, calculate_credit_cost
from billing.models import UserWallet, BillingConfig
from users.eligibility import is_staff_or_admin_user
from core.ai_provider import get_agno_openai_kwargs
from .models import SanitizedOpenAIChat
from .prompt_culture import compose_agent_instructions
from .session_metadata import apply_session_metadata_defaults

try:
    from canvas.manager import canvas_manager
except ImportError:
    canvas_manager = None

logger = logging.getLogger("agno.billing")

FIELD_WRITE_AGENT_SLUGS = {"vania-expert-assistant"}


class InsufficientCreditsError(Exception):
    def __init__(self, message: str):
        self.title = "اعتبار گفتگو تمام شد"
        self.message = message or "برای ادامه، یک طرح یا بسته اعتبار تهیه کنید."
        super().__init__(self.message)


def _normalize_message_text(value: str) -> str:
    return (value or "").strip().replace("\r\n", "\n")


def _detect_expert_field_write_instruction(agent_id: str, message: Optional[str]) -> Optional[str]:
    if agent_id not in FIELD_WRITE_AGENT_SLUGS or not message:
        return None

    text = _normalize_message_text(message)
    lowered = text.lower()

    clinical_summary_terms = (
        "علت مراجعه",
        "علت مراجع",
        "مشاهدات",
        "clinical summary",
        "observations",
    )
    analysis_terms = (
        "تحلیل تست",
        "تحلیل بالینی",
        "تحلیل بالینی تست",
        "تحلیل بالینی تست‌ها",
        "تحلیل بالینی تست ها",
        "تحلیل بالینی تست‌ها و فرم‌ها",
        "تحلیل بالینی تست ها و فرم ها",
        "forms_tests_analysis",
    )
    write_terms = (
        "بنویس",
        "ثبت کن",
        "ذخیره کن",
        "پر کن",
        "اضافه کن",
        "بذار",
        "update",
        "write",
        "save",
        "fill",
        "record",
    )

    wants_clinical_summary = any(term in text for term in clinical_summary_terms)
    wants_analysis = any(term in text for term in analysis_terms)
    wants_write = any(term in lowered for term in write_terms) or wants_clinical_summary or wants_analysis

    if not wants_write or not (wants_clinical_summary or wants_analysis):
        return None

    tool_lines: list[str] = []
    if wants_clinical_summary:
        tool_lines.append("- You must call `update_clinical_summary(summary_text=...)` in this run.")
    if wants_analysis:
        tool_lines.append("- You must call `update_forms_tests_analysis(analysis_text=...)` in this run.")

    sample_hint = (
        "- The user accepts professional sample content. Draft the missing case text yourself and save it through the required tool call(s)."
        if re.search(r"تستی|نمونه|sample|dummy|mock|test data", lowered, flags=re.IGNORECASE)
        else "- If the user did not supply full text, generate concise professional content yourself and save it."
    )

    return "\n".join([
        "### DIRECT CASE FIELD WRITE (System Injected)",
        "- This user message is a direct request to write content into case fields, not a request for a chat draft.",
        "- Do not answer with prose before the tool call.",
        "- Read current case state first only if needed for safe writing; otherwise write directly.",
        *tool_lines,
        sample_hint,
        "- After successful tool calls, do not dump the saved text into chat.",
    ])


def _extract_token_counts(payload: Any) -> tuple[int, int]:
    if payload is None:
        return 0, 0

    if isinstance(payload, dict):
        data = payload
    elif hasattr(payload, "to_dict"):
        data = payload.to_dict()
    elif hasattr(payload, "model_dump"):
        data = payload.model_dump()
    elif hasattr(payload, "__dict__"):
        data = payload.__dict__
    else:
        return 0, 0

    input_tokens = data.get("input_tokens", data.get("prompt_tokens", 0)) or 0
    output_tokens = data.get("output_tokens", data.get("completion_tokens", 0)) or 0

    try:
        return int(input_tokens), int(output_tokens)
    except (TypeError, ValueError):
        return 0, 0


def _extract_model_usage_metrics(model: Any) -> dict[str, int]:
    usage = getattr(model, "last_usage_metrics", None)
    if not usage:
        return {}

    input_tokens, output_tokens = _extract_token_counts(usage)
    total_tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0

    try:
        total_tokens = int(total_tokens or 0)
    except (TypeError, ValueError):
        total_tokens = 0

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or (input_tokens + output_tokens),
    }

class ServiceAgent(Agent):
    
    def __init__(self, user, service_config, session_id: str = None, db=None, storage=None, extra_instructions: str = "", *args, **kwargs):
        self.log_prefix = f"[Run:{session_id}]"
        logger.debug(f"{self.log_prefix} 🏗️ Initializing ServiceAgent for User {user.id}...")

        self.user = user
        self.service_config = service_config
        self.session_id = session_id
        self.agent_id = service_config.slug
        
        base_prompt = compose_agent_instructions(
            service_config.system_prompt or "You are a helpful AI assistant.",
            extra_instructions,
        )

        db_to_use = storage or db
        target_model_id = service_config.model_id or "gpt-4o"
        injected_model = kwargs.pop("model", None)
        session_metadata = apply_session_metadata_defaults({"agent_id": service_config.slug})

        # Check for Summary Manager
        summary_manager = kwargs.get('session_summary_manager')
        has_summaries = summary_manager is not None

        init_kwargs = {
            "name": service_config.name,
            "instructions": base_prompt,
            "model": injected_model or SanitizedOpenAIChat(id=target_model_id, **get_agno_openai_kwargs()),
            "user_id": str(user.id),
            "session_id": session_id,
            
            # --- CONTEXT MANAGEMENT ---
            "read_chat_history": True,
            "add_history_to_context": True,
            
            # Reduced history window (relying on summary for older context)
            "num_history_runs": 3, 
            
            # Session Summaries
            "enable_session_summaries": has_summaries,
            "add_session_summary_to_context": has_summaries,
            
            # [FIXED] Removed invalid 'session_summary_prompt' argument
            
            "markdown": True,
            "add_dependencies_to_context": True,
        }

        # Compatibility logic
        parent_params = inspect.signature(Agent.__init__).parameters
        if 'session_type' in parent_params:
            init_kwargs["session_type"] = "agent"
        
        if db_to_use:
            if 'storage' in parent_params:
                init_kwargs['storage'] = db_to_use
            elif 'db' in parent_params:
                init_kwargs['db'] = db_to_use

        if 'memory' in kwargs: del kwargs['memory']
        if 'session_data' in kwargs: del kwargs['session_data']

        init_kwargs.update(kwargs)
        super().__init__(*args, **init_kwargs)
        
        # Snapshot metrics at startup to enable Delta Billing
        self._initial_session_metrics = self._snapshot_session_metrics()
        self._captured_run_metrics = None
        self._captured_usage_totals = {"input_tokens": 0, "output_tokens": 0}
        
        # Persist Metadata
        if db_to_use and session_id:
            try:
                existing_session = None
                try:
                    existing_session = db_to_use.get_session(session_id=session_id)
                except TypeError:
                    existing_session = db_to_use.get_session(session_id=session_id, session_type="agent")
                
                if existing_session:
                    if not existing_session.session_data:
                        existing_session.session_data = {}
                    apply_session_metadata_defaults(existing_session)
                    if 'agent_id' not in existing_session.session_data:
                        existing_session.session_data.update(session_metadata)
                        db_to_use.upsert_session(existing_session)
                else:
                    now = int(time.time())
                    new_sess = AgentSession(
                        session_id=session_id, user_id=str(self.user.id),
                        session_data=session_metadata, created_at=now, updated_at=now
                    )
                    db_to_use.upsert_session(new_sess)
            except Exception as e:
                logger.warning(f"⚠️ Metadata persistence warning: {e}")

    def _snapshot_session_metrics(self) -> dict:
        """Helper to get current total usage from memory/model."""
        try:
            if hasattr(self, 'get_session_metrics'):
                m = self.get_session_metrics()
                return m.to_dict() if hasattr(m, 'to_dict') else (m.__dict__ if m else {})
        except:
            pass
        return {}

    async def arun(
        self,
        message: str = None,
        images: Optional[List[Any]] = None,
        files: Optional[List[Any]] = None,
        retrieved_file_context: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator:
        run_log = f"[Run {self.session_id}]"
        
        full_response_accumulator = ""
        run_completed_successfully = False
        self._captured_run_metrics = None  # Reset for this run
        self._captured_usage_totals = {"input_tokens": 0, "output_tokens": 0}
        if hasattr(self.model, "reset_usage_metrics"):
            self.model.reset_usage_metrics()
        
        if message:
            logger.info(f"{run_log} 🟢 User Input: {message[:50]}...")
        
        try:
            logger.info(f"{run_log} 🎬 Starting Execution...")

            # 1. Access Check (No Estimation)
            can_run, reason = await self._check_access()
            if not can_run:
                raise InsufficientCreditsError(reason)

            # 2. Context Injection
            additional_messages = []
            field_write_instruction = _detect_expert_field_write_instruction(self.agent_id, message)
            previous_suppress_plaintext = getattr(self, "suppress_plaintext_response", False)
            self.suppress_plaintext_response = bool(field_write_instruction)
            if field_write_instruction:
                additional_messages.append(
                    Message(role="system", content=field_write_instruction, add_to_agent_memory=False)
                )
            if canvas_manager:
                try:
                    # Safe Method Call
                    canvas_str = ""
                    if hasattr(canvas_manager, 'get_llm_context_summary'):
                        canvas_str = await sync_to_async(canvas_manager.get_llm_context_summary)(self.session_id)
                    elif hasattr(canvas_manager, 'get_llm_context'):
                        canvas_str = await sync_to_async(canvas_manager.get_llm_context)(self.session_id)
                    
                    if canvas_str: 
                        additional_messages.append(Message(role="system", content=canvas_str))
                except Exception as e:
                    logger.error(f"{run_log} ⚠️ Canvas context error: {e}")

            if retrieved_file_context:
                additional_messages.append(
                    Message(role="system", content=retrieved_file_context, add_to_agent_memory=False)
                )

            if images: kwargs['images'] = images
            if files: kwargs['files'] = files
            kwargs['stream'] = True

            # [Pre-Run] Re-snapshot
            self._initial_session_metrics = self._snapshot_session_metrics()

            logger.info(f"{run_log} 🚀 Invoking LLM Stream...")

            previous_additional_input = getattr(self, "additional_input", None)
            self.additional_input = additional_messages or None
            try:
                # 3. Standard Execution
                async for chunk in super().arun(message, **kwargs):
                    yield chunk

                    # --- Metrics Capture ---
                    if isinstance(chunk, RunOutput) or (hasattr(chunk, 'metrics') and chunk.metrics):
                        m = chunk.metrics
                        if m:
                            self._captured_run_metrics = m
                            logger.debug(f"{run_log} 📦 Received Metrics Chunk: {m}")

                    if hasattr(chunk, 'usage') and chunk.usage:
                         usage_input_t, usage_output_t = _extract_token_counts(chunk.usage)
                         if usage_input_t or usage_output_t:
                             self._captured_usage_totals["input_tokens"] = max(
                                 self._captured_usage_totals["input_tokens"], usage_input_t
                             )
                             self._captured_usage_totals["output_tokens"] = max(
                                 self._captured_usage_totals["output_tokens"], usage_output_t
                             )
                         logger.debug(f"{run_log} 🔌 Raw OpenAI Usage Event: {chunk.usage}")

                    # --- Content Accumulation ---
                    if hasattr(chunk, 'event') and chunk.event == RunEvent.run_content:
                        if chunk.content: full_response_accumulator += str(chunk.content)
                    elif isinstance(chunk, str):
                        full_response_accumulator += chunk

                    # --- Tool Logging ---
                    if hasattr(chunk, 'event') and chunk.event == RunEvent.tool_call_started:
                         tool_name = getattr(chunk, 'tool_call', {}).get('function', {}).get('name', 'unknown')
                         logger.info(f"{run_log} 🛠️  Model is calling Tool: {tool_name}")

                run_completed_successfully = True

            finally:
                self.additional_input = previous_additional_input
                self.suppress_plaintext_response = previous_suppress_plaintext

        except GeneratorExit:
            logger.warning(f"{run_log} 🛑 Client Disconnected.")
            raise

        except Exception as e:
            logger.error(f"{run_log} ❌ Execution Critical Error: {e}")
            logger.error(traceback.format_exc())
            raise e
        
        finally:
            # 4. Precise Billing
            # Billing should not keep the stream open after content generation is done.
            # Run it in the background so the UI can unlock as soon as the model finishes.
            if run_completed_successfully:
                asyncio.create_task(self._finalize_billing(run_log))
            else:
                logger.warning(f"{run_log} ⚠️ Billing skipped because the run did not complete successfully.")

    @sync_to_async
    def _check_access(self) -> tuple[bool, str]:
        try:
            if is_staff_or_admin_user(self.user):
                return True, ""

            config = BillingConfig.load()
            daily_limit = config.daily_free_credits
            wallet, _ = UserWallet.objects.get_or_create(user=self.user)
            
            is_plan_active = wallet.is_plan_active
            
            if is_plan_active:
                available = wallet.balance_plan + wallet.balance_paid
                if available > Decimal("0.05"):
                    return True, ""
                else:
                    return False, "اعتبار اشتراک شما به پایان رسیده است."
            else:
                free_available = max(Decimal(0), daily_limit - wallet.daily_free_used)
                if free_available > Decimal("0.05"):
                    return True, ""
                else:
                    msg = "اعتبار رایگان روزانه شما تمام شده است."
                    if wallet.balance_paid > 0:
                        msg += " (برای استفاده از اعتبار ذخیره، طرح بخرید)"
                    return False, msg

        except Exception as e:
            logger.error(f"Wallet check failed: {e}")
            return False, "خطای بررسی کیف پول"

    async def _finalize_billing(self, log_prefix: str):
        """
        Billing Calculation Engine with Delta Logic.
        """
        input_t, output_t = 0, 0
        source = "Unknown"
        final_metrics = {}

        try:
            if self._captured_run_metrics:
                m = self._captured_run_metrics
                final_metrics = m if isinstance(m, dict) else m.__dict__
                source = "DirectCapture"
            else:
                current_total = self._snapshot_session_metrics()
                start_total = self._initial_session_metrics
                
                i_end = current_total.get('input_tokens', 0)
                i_start = start_total.get('input_tokens', 0)
                input_t = max(0, i_end - i_start)

                o_end = current_total.get('output_tokens', 0)
                o_start = start_total.get('output_tokens', 0)
                output_t = max(0, o_end - o_start)
                
                if input_t > 0 or output_t > 0:
                    source = "SessionDelta"
                    final_metrics = {'input_tokens': input_t, 'output_tokens': output_t}
                    logger.warning(f"{log_prefix} ⚠️ Used Delta Billing (End {i_end} - Start {i_start})")

            if not final_metrics and (
                self._captured_usage_totals["input_tokens"] > 0
                or self._captured_usage_totals["output_tokens"] > 0
            ):
                input_t = self._captured_usage_totals["input_tokens"]
                output_t = self._captured_usage_totals["output_tokens"]
                final_metrics = {"input_tokens": input_t, "output_tokens": output_t}
                source = "UsageEvent"

            if not final_metrics:
                model_metrics = _extract_model_usage_metrics(self.model)
                if model_metrics.get("input_tokens", 0) > 0 or model_metrics.get("output_tokens", 0) > 0:
                    input_t = model_metrics["input_tokens"]
                    output_t = model_metrics["output_tokens"]
                    final_metrics = model_metrics
                    source = "ModelUsageCache"

            if final_metrics:
                if input_t == 0 and output_t == 0:
                    input_t = final_metrics.get('input_tokens', final_metrics.get('prompt_tokens', 0))
                    output_t = final_metrics.get('output_tokens', final_metrics.get('completion_tokens', 0))

            if input_t > 0 or output_t > 0:
                logger.info(f"{log_prefix} --- 🧾 BILLING INVOICE ({source}) ---")
                logger.info(f"{log_prefix} Input : {input_t} tokens")
                logger.info(f"{log_prefix} Output: {output_t} tokens")
                logger.info(f"{log_prefix} Total : {input_t + output_t} tokens")
                
                try:
                    result = await sync_to_async(process_usage_charge)(
                        self.user, input_tokens=input_t, output_tokens=output_t, run_id=self.session_id
                    )
                    if result.get("success"):
                        cost = result.get('deducted', 0)
                        logger.info(
                            f"{log_prefix} 💸 DEBIT: {cost} Credits | New Balance: {result.get('new_daily_used', 0)}"
                        )
                    else:
                        logger.warning(
                            "%s ⚠️ Billing charge was not applied: message=%s shortfall=%s",
                            log_prefix,
                            result.get("message", "Unknown billing failure"),
                            result.get("shortfall", 0),
                        )
                except Exception as e:
                    logger.error(f"{log_prefix} ❌ DB Transaction Failed: {e}")
            else:
                logger.error(f"{log_prefix} ❌ ZERO TOKENS DETECTED. Billing Skipped.")

        except Exception as e:
            logger.error(f"{log_prefix} ❌ Critical Error in Billing: {e}")
            traceback.print_exc()
