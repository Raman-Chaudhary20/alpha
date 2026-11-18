"""Alpha's structured memory: the register of projects and people, and what it holds."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import caller_scope, get_memory, get_store, require_admin
from app.services.memory_service import MemoryBuilder
from app.services.store import Store

router = APIRouter(prefix="/memory")


@router.get("/projects", summary="The projects Alpha has worked out, with their people")
async def projects(scope: str = Depends(caller_scope), store: Store = Depends(get_store)) -> dict:
    items = store.knowledge(scope, "project")
    return {"scope": scope, "count": len(items), "projects": items}


@router.get("/people", summary="What each person works on")
async def people(scope: str = Depends(caller_scope), store: Store = Depends(get_store)) -> dict:
    items = store.knowledge(scope, "person")
    return {"scope": scope, "count": len(items), "people": items}


@router.get("/status", summary="How much Alpha holds", dependencies=[Depends(require_admin)])
async def status(store: Store = Depends(get_store)) -> dict:
    conversations = store.conversations()
    kinds: dict[str, int] = {}
    for conversation in conversations:
        kinds[conversation["kind"]] = kinds.get(conversation["kind"], 0) + 1
    return {
        "messages": store.message_count(),
        "conversations": len(conversations),
        "conversations_by_kind": kinds,
        "people": len(store.users()),
        "connected_people": len(store.tokens()),
        "day_summaries_waiting": len(store.days_to_digest(100000)),
        "projects": len(store.knowledge("all", "project")),
        "last_read_from_slack": store.get_meta("last_harvest_at"),
    }


@router.post("/build", summary="Summarize new chats and update the register now", dependencies=[Depends(require_admin)])
async def build(memory: MemoryBuilder = Depends(get_memory)) -> dict:
    return await memory.build()
