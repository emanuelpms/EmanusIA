"""Barramento de eventos em memória: alimenta o painel do escritório e o histórico das tarefas."""
import itertools
import threading
import time
from collections import deque

_buf: deque = deque(maxlen=1000)
_ids = itertools.count(1)
_lock = threading.Lock()


def emit(kind: str, **data) -> dict:
    with _lock:
        ev = {"id": next(_ids), "ts": time.time(), "kind": kind, **data}
        _buf.append(ev)
    return ev


def since(last_id: int = 0, limit: int = 200) -> list[dict]:
    with _lock:
        return [e for e in _buf if e["id"] > last_id][-limit:]


def last_id() -> int:
    with _lock:
        return _buf[-1]["id"] if _buf else 0
