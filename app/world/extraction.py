"""
World-model proposal extraction.

After each agent run, this module analyses the conversation with a cheap
background model and proposes structured world-model updates.  Low-risk
proposals (aliases, device facts) auto-apply at high confidence; others
queue for admin review.

Called as a fire-and-forget coroutine from bot.py — never blocks the response.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, TextPart, UserPromptPart

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a world-model extraction assistant for a household AI.

The household maintains a structured world model with entities:
  members, places, devices, routines, facts (key-value), aliases, interests, activities, goals.

Review the conversation and extract structured updates the user is stating
explicitly or implicitly.  Only extract things that are durable household
facts — not temporary states, not device readings, not weather.

For each proposal, set:
- proposal_type: one of "fact", "alias", "interest", "activity", "goal", "routine"
- payload: a JSON object with the relevant fields (see examples below)
- reason: a one-sentence explanation of why this should be stored
- confidence: 0.0–1.0 (how certain you are this is a real, lasting fact)

Payload examples by type:
  fact:     {"scope": "device", "key": "tibber_pulse.purpose", "value": "total house power"}
  alias:    {"entity_type": "place", "entity_name": "Office", "alias": "kontor"}
  interest: {"member_name": "Sondre", "name": "football", "notes": "plays striker"}
  activity: {"member_name": "Sondre", "name": "football practice", "schedule_hint": "Tue/Thu 17:00"}
  goal:     {"member_name": "Kristian", "name": "reduce energy usage"}
  routine:  {"name": "night mode", "description": "all lights off, heating unchanged", "kind": "mode"}

IMPORTANT:
- Do NOT propose things already present in the existing world model (provided below).
- Do NOT extract current device states, sensor readings, or ephemeral information.
- Do NOT extract time, date, weather, or anything temporary.
- Return an empty proposals list if nothing qualifies.
- Prefer high confidence only when the user explicitly states the fact.
"""


class _Proposal(BaseModel):
    proposal_type: str
    payload: dict
    reason: str
    confidence: float = 0.5


class _Proposals(BaseModel):
    proposals: list[_Proposal]


_extractor: Agent[None, _Proposals] | None = None


def _get_extractor() -> Agent[None, _Proposals]:
    global _extractor
    if _extractor is None:
        from app.agent.llm_router import LLMRouter, TaskType

        model = LLMRouter().get_model(TaskType.WORLD_MODEL_EXTRACTION)
        _extractor = Agent(model=model, output_type=_Proposals, system_prompt=_SYSTEM_PROMPT)
    return _extractor


def _messages_to_text(messages: list[ModelMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, UserPromptPart):
                lines.append(f"User: {part.content}")
            elif isinstance(part, TextPart):
                lines.append(f"Assistant: {part.content}")
    return "\n".join(lines)


# Types eligible for auto-apply at high confidence
_AUTO_APPLY_TYPES = {"alias", "fact"}
_AUTO_APPLY_THRESHOLD = 0.85


async def extract_and_propose_world_updates(
    household_id: str,
    user_id: str,
    run_id: str,
    new_messages: list[ModelMessage],
    world_model_text: str = "",
) -> None:
    """
    Background task: extract world-model proposals from a conversation run.

    High-confidence aliases and facts auto-apply; everything else queues for
    admin review.  Silently swallows all errors.
    """
    if not new_messages:
        return

    text = _messages_to_text(new_messages)
    if not text.strip():
        return

    # Include existing world model so the extractor avoids duplicates
    prompt = text
    if world_model_text:
        prompt = (
            f"<existing_world_model>\n{world_model_text}\n</existing_world_model>\n\n"
            f"<conversation>\n{text}\n</conversation>"
        )

    try:
        result = await _get_extractor().run(prompt)
        proposals = result.output.proposals
    except Exception:
        logger.warning("World-model extraction failed for run %s", run_id[:8], exc_info=True)
        return

    if not proposals:
        return

    from app.control.events import emit
    from app.world.repository import WorldModelRepository

    repo = WorldModelRepository()
    created = 0

    for prop in proposals:
        ptype = prop.proposal_type.strip().lower()
        conf = max(0.0, min(1.0, prop.confidence))

        # Auto-apply high-confidence aliases and facts
        auto = ptype in _AUTO_APPLY_TYPES and conf >= _AUTO_APPLY_THRESHOLD
        status = "auto_applied" if auto else "pending"

        try:
            p = repo.create_proposal(
                household_id=household_id,
                proposal_type=ptype,
                payload=prop.payload,
                reason=prop.reason,
                confidence=conf,
                source_run_id=run_id,
                status=status,
            )
            created += 1

            if auto:
                _apply_proposal(repo, household_id, ptype, prop.payload, run_id)

            emit(
                "world.proposal",
                {
                    "proposal_id": p.id,
                    "type": ptype,
                    "status": status,
                    "confidence": conf,
                    "reason": prop.reason,
                },
                run_id=run_id,
            )
        except Exception:
            logger.warning("Failed to store proposal: %s", prop.reason[:80], exc_info=True)

    if created:
        logger.info(
            "World-model extraction: %d proposal(s) from run %s",
            created,
            run_id[:8],
        )


def _apply_proposal(
    repo: WorldModelRepository,
    household_id: str,
    ptype: str,
    payload: dict,
    run_id: str,
) -> None:
    """Apply an auto-approved proposal to the world model."""
    try:
        if ptype == "fact":
            repo.upsert_world_fact(
                household_id=household_id,
                scope=payload.get("scope", "household"),
                key=payload["key"],
                value_json=json.dumps(payload["value"]) if not isinstance(payload["value"], str) else payload["value"],
                source="agent_inferred",
                confidence=0.85,
            )
        elif ptype == "alias":
            entity_type = payload.get("entity_type", "")
            entity_name = payload.get("entity_name", "")
            alias = payload.get("alias", "")
            # Resolve entity name to ID
            finder = {
                "member": repo.find_member_by_name,
                "place": repo.find_place_by_name,
                "device": repo.find_device_by_name,
            }.get(entity_type)
            if finder and alias:
                entity = finder(household_id, entity_name)
                if entity:
                    repo.add_alias(household_id, entity_type, entity.id, alias)
    except Exception:
        logger.warning("Failed to auto-apply %s proposal", ptype, exc_info=True)
