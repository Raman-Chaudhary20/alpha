from fastapi import APIRouter

from app.api.v1.endpoints import ask, connect, history, memory, messages, sync

router = APIRouter(prefix="/api/v1")
router.include_router(ask.router, tags=["AI"])
router.include_router(messages.router, tags=["Messages"])
router.include_router(sync.router, tags=["Sync"])
router.include_router(history.router, tags=["History"])
router.include_router(connect.router, tags=["Slack Sign-In"])
router.include_router(memory.router, tags=["Memory"])

# Root router for backwards compatibility with Node.js endpoints (/ask, /sync, /history, etc.)
root_router = APIRouter()
root_router.include_router(ask.router, tags=["AI"], include_in_schema=False)
root_router.include_router(messages.router, tags=["Messages"], include_in_schema=False)
root_router.include_router(sync.router, tags=["Sync"], include_in_schema=False)
root_router.include_router(history.router, tags=["History"], include_in_schema=False)
