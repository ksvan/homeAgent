from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Security constants
# --------------------------------------------------------------------------

# Hardcoded — cannot be overridden via .env allowlist.
# Shells would bypass shell=False protection; network/privilege tools are
# out of scope for a household workspace runner.
ALWAYS_BLOCKED: frozenset[str] = frozenset(
    {
        # Shells (argv ["bash", "-c", "rm -rf /"] still works without shell=True)
        "bash",
        "sh",
        "zsh",
        "fish",
        "dash",
        "csh",
        "tcsh",
        "ksh",
        # Privilege escalation
        "sudo",
        "su",
        "doas",
        # Network tools
        "curl",
        "wget",
        "nc",
        "netcat",
        "ncat",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        # Destructive disk operations
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "shred",
        # File removal and permission changes (not in default scope)
        "rm",
        "rmdir",
        "chmod",
        "chown",
        "chgrp",
        # Process control
        "kill",
        "pkill",
        "killall",
    }
)

# Used when BASH_ALLOWED_COMMANDS is not set in .env.
DEFAULT_ALLOWED: frozenset[str] = frozenset(
    {
        # Read / inspect
        "ls",
        "cat",
        "grep",
        "rg",
        "find",
        "stat",
        "head",
        "tail",
        "wc",
        "echo",
        "pwd",
        "sort",
        "uniq",
        "cut",
        "awk",
        "sed",
        "diff",
        "du",
        "df",
        "date",
        "tr",
        "xargs",
        "jq",
        # Write inside workspace
        "touch",
        "mkdir",
        "cp",
        "mv",
        "tee",
        # Development
        "git",
    }
)

_MAX_OUTPUT_BYTES = 200_000
_MAX_TIMEOUT_SECONDS = 300


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = field(default=False)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


async def run_command(
    argv: list[str],
    cwd: str,
    timeout_s: int,
    workspace_dir: Path,
    allowed_commands: frozenset[str],
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    """
    Execute argv as a subprocess confined to workspace_dir.

    Security guarantees:
    - shell=False — no shell expansion, pipes, or redirects in argv
    - Binary name checked against ALWAYS_BLOCKED then allowed_commands
    - cwd resolved inside workspace_dir (symlink + traversal safe)
    - Minimal clean environment — no host secrets passed to subprocess
    - Timeout kills the entire process group (start_new_session=True)
    - stdout/stderr each truncated to max_output_bytes
    """
    if not argv:
        return RunResult(ok=False, exit_code=1, stdout="", stderr="Empty command.")

    binary = Path(argv[0]).name

    if binary in ALWAYS_BLOCKED:
        logger.warning("Shell runner: blocked command attempt: %s", binary)
        return RunResult(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"Command '{binary}' is not permitted.",
        )

    if binary not in allowed_commands:
        logger.warning("Shell runner: command not in allowlist: %s", binary)
        return RunResult(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"Command '{binary}' is not in the allowlist. Allowed: "
            + ", ".join(sorted(allowed_commands)),
        )

    # Resolve cwd — must stay inside workspace (handles '..' and symlinks)
    workspace = workspace_dir.resolve()
    try:
        if not cwd or cwd in (".", "/"):
            resolved_cwd = workspace
        else:
            resolved_cwd = (workspace / cwd).resolve()
        # Raises ValueError if resolved_cwd is not relative to workspace
        resolved_cwd.relative_to(workspace)
    except ValueError:
        return RunResult(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"cwd '{cwd}' escapes the workspace directory.",
        )

    resolved_cwd.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "HOME": str(workspace),
        "LANG": "en_US.UTF-8",
        "TERM": "dumb",
    }
    if extra_env:
        env.update(extra_env)

    logger.info("Shell runner: %s  cwd=%s  timeout=%ss", argv, resolved_cwd, timeout_s)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(resolved_cwd),
            env=env,
            start_new_session=True,  # isolate process group for clean kill
        )
    except FileNotFoundError:
        return RunResult(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"Command '{argv[0]}' not found on PATH.",
        )
    except Exception as exc:
        return RunResult(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"Failed to start process: {exc}",
        )

    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(
            proc.communicate(), timeout=float(timeout_s)
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        logger.warning("Shell runner: command timed out after %ss: %s", timeout_s, argv)
        return RunResult(
            ok=False,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout_s}s.",
        )

    truncated = len(raw_stdout) > max_output_bytes or len(raw_stderr) > max_output_bytes
    exit_code = proc.returncode if proc.returncode is not None else 0

    logger.info(
        "Shell runner: exit=%d stdout=%dB stderr=%dB argv=%s",
        exit_code,
        len(raw_stdout),
        len(raw_stderr),
        argv,
    )

    return RunResult(
        ok=exit_code == 0,
        exit_code=exit_code,
        stdout=raw_stdout[:max_output_bytes].decode("utf-8", errors="replace"),
        stderr=raw_stderr[:max_output_bytes].decode("utf-8", errors="replace"),
        truncated=truncated,
    )
