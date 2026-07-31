from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class _SafeStr(str):
    """A str that reconstructs {key:spec} when an unknown format spec is applied."""

    def __format__(self, spec: str) -> str:
        # Reconstruct the original {key} or {key:spec} so JSON examples in prompt
        # files are preserved verbatim even when format_map processes the template.
        if spec:
            return "{" + str(self) + ":" + spec + "}"
        return "{" + str(self) + "}"


class _SafeDict(dict):  # type: ignore[type-arg]
    """Leave unrecognised {keys} unchanged instead of raising KeyError or ValueError."""

    def __missing__(self, key: str) -> _SafeStr:
        return _SafeStr(key)


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def _render(template: str, variables: dict[str, str]) -> str:
    cleaned = _strip_html_comments(template)
    return cleaned.format_map(_SafeDict(variables))


@lru_cache(maxsize=16)
def _read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        logger.warning("Prompt file not found: %s (cwd=%s)", path, Path.cwd())
        return ""
    content = p.read_text(encoding="utf-8")
    logger.debug("Loaded prompt file: %s (%d chars)", path, len(content))
    return content


def clear_prompt_cache() -> None:
    """Bust the file cache — call this on admin /reload."""
    _read_file.cache_clear()
    load_static_prompt_body.cache_clear()


def load_persona(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "persona.md")
    return _render(_read_file(path), variables)


def load_instructions(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "instructions.md")
    return _render(_read_file(path), variables)


def load_home_context(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "home_context.md")
    return _render(_read_file(path), variables)


def render_identity_block(agent_name: str, household_name: str, user_name: str) -> str:
    """The one part of "persona" that legitimately varies per call.

    Kept separate from load_static_prompt_body() so persona.md's tone/style
    content has no per-call substitutions and can form a stable, cacheable
    prefix (see docs/prompt-caching-design.md).
    """
    path = str(get_settings().prompts_path() / "identity.md")
    variables = {
        "agent_name": agent_name,
        "household_name": household_name,
        "user_name": user_name,
    }
    return _render(_read_file(path), variables)


@lru_cache(maxsize=1)
def load_static_prompt_body() -> str:
    """Persona + instructions with no per-call template variables.

    Passed to `Agent(instructions=...)` — pydantic-ai keeps this out of
    persisted message history and it's the block Anthropic's
    `anthropic_cache_instructions` setting places its cache breakpoint
    after. Cleared by clear_prompt_cache() on admin /reload.
    """
    persona = load_persona({})
    instructions = load_instructions({})
    parts = [p for p in (persona, instructions) if p]
    return "\n\n---\n\n".join(parts)
