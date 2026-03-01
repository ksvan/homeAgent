from __future__ import annotations

import time
from collections.abc import Callable
from importlib.metadata import version

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import cache_engine, memory_engine, users_engine

router = APIRouter()

_start_time = time.time()


def _db_ok(engine_fn: Callable[[], Engine]) -> str:
    try:
        with engine_fn().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"


@router.get("/health")
async def health() -> dict[str, object]:
    from app.homey.mcp_client import get_mcp_server
    from app.scheduler.engine import get_scheduler

    db_users = _db_ok(users_engine)
    db_memory = _db_ok(memory_engine)
    db_cache = _db_ok(cache_engine)
    mcp_status = "ok" if get_mcp_server() is not None else "disconnected"
    scheduler_status = "ok" if get_scheduler() is not None else "stopped"

    all_ok = all(s == "ok" for s in (db_users, db_memory, db_cache))
    status = "healthy" if all_ok else "degraded"

    try:
        pkg_version = version("homeagent")
    except Exception:
        pkg_version = "unknown"

    return {
        "status": status,
        "version": pkg_version,
        "uptime_seconds": int(time.time() - _start_time),
        "components": {
            "db_users": db_users,
            "db_memory": db_memory,
            "db_cache": db_cache,
            "mcp": mcp_status,
            "scheduler": scheduler_status,
        },
    }
