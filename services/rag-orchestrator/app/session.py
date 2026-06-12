import json
import os
import redis
from typing import List, Dict

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SESSION_TTL_S = int(os.getenv("SESSION_TTL_S", "0"))

r = redis.from_url(REDIS_URL, decode_responses=True)

def _key(session_id: str) -> str:
    return f"session:{session_id}"

def get_session(session_id: str) -> Dict:
    raw = r.get(_key(session_id))
    return json.loads(raw) if raw else {"messages": []}

def save_session(session_id: str, data: Dict):
    payload = json.dumps(data)
    if SESSION_TTL_S > 0:
        r.setex(_key(session_id), SESSION_TTL_S, payload)
    else:
        r.set(_key(session_id), payload)

def append_message(session_id: str, role: str, content: str):
    s = get_session(session_id)
    s["messages"].append({"role": role, "content": content})
    save_session(session_id, s)

def get_messages(session_id: str) -> List[Dict]:
    return get_session(session_id)["messages"]

class SessionStore:
    def __init__(self):
        self.redis_enabled = False
        self._client = None
        self._memory_store: Dict[str, List[Dict]] = {}

        host = os.getenv("REDIS_HOST")
        if host and redis:
            self._client = redis.Redis(
                host=host,
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                decode_responses=True,
            )
            self.redis_enabled = True

        self.ttl = int(os.getenv("REDIS_TTL_SECONDS", "86400"))

    def get_history(self, session_id: str) -> List[Dict]:
        if self.redis_enabled:
            data = self._client.get(session_id)
            return json.loads(data) if data else []
        return self._memory_store.get(session_id, [])

    def append(self, session_id: str, role: str, content: str):
        history = self.get_history(session_id)
        history.append({"role": role, "content": content})

        if self.redis_enabled:
            self._client.setex(
                session_id,
                self.ttl,
                json.dumps(history),
            )
        else:
            self._memory_store[session_id] = history
