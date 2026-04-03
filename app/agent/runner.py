"""
Unified agent execution path.

agent_run() is the single function all entry points use: user messages,
task resumes, and scheduled prompts all go through the same pipeline:

  assemble context → run conversation → emit events → persist → background tasks

Per-user locking (get_user_run_lock) covers ALL triggers so that a background
job and a concurrent user message for the same user are never interleaved.

Callers are responsible for:
- Acquiring get_user_run_lock(user_id) before calling agent_run().
- Delivering the response to the user (send_message).
- Trigger-specific pre/post work (task state transitions, preflight checks, etc.).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Per-user run lock.  Keyed by internal user_id (UUID str), not telegram_id.
# Shared across ALL triggers so a background job and a user message for the
# same user never run concurrently and context always sees consistent history.
_user_run_locks: dict[str, asyncio.Lock] = {}

_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})


def get_user_run_lock(user_id: str) -> asyncio.Lock:
    """Return the per-user asyncio.Lock, creating it on first access."""
    if user_id not in _user_run_locks:
        _user_run_locks[user_id] = asyncio.Lock()
    return _user_run_locks[user_id]


@dataclass
class RunOutcome:
    response: str
    success: bool
    duration_ms: int
    run_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    # Stripped new messages (binary replaced with text labels) — callers can
    # use these for snapshot updates, further processing, etc.
    new_messages: list = field(default_factory=list)


async def agent_run(
    *,
    text: str,
    user_id: str,
    household_id: str,
    channel_user_id: str,
    run_id: str | None = None,
    trigger: str = "user_message",
    user_name: str = "",
    household_name: str = "the household",
    media: list | None = None,
    save_history: bool = False,
    retries: int = 0,
    on_retry: Callable[[int], Awaitable[None]] | None = None,
    control_task_id: str | None = None,
) -> RunOutcome:
    """
    Core agent execution function used by all entry points.

    Assembles the full context, runs the conversation agent, emits
    control-plane events, writes an AgentRunLog, optionally persists the
    conversation turn, and fires background memory / world-model tasks.

    Args:
        text: Input text / prompt for this run.
        user_id: Internal user UUID.
        household_id: Household UUID.
        channel_user_id: Channel-specific ID (e.g. str(telegram_id)).
        run_id: Correlation ID — generated if not provided.
        trigger: Origin label used in events (user_message, task_resume,
                 scheduled_prompt, …).
        user_name: Display name — resolved from DB if empty.
        household_name: Display name — resolved from DB if empty.
        media: Optional media attachments (user_message trigger only).
        save_history: If True, persist the conversation turn so future LLM
                      calls see it in message history.
        retries: Number of retries on retryable HTTP / timeout errors.
        on_retry: Async callback invoked before each retry; receives the
                  0-based attempt index that is about to be retried.

    NOTE: Callers must acquire get_user_run_lock(user_id) before calling.
    """
    import random

    from pydantic_ai.exceptions import ModelHTTPError

    from app.agent.agent import run_conversation
    from app.agent.context import AgentContext, assemble_context
    from app.agent.llm_router import LLMRouter, TaskType
    from app.config import get_settings
    from app.control.events import emit
    from app.db import users_session
    from app.models.users import Household, User

    if run_id is None:
        run_id = str(uuid.uuid4())

    settings = get_settings()

    # --- Resolve display names from DB when not supplied by caller ---
    if not user_name or not household_name or household_name == "the household":
        try:
            with users_session() as session:
                _user = session.get(User, user_id)
                _hh = session.get(Household, household_id)
                if _user:
                    user_name = _user.name
                if _hh:
                    household_name = _hh.name
        except Exception:
            logger.warning("agent_run: could not resolve user/household names", exc_info=True)

    # --- Model name for logging (best-effort) ---
    try:
        model_name = str(LLMRouter(settings).get_model(TaskType.CONVERSATION))
    except Exception:
        model_name = "unknown"

    # --- Assemble full context (profile, world model, memories, history) ---
    ctx: AgentContext = assemble_context(user_id, household_id, text)

    ctx_chars = _estimate_context_chars(ctx)
    emit(
        "run.start",
        {
            "trigger": trigger,
            "user_name": user_name,
            "model": model_name,
            "ctx_chars": ctx_chars,
            "ctx_tokens": ctx_chars // 4,
            "msg_count": len(ctx.recent_messages),
        },
        run_id=run_id,
    )

    # --- Run with optional retry on transient errors ---
    t_start = time.monotonic()
    result = None
    success = False
    response = ""

    for attempt in range(retries + 1):
        try:
            result = await run_conversation(
                text,
                user_name=user_name,
                household_name=household_name,
                message_history=ctx.recent_messages,
                user_profile_text=ctx.user_profile_text,
                household_profile_text=ctx.household_profile_text,
                world_model_text=ctx.world_model_text,
                active_task_text=ctx.active_task_text,
                conversation_summary=ctx.conversation_summary,
                relevant_memories=ctx.relevant_memories,
                user_id=user_id,
                household_id=household_id,
                channel_user_id=channel_user_id,
                run_id=run_id,
                control_task_id=control_task_id or "",
                media=media or [],
            )
            response = str(result.output)
            success = True
            break
        except (ModelHTTPError, asyncio.TimeoutError) as exc:
            is_retryable = (
                isinstance(exc, ModelHTTPError) and exc.status_code in _RETRYABLE_STATUS
            ) or isinstance(exc, asyncio.TimeoutError)
            if is_retryable and attempt < retries:
                wait = min(5 * (2**attempt) + random.uniform(0, 2), 30)
                logger.warning(
                    "agent_run: retryable error attempt=%d trigger=%s (%s) — retrying in %.1fs",
                    attempt + 1,
                    trigger,
                    type(exc).__name__,
                    wait,
                )
                if on_retry:
                    try:
                        await on_retry(attempt)
                    except Exception:
                        pass
                await asyncio.sleep(wait)
                continue
            logger.exception(
                "agent_run: run failed trigger=%s attempt=%d", trigger, attempt
            )
            response = "Sorry, something went wrong. Please try again in a moment."
            break
        except Exception:
            logger.exception(
                "agent_run: run failed trigger=%s attempt=%d", trigger, attempt
            )
            response = "Sorry, something went wrong. Please try again in a moment."
            break

    duration_ms = int((time.monotonic() - t_start) * 1000)

    # --- Extract tool calls and token usage from new messages ---
    raw_new_messages: list = []
    tool_calls: list[dict[str, object]] = []
    input_tokens = 0
    output_tokens = 0

    if result is not None:
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        raw_new_messages = list(result.new_messages())
        for msg in raw_new_messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, ToolCallPart):
                        try:
                            args: dict[str, object] = part.args_as_dict()
                        except Exception:
                            args = {}
                        tool_calls.append({"tool": part.tool_name, "args": args})
        usage = result.usage()
        input_tokens = usage.request_tokens or 0
        output_tokens = usage.response_tokens or 0

    # Strip binary content before any persistence or exposure to callers
    new_messages = _strip_binary_from_messages(raw_new_messages)

    # --- Emit run.complete / run.error ---
    if success:
        if input_tokens > settings.token_cost_warn_threshold:
            logger.warning(
                "High token usage: input_tokens=%d trigger=%s", input_tokens, trigger
            )
            emit("run.token_warning", {"input_tokens": input_tokens}, run_id=run_id)
        emit(
            "run.complete",
            {
                "trigger": trigger,
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tool_count": len(tool_calls),
                "tools": [str(t["tool"]) for t in tool_calls],
            },
            run_id=run_id,
        )
    else:
        emit(
            "run.error",
            {"trigger": trigger, "error": "Agent run failed", "duration_ms": duration_ms},
            run_id=run_id,
        )

    # --- Write AgentRunLog ---
    _write_run_log(
        household_id=household_id,
        user_id=user_id,
        model_used=model_name,
        input_summary=(text or "")[:200],
        output_summary=response[:200],
        tools_called=tool_calls,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    # --- Optionally persist conversation turn for future LLM context ---
    if save_history and new_messages:
        try:
            from app.memory.conversation import save_conversation_turn

            save_conversation_turn(user_id, new_messages)
        except Exception:
            logger.warning("agent_run: failed to save conversation turn", exc_info=True)

    # --- Fire background memory / world-model tasks ---
    if success and new_messages:
        _fire_background_tasks(
            household_id=household_id,
            user_id=user_id,
            run_id=run_id,
            new_messages=new_messages,
            world_model_text=ctx.world_model_text,
        )

    return RunOutcome(
        response=response,
        success=success,
        duration_ms=duration_ms,
        run_id=run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        new_messages=new_messages,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_context_chars(ctx: object) -> int:
    """Rough character count of all context that will be fed to the agent."""
    from app.agent.context import AgentContext

    if not isinstance(ctx, AgentContext):
        return 0

    from app.agent.prompts import load_instructions, load_persona
    from app.config import get_settings

    settings = get_settings()
    prompt_vars: dict[str, str] = {
        "agent_name": settings.agent_name,
        "household_name": "",
        "user_name": "",
        "current_date": "",
        "current_time": "",
        "timezone": settings.household_timezone,
    }
    total = len(load_persona(prompt_vars)) + len(load_instructions(prompt_vars))
    total += len(ctx.user_profile_text)
    total += len(ctx.household_profile_text)
    total += len(ctx.world_model_text or "")
    total += len(ctx.active_task_text or "")
    total += len(ctx.conversation_summary or "")
    total += sum(len(m) for m in ctx.relevant_memories)
    for msg in ctx.recent_messages:
        for part in msg.parts:
            if hasattr(part, "content"):
                total += len(str(part.content))
    return total


def _strip_binary_from_messages(messages: list) -> list:
    """Replace BinaryContent in UserPromptParts with text labels."""
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    result = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            result.append(msg)
            continue
        new_parts = []
        for part in msg.parts:
            if not isinstance(part, UserPromptPart):
                new_parts.append(part)
                continue
            content = part.content
            if isinstance(content, str):
                new_parts.append(part)
                continue
            new_content: list = []
            for item in content:
                if isinstance(item, BinaryContent):
                    new_content.append(f"[{item.media_type} attached]")
                else:
                    new_content.append(item)
            if len(new_content) == 1 and isinstance(new_content[0], str):
                new_parts.append(UserPromptPart(content=new_content[0], timestamp=part.timestamp))
            else:
                new_parts.append(UserPromptPart(content=new_content, timestamp=part.timestamp))
        result.append(ModelRequest(parts=new_parts))
    return result


def _write_run_log(
    *,
    household_id: str,
    user_id: str,
    model_used: str,
    input_summary: str,
    output_summary: str,
    tools_called: list[dict[str, object]],
    duration_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    try:
        import json as _json

        from app.db import cache_session
        from app.models.cache import AgentRunLog

        with cache_session() as session:
            log = AgentRunLog(
                household_id=household_id,
                user_id=user_id,
                model_used=model_used,
                input_summary=input_summary,
                output_summary=output_summary,
                tools_called=_json.dumps(tools_called),
                duration_ms=duration_ms,
                tokens_used=_json.dumps({"input": input_tokens, "output": output_tokens}),
            )
            session.add(log)
            session.commit()
    except Exception:
        logger.warning("agent_run: failed to write AgentRunLog", exc_info=True)


def _fire_background_tasks(
    *,
    household_id: str,
    user_id: str,
    run_id: str,
    new_messages: list,
    world_model_text: str,
) -> None:
    from app.config import get_settings
    from app.memory.conversation import maybe_summarize_conversation
    from app.memory.extraction import extract_and_store_memories

    def _done_cb(label: str):  # noqa: ANN202
        def _cb(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
            if not fut.cancelled() and (exc := fut.exception()):
                logger.error("Background task %r failed: %s", label, exc, exc_info=exc)
                from app.control.events import emit

                emit("run.background_error", {"task": label, "error": str(exc)}, run_id=run_id)

        return _cb

    asyncio.ensure_future(
        extract_and_store_memories(
            household_id=household_id,
            user_id=user_id,
            run_id=run_id,
            new_messages=new_messages,
        )
    ).add_done_callback(_done_cb("extract_memories"))

    asyncio.ensure_future(
        maybe_summarize_conversation(user_id)
    ).add_done_callback(_done_cb("summarize_conversation"))

    if get_settings().features.world_model_proposals:
        from app.world.extraction import extract_and_propose_world_updates

        asyncio.ensure_future(
            extract_and_propose_world_updates(
                household_id=household_id,
                user_id=user_id,
                run_id=run_id,
                new_messages=new_messages,
                world_model_text=world_model_text,
            )
        ).add_done_callback(_done_cb("world_model_extraction"))
