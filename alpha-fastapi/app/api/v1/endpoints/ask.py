from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import AskRequest, AskResponse
from app.services.groq_service import GroqService
from app.services.file_service import FileService
from app.dependencies import get_groq_service, get_file_service

router = APIRouter()


@router.post("/ask", response_model=AskResponse, summary="Ask the AI bot a question")
async def ask(
    body: AskRequest | None = None,
    groq_service: GroqService = Depends(get_groq_service),
    file_service: FileService = Depends(get_file_service),
) -> AskResponse:
    if body is None:
        raise HTTPException(status_code=400, detail="question is required")

    question = (body.question or body.q or body.prompt or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    user = body.user or "api-user"
    answer = await groq_service.ask_bot(question)
    file_service.save_conversation("api", user, question, answer)
    return AskResponse(answer=answer)
