from typing import List, Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    history: List[ChatMessage]
    context_used: int
