from uuid import uuid4
import time
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from opentelemetry import trace as otel_trace
from opentelemetry import trace

from .session import SessionStore
from .health import readiness, liveness
from utils.logging import log_request
from .retriever import build_retriever_from_env
from .prompt import build_prompt
from .llm_client import build_kserve_client_from_env
from .schemas import ChatRequest, ChatResponse
from .metrics import (
    RAG_CHAT_REQUESTS_TOTAL,
    RAG_CHAT_ERRORS_TOTAL,
    RAG_RETRIEVAL_LATENCY_SECONDS,
    RAG_CONTEXT_TOKENS,
    RAG_EMPTY_CONTEXT_TOTAL,
    RAG_GENERATION_LATENCY_SECONDS,
    RAG_FALLBACK_TOTAL,
    RAG_INFLIGHT,
)

from utils.tracing import setup_tracing

from .guardrails_app import (
    GUARDRAILS_ENABLED,
    generate_with_guardrails,
)

# ---------------------------------------------------------------------
# App bootstrap and tracing
# ---------------------------------------------------------------------

app = FastAPI(title="Medical RAG Orchestrator")

# Initialize OpenTelemetry tracing (FastAPI + outbound clients)
setup_tracing(
    app=app,
    service_name=os.getenv("OTEL_SERVICE_NAME", "rag-orchestrator"),
)

tracer = trace.get_tracer("rag-orchestrator")

# ---------------------------------------------------------------------
# Session store (Redis-backed if configured)
# ---------------------------------------------------------------------

session_store = SessionStore()


# ---------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    RAG_CHAT_ERRORS_TOTAL.inc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


# ---------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return readiness()


@app.get("/live")
def live():
    return liveness()

# ---------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------
# API logging (only /api)
# ---------------------------------------------------------------------
@app.middleware("http")
async def api_logging_middleware(request: Request, call_next):
    if request.url.path.startswith("/api"):
        start = time.time()
        request_id = str(uuid4())
        request.state.request_id = request_id

        # Single call_next, wrapped in the span
        with tracer.start_as_current_span(
            f"http {request.method} {request.url.path}"
        ) as span:
            ctx = span.get_span_context()
            request.state.trace_id = format(ctx.trace_id, "032x")
            request.state.span_id = format(ctx.span_id, "016x")
            response = await call_next(request)
            status_code = getattr(response, "status_code", 500)

        try:
            # no second call_next here
            pass
        except Exception as exc:
            request.state.error_message = str(exc)
            status_code = 500
            raise
        finally:
            await log_request(request, status_code, start)

        return response

    return await call_next(request)


# ---------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    RAG_CHAT_REQUESTS_TOTAL.inc()
    RAG_INFLIGHT.inc()
    try:
        # Root span for this chat request
        with tracer.start_as_current_span("rag.chat") as root_span:
            session_id = req.session_id or str(uuid4())
            request.state.session_id = session_id
            root_span.set_attribute("session.id", session_id)

            # append user message + trace
            with tracer.start_as_current_span("session.append_user"):
                session_store.append(session_id, "user", req.message)

            # load chat history + trace
            with tracer.start_as_current_span("session.load_history") as span:
                history = session_store.get_history(session_id)
                span.set_attribute("session.history_length", len(history))

            # retrieve context
            with tracer.start_as_current_span("retriever.build"):
                retriever = build_retriever_from_env()

            with tracer.start_as_current_span("retrieval.vector_search") as span:
                span.set_attribute("vector.db", "qdrant")
                span.set_attribute(
                    "vector.collection",
                    os.getenv("QDRANT_COLLECTION", "medical_docs"),
                )
                span.set_attribute(
                    "vector.top_k",
                    int(os.getenv("RAG_TOP_K", "4")),
                )

                t0 = time.time()
                chunks = retriever.retrieve(req.message) if retriever else []
                retrieval_ms = round((time.time() - t0) * 1000.0, 2)

                span.set_attribute("retrieval.chunks", len(chunks))

            RAG_RETRIEVAL_LATENCY_SECONDS.observe(retrieval_ms / 1000.0)
            request.state.retrieval_ms = retrieval_ms
            request.state.chunks_returned = len(chunks)

            # estimate context tokens (simple heuristic; consistent with retriever)
            est_tokens = sum(max(1, len(c.text) // 4) for c in chunks)
            RAG_CONTEXT_TOKENS.observe(est_tokens)

            if not chunks:
                RAG_EMPTY_CONTEXT_TOTAL.inc()

            # build grounded prompt + trace
            with tracer.start_as_current_span("prompt.build") as span:
                span.set_attribute("prompt.history_turns", len(history))
                span.set_attribute("prompt.context_chunks", len(chunks))
                prompt = build_prompt(
                    req.message,
                    chunks,
                    chat_history=history,
                )
                
            with tracer.start_as_current_span("llm.inference") as span:
                span.set_attribute(
                    "llm.model",
                    os.getenv("LLM_MODEL_ID", "unknown"),
                )
                g0 = time.time()

                # Check prompt before send
                if GUARDRAILS_ENABLED:
                    with tracer.start_as_current_span("guardrails.evaluate") as span:
                        span.set_attribute("llm.provider", "nemo_guardrails")
                        answer = generate_with_guardrails(
                        user_message=req.message,
                        grounded_prompt=prompt,
                    )
                else:
                    span.set_attribute("llm.provider", "kserve")
                    kserve = build_kserve_client_from_env()

                    if kserve:
                        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "512"))
                        temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
                        answer = kserve.generate(
                            prompt,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        )
                    else:
                        # existing fallback path
                        RAG_FALLBACK_TOTAL.inc()
                        if chunks:
                            answer = (
                                "General information based on available context:\n\n"
                                + "\n\n".join(
                                    f"- {c.text} [source:{c.id}]"
                                    for c in chunks[:3]
                                )
                                + "\n\n(Configure KSERVE_URL for full generation.)"
                            )
                        else:
                            answer = (
                                "I don't have enough context. "
                                "Ingest documents into Qdrant first."
                            )
                llm_ms = round((time.time() - g0) * 1000.0, 2)

            # generate answer
            kserve = build_kserve_client_from_env()

            RAG_GENERATION_LATENCY_SECONDS.observe(llm_ms / 1000.0)
            request.state.llm_ms = llm_ms

            # append assistant response
            session_store.append(session_id, "assistant", answer)

            # reload full history
            history = session_store.get_history(session_id)

            return ChatResponse(
                session_id=session_id,
                answer=answer,
                history=history,
                context_used=len(chunks),
            )
    finally:
        RAG_INFLIGHT.dec()
