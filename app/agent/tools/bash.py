from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)


def register_bash_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach the shell runner tool to the conversation agent."""

    @agent.tool
    async def run_bash_command(
        ctx: RunContext[AgentDeps],
        argv: list[str],
        cwd: str = ".",
        timeout_s: int = 30,
    ) -> str:
        """Run an allowlisted command in the household workspace directory.

        Use this to read files, search content, run scripts, inspect state,
        or perform simple file operations inside the workspace.

        Only plain argument lists are accepted — no shell metacharacters, pipes,
        redirects, or variable expansion (shell=False). For file writes use tee.
        For running Python scripts use run_python_script instead.

        IMPORTANT — confirm before write operations:
        - READ-ONLY (ls, cat, grep, find, git status, head, etc.) → run immediately.
        - WRITE / MODIFY (cp, mv, touch, mkdir, python3 writing a file, git commit,
          git checkout, etc.) → ask for explicit user confirmation first.

        Args:
            argv: Command as a list of strings, e.g. ["grep", "-r", "error", "logs/"].
                  Never include shell operators (|, >, <, &&, ;) — they won't work
                  and may cause unexpected errors.
            cwd:  Working directory relative to the workspace root. Default is the
                  workspace root itself. Must not escape the workspace (no '..' traversal).
            timeout_s: Seconds before the process is killed. Max 300, default 30.
        """
        from app.config import get_settings
        from app.shell import ALWAYS_BLOCKED, DEFAULT_ALLOWED, run_command

        settings = get_settings()
        timeout_s = min(timeout_s, settings.bash_max_timeout_seconds)

        if settings.bash_allowed_commands:
            allowed = frozenset(settings.bash_allowed_commands) - ALWAYS_BLOCKED
        else:
            allowed = DEFAULT_ALLOWED

        workspace = Path(settings.bash_workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        result = await run_command(
            argv=argv,
            cwd=cwd,
            timeout_s=timeout_s,
            workspace_dir=workspace,
            allowed_commands=allowed,
            max_output_bytes=settings.bash_max_output_bytes,
        )

        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if result.truncated:
            parts.append("[Output truncated to size limit]")
        if not result.ok:
            parts.append(f"[exit {result.exit_code}]")

        return "\n".join(parts) if parts else "(no output)"
