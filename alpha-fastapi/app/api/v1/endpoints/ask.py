from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import caller_scope, get_brain, get_file_service
from app.models.schemas import AskRequest, AskResponse
from app.services.bedrock_service import Brain
from app.services.file_service import FileService

router = APIRouter()


@router.post("/ask", response_model=AskResponse, summary="Ask Alpha a question")
async def ask(
    body: AskRequest | None = None,
    scope: str = Depends(caller_scope),
    brain: Brain = Depends(get_brain),
    file_service: FileService = Depends(get_file_service),
) -> AskResponse:
    """Answered from every conversation with the admin key in X-API-Key, else from public channels."""
    if body is None:
        raise HTTPException(status_code=400, detail="question is required")

    question = (body.question or body.q or body.prompt or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    user = body.user or "api-user"
    answer = await brain.ask_bot(question, scope=scope, asker=user)
    file_service.save_conversation("api", user, question, answer)
    return AskResponse(answer=answer)
