from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str | None = Field(default=None, description="The question to ask the AI bot")
    q: str | None = Field(default=None, description="Alias for question")
    prompt: str | None = Field(default=None, description="Alias for question")
    user: str = Field(default="api-user", description="Identifier for the requesting user")


class AskResponse(BaseModel):
    answer: str = Field(..., description="The AI-generated answer")


class MessageRequest(BaseModel):
    user: str = Field(default="api-user", description="Identifier for the message author")
    text: str | None = Field(default=None, description="Message content")
    message: str | None = Field(default=None, description="Alias for text")
    channel: str = Field(default="api", description="Channel the message belongs to")


class MessageResponse(BaseModel):
    saved: bool = Field(default=True, description="Whether the message was saved successfully")


class SyncResponse(BaseModel):
    synced: bool = Field(default=True, description="Whether the sync completed successfully")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Short error description")
    detail: str | None = Field(default=None, description="Extended error detail")


class HistoryResponse(BaseModel):
    content: str = Field(..., description="Raw text content of the requested history file")
