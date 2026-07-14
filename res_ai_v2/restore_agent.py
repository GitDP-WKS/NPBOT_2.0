from __future__ import annotations

from typing import Any

from .agent import AgentEvent, register_handler
from .knowledge_agent import rebuild_knowledge
from .restore_writer import apply_restore_request


def process_restore_event(event: AgentEvent) -> dict[str, Any]:
    request_id = int(event.payload.get("request_id") or event.subject_key)
    restored = apply_restore_request(request_id)
    analysis = rebuild_knowledge(
        full_rebuild=True,
        trigger_type="restore_completed",
        trigger_key=str(request_id),
    )
    return {
        "event": event.event_type,
        "request_id": request_id,
        "restored": restored,
        "scope": "full_pit",
        "analysis": analysis,
    }


register_handler("restore_requested", process_restore_event)
