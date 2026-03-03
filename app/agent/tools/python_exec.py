from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai import Agent, RunContext

from app.agent.agent import AgentDeps

logger = logging.getLogger(__name__)

_TMP_DIR = ".agent_tmp"
_CLEANUP_AGE_HOURS = 24


def _cleanup_old_runs(workspace: Path) -> None:
    """Delete temp run dirs older than _CLEANUP_AGE_HOURS."""
    tmp_base = workspace / _TMP_DIR
    if not tmp_base.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - _CLEANUP_AGE_HOURS * 3600
    for d in tmp_base.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def register_python_tools(agent: Agent[AgentDeps, str]) -> None:
    """Attach the Python script runner tool to the conversation agent."""

    @agent.tool
    async def run_python_script(
        ctx: RunContext[AgentDeps],
        code: str,
        files: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> str:
        """Write and execute a Python script in an isolated workspace directory.

        Use this when you need to:
        - Perform calculations or data transformations
        - Process files from the workspace
        - Generate output files (charts, reports, processed data)
        - Run multi-step logic that is cleaner as a script than a shell command

        The script runs with the same access as the agent process — it can read
        and write files inside the workspace but should not access the network
        or paths outside the workspace.

        IMPORTANT — confirm before running scripts that write files or produce
        artifacts, just as you would for any write operation.

        Args:
            code:    The Python script to run (written as main.py).
            files:   Optional helper files to place in the same run directory,
                     as {filename: content}. E.g. {"utils.py": "def foo(): ..."}.
                     These can be imported by the main script.
            timeout_s: Seconds before the process is killed. Max 300, default 30.

        Returns a string containing stdout, stderr (if any), a list of output
        files created during the run, and the exit code if non-zero.
        """
        from app.config import get_settings
        from app.shell import run_command

        settings = get_settings()
        timeout_s = min(timeout_s, settings.python_max_timeout_seconds)

        workspace = Path(settings.bash_workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        # Lazy cleanup of old runs
        _cleanup_old_runs(workspace)

        run_id = uuid.uuid4().hex[:12]
        run_dir = workspace / _TMP_DIR / run_id
        run_dir.mkdir(parents=True)

        # Write main script and any helper files
        (run_dir / "main.py").write_text(code, encoding="utf-8")
        input_names = {"main.py"}
        for name, content in (files or {}).items():
            safe_name = Path(name).name  # strip any path components
            (run_dir / safe_name).write_text(content, encoding="utf-8")
            input_names.add(safe_name)

        # Run via the shared runner — uses python3 with workspace confinement
        result = await run_command(
            argv=["python3", "main.py"],
            cwd=str((run_dir).relative_to(workspace)),
            timeout_s=timeout_s,
            workspace_dir=workspace,
            allowed_commands=frozenset({"python3"}),
            max_output_bytes=settings.python_max_output_bytes,
        )

        # Collect output artifacts (files created by the script)
        artifacts: list[str] = []
        for f in sorted(run_dir.rglob("*")):
            if f.is_file() and f.name not in input_names:
                rel = f.relative_to(run_dir)
                size = f.stat().st_size
                artifacts.append(f"  {rel} ({size} bytes)")

        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if result.truncated:
            parts.append("[Output truncated to size limit]")
        if not result.ok:
            parts.append(f"[exit {result.exit_code}]")
        if artifacts:
            parts.append("Output files:\n" + "\n".join(artifacts))

        return "\n".join(parts) if parts else "(no output)"
