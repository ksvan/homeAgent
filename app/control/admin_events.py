from __future__ import annotations

from typing import Any

from app.control.events import emit as _emit


def emit_admin_event(event_type: str, payload: dict[str, Any]) -> None:
    _emit(event_type, payload)
