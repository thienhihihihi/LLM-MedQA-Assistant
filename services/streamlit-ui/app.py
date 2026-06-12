import streamlit as st
import os
import uuid
import requests
import logging
import json
import time
from utils.tracing import setup_tracing
from opentelemetry import trace

# -----------------------
# OpenTelemetry tracing (Streamlit root)
# -----------------------
setup_tracing()
tracer = trace.get_tracer("streamlit-ui")

# -----------------------
# Configuration
# -----------------------
RAG_API_URL = os.getenv(
    "RAG_API_URL",
    "http://rag-orchestrator:8000"
)
REQUEST_TIMEOUT_S = int(os.getenv("RAG_API_TIMEOUT_S", "120"))

# -----------------------
# Structured stdout logger (for Filebeat)
# -----------------------
logger = logging.getLogger("streamlit_ui")
logger.setLevel(logging.INFO)

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)

# -----------------------
# Streamlit page setup
# -----------------------
st.set_page_config(page_title="Medical QA (Model-Serving)")
st.title(" Medical QA (Model-Serving)")

# -----------------------
# Session initialization
# -----------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

st.caption(f"session_id: `{st.session_state.session_id}`")

# -----------------------
# Render chat history
# -----------------------
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# -----------------------
# Chat input
# -----------------------
prompt = st.chat_input("Ask a medical question...")

if prompt:
    # ---- add trace ----
    with tracer.start_as_current_span("ui.submit_question") as span:
        span.set_attribute("ui.framework", "streamlit")
        span.set_attribute("session_id", st.session_state.session_id)
        span.set_attribute("prompt.length", len(prompt))

        # ---- render user message ----
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # ---- call RAG API ----
        start = time.time()
        status_code = None
        error_msg = None
        answer = ""
        context_used = 0

        try:
            resp = requests.post(
                f"{RAG_API_URL}/api/chat",
                json={
                    "session_id": st.session_state.session_id,
                    "message": prompt,
                },
                timeout=REQUEST_TIMEOUT_S,
            )

            status_code = resp.status_code
            resp.raise_for_status()

            data = resp.json()
            answer = data.get("answer", "")
            context_used = int(data.get("context_used", 0))

        except Exception as e:
            error_msg = str(e)
            answer = f" Error calling RAG API: {e}"
            context_used = 0

        duration_ms = round((time.time() - start) * 1000.0, 2)

        span.set_attribute("http.status_code", status_code or 0)
        span.set_attribute("rag.duration_ms", duration_ms)
        span.set_attribute("rag.context_used", context_used)
        if error_msg:
            span.record_exception(Exception(error_msg))
            
        # -----------------------
        # Structured UI log (ELK)
        # -----------------------
        ui_log = {
            "service": "streamlit-ui",
            "event": "chat_request",
            "session_id": st.session_state.session_id,
            "status": status_code,
            "duration_ms": duration_ms,
            "context_used": context_used,
            "error": error_msg,
        }

        logger.info(json.dumps(ui_log, ensure_ascii=False))

        # ---- render assistant reply ----
        st.session_state.messages.append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            if context_used > 0:
                st.caption(f"Context chunks used: {context_used}")
            st.markdown(answer)
