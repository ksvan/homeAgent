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


def load_persona(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "persona.md")
    return _render(_read_file(path), variables)


def load_instructions(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "instructions.md")
    return _render(_read_file(path), variables)


def load_home_context(variables: dict[str, str]) -> str:
    path = str(get_settings().prompts_path() / "home_context.md")
    return _render(_read_file(path), variables)
