from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Iterable

from qdrant_client import QdrantClient
from fastembed import TextEmbedding


def _estimate_tokens(text: str) -> int:
    """Rough token estimate without external tokenizers.
    Empirically, ~4 characters per token for English-like text.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _stable_text_hash(text: str) -> str:
    norm = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class RetrievedChunk:
    id: str
    text: str
    score: float
    metadata: Dict[str, Any]


class QdrantRetriever:
    def __init__(
        self,
        qdrant_url: str,
        collection: str,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        top_k: int = 4,
        score_threshold: float = 0.25,
        max_context_tokens: int = 2048,
        deduplicate: bool = True,
    ):
        self.client = QdrantClient(url=qdrant_url)
        self.collection = collection
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.max_context_tokens = max_context_tokens
        self.deduplicate = deduplicate
        self.embedder = TextEmbedding(model_name=embedding_model)

    def retrieve(self, query: str) -> List[RetrievedChunk]:
        try:
            # Embed query
            qvec = next(self.embedder.embed([query])).tolist()

            res = self.client.search(
                collection_name=self.collection,
                query_vector=qvec,
                limit=self.top_k,
                with_payload=True,
            )

        except Exception as e:
            # Graceful fallback: no retrieval, no crash
            print(f"[RAG] Retrieval skipped: {e}")
            return []

        chunks: List[RetrievedChunk] = []
        seen: set[str] = set()
        used_tokens = 0

        for p in res:
            if p.score is None or p.score < self.score_threshold:
                continue

            payload = p.payload or {}
            text = str(payload.get("text", "") or "")
            if not text.strip():
                continue

            if self.deduplicate:
                h = _stable_text_hash(text)
                if h in seen:
                    continue
                seen.add(h)

            tks = _estimate_tokens(text)
            if self.max_context_tokens and used_tokens + tks > self.max_context_tokens:
                break

            used_tokens += tks
            md = payload.get("metadata", {})
            chunks.append(
                RetrievedChunk(
                    id=str(p.id),
                    text=text,
                    score=float(p.score),
                    metadata=md if isinstance(md, dict) else {},
                )
            )

        return chunks



def build_retriever_from_env() -> Optional[QdrantRetriever]:
    qdrant_url = os.getenv("QDRANT_URL", "").strip()
    if not qdrant_url:
        return None

    # Retrieval controls (strict wiring via env; chart sets these explicitly)
    top_k = int(os.getenv("RAG_TOP_K", os.getenv("TOP_K", "4")))
    score_threshold = float(os.getenv("RAG_MIN_SCORE", os.getenv("SCORE_THRESHOLD", "0.25")))
    max_context_tokens = int(os.getenv("RAG_MAX_CONTEXT_TOKENS", "2048"))
    dedup = os.getenv("RAG_DEDUPLICATE", "true").strip().lower() in ("1", "true", "yes", "y", "on")

    return QdrantRetriever(
        qdrant_url=qdrant_url,
        collection=os.getenv("QDRANT_COLLECTION", "medical_docs"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        top_k=top_k,
        score_threshold=score_threshold,
        max_context_tokens=max_context_tokens,
        deduplicate=dedup,
    )
