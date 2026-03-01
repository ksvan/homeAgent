from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class _SafeDict(dict):  # type: ignore[type-arg]
    """Leave unrecognised {keys} unchanged instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def _render(template: str, variables: dict[str, str]) -> str:
    cleaned = _strip_html_comments(template)
    return cleaned.format_map(_SafeDict(variables))


@lru_cache(maxsize=16)
def _read_file(path: str) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


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
