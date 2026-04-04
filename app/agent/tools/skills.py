from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_skills_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach skill lookup tools to the conversation agent."""

    @agent.tool
    async def get_skill(ctx: RunContext[AgentDeps], name: str) -> str:
        """Load full workflow guidance for a named skill.

        Call this before using a skill for the first time in a conversation, or
        when you need complete API, script, and workflow instructions for a skill
        listed under ## Available Skills in your system prompt.

        Args:
            name: The skill name exactly as listed in ## Available Skills,
                e.g. \"metno-norway-weather\" or \"vegvesen-datex\".
        """
        from app.agent.skills import get_skill_registry

        registry = get_skill_registry()
        content = registry.get_content(name)
        if content is None:
            available = ", ".join(s.name for s in registry.list()) or "none"
            return f"Skill {name!r} not found. Available skills: {available}"

        logger.info(
            "Skill loaded by agent: name=%r user=%s", name, ctx.deps.user_id
        )
        return content

    @agent.tool
    async def list_skills(ctx: RunContext[AgentDeps]) -> str:
        """List all available skills with their descriptions.

        Use this when you are unsure whether a skill exists for a domain, or to
        discover what specialised data sources and workflows are available.
        """
        from app.agent.skills import get_skill_registry

        registry = get_skill_registry()
        skills = registry.list()
        if not skills:
            return "No skills are currently loaded."

        lines = [f"{len(skills)} skill(s) available:\n"]
        for s in skills:
            lines.append(f"- **{s.name}** ({s.display_name}): {s.description}")
        return "\n".join(lines)
