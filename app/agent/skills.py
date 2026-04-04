from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIM = "---"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from markdown body. Returns (meta, body)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}, text
    end = next(
        (i for i, ln in enumerate(lines[1:], 1) if ln.strip() == _FRONTMATTER_DELIM),
        None,
    )
    if end is None:
        return {}, text
    meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


@dataclass
class SkillMeta:
    name: str
    description: str
    display_name: str
    short_description: str
    default_prompt: str
    path: Path
    has_scripts: bool = False
    has_references: bool = False

    def index_entry(self) -> str:
        return (
            f"- **{self.name}** — {self.short_description or self.description}\n"
            f"  Invoke: \"{self.default_prompt}\""
        )


@dataclass
class SkillRegistry:
    _skills: dict[str, SkillMeta] = field(default_factory=dict)

    def load(self, skills_dir: Path) -> None:
        self._skills.clear()
        if not skills_dir.is_dir():
            logger.warning("Skills directory not found: %s", skills_dir)
            return
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            agent_yaml = skill_dir / "agents" / "agent.yaml"
            if not skill_md.exists():
                continue
            try:
                meta, _ = _parse_frontmatter(skill_md.read_text())
                name = meta.get("name") or skill_dir.name
                description = meta.get("description", "")

                display_name = name
                short_description = ""
                default_prompt = f"Use ${name}"
                if agent_yaml.exists():
                    ay = yaml.safe_load(agent_yaml.read_text()) or {}
                    iface = ay.get("interface", {})
                    display_name = iface.get("display_name", name)
                    short_description = iface.get("short_description", "")
                    default_prompt = iface.get("default_prompt", default_prompt)

                self._skills[name] = SkillMeta(
                    name=name,
                    description=description,
                    display_name=display_name,
                    short_description=short_description,
                    default_prompt=default_prompt,
                    path=skill_dir,
                    has_scripts=(skill_dir / "scripts").is_dir()
                    and any((skill_dir / "scripts").iterdir()),
                    has_references=(skill_dir / "references").is_dir()
                    and any((skill_dir / "references").iterdir()),
                )
                logger.debug("Loaded skill: %s", name)
            except Exception:
                logger.exception("Failed to load skill from %s", skill_dir)

        logger.info("SkillRegistry loaded %d skill(s)", len(self._skills))

    def list(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def get_content(self, name: str) -> str | None:
        skill = self._skills.get(name)
        if not skill:
            return None
        skill_md = skill.path / "SKILL.md"
        return skill_md.read_text() if skill_md.exists() else None

    def skills_index_text(self) -> str:
        if not self._skills:
            return ""
        entries = "\n".join(s.index_entry() for s in self._skills.values())
        return f"## Available Skills\n\n{entries}"


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _load_registry(_registry)
    return _registry


def _load_registry(reg: SkillRegistry) -> None:
    from app.config import get_settings
    settings = get_settings()
    skills_dir = Path(settings.skills_dir)
    if not skills_dir.is_absolute():
        # Resolve relative to repo root (one level above the app/ package)
        skills_dir = Path(__file__).parent.parent.parent / skills_dir
    reg.load(skills_dir)


def reload_skill_registry() -> None:
    """Reload skills from disk — called on admin /reload."""
    global _registry
    _registry = None
    logger.info("SkillRegistry cleared — will reload on next access")
