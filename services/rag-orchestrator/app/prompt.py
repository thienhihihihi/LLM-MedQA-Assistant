from __future__ import annotations
from typing import List
from .retriever import RetrievedChunk

SYSTEM_RULES = """You are a medical question-answering assistant.
Rules:
1) Use ONLY the provided CONTEXT.
2) If context is insufficient, say you don't have enough information.
3) Do NOT provide diagnosis or prescriptions; advise consulting a clinician.
4) Add citations like [source:<id>].
"""

def build_prompt(question: str, chunks: List[RetrievedChunk], chat_history: list | None = None) -> str:
    history = ""
    if chat_history:
        last = chat_history[-6:]
        history = "\n".join([f"{m.get('role','').upper()}: {m.get('content','')}" for m in last])

    context = "NO_CONTEXT" if not chunks else "\n\n".join([f"[source:{c.id}] {c.text}" for c in chunks])

    return f"""{SYSTEM_RULES}

CHAT_HISTORY:
{history if history else "NONE"}

QUESTION:
{question}

CONTEXT:
{context}

Answer concisely. Include citations like [source:abc]."""
