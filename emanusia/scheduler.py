"""Agendador embutido (sem dependências). Persiste em data/schedules.json.

Especificação de horário:
  {"every_minutes": 30}                       a cada 30 min
  {"daily_at": "08:30", "days": [0,1,2,3,4]}  seg-sex às 08:30 (0=segunda)
"""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable

from . import config


def next_run(spec: dict, after: datetime) -> datetime:
    if spec.get("every_minutes"):
        return after + timedelta(minutes=int(spec["every_minutes"]))
    if spec.get("daily_at"):
        h, m = map(int, spec["daily_at"].split(":"))
        days = spec.get("days") or list(range(7))
        for i in range(8):
            cand = (after + timedelta(days=i)).replace(hour=h, minute=m, second=0, microsecond=0)
            if cand > after and cand.weekday() in days:
                return cand
    raise ValueError("Agendamento inválido: use every_minutes ou daily_at")


def _file(): return config.data_dir() / "schedules.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    return json.loads(_file().read_text(encoding="utf-8")) if _file().exists() else []


def _save(items: list[dict]):
    _file().write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def list_schedules() -> list[dict]:
    with _lock:
        return _load()


def add_schedule(workflow: str | None, when: dict, params: dict | None = None, owner: str = "local",
                 unattended: bool = False, now: datetime | None = None, bot: str | None = None) -> dict:
    """Agenda um workflow OU um bot (bot precisa estar ativo na hora de rodar)."""
    if not (workflow or bot):
        raise ValueError("informe workflow ou bot")
    now = now or datetime.now()
    item = {"id": uuid.uuid4().hex[:8], "workflow": workflow, "bot": bot, "when": when,
            "params": params or {},
            "owner": owner, "unattended": unattended, "enabled": True,
            "next_run": next_run(when, now).isoformat(timespec="seconds"),
            "last_run": None, "last_status": None}
    with _lock:
        _save(_load() + [item])
    return item


def remove_schedule(sid: str):
    with _lock:
        items = _load()
        _save([i for i in items if i["id"] != sid])


def set_enabled(sid: str, enabled: bool):
    with _lock:
        items = _load()
        for i in items:
            if i["id"] == sid:
                i["enabled"] = enabled
        _save(items)


def record_result(sid: str, status: str):
    with _lock:
        items = _load()
        for i in items:
            if i["id"] == sid:
                i["last_status"] = status
        _save(items)


def due(now: datetime | None = None) -> list[dict]:
    """Retorna itens vencidos e já avança o próximo horário (sem repetir execuções atrasadas)."""
    now = now or datetime.now()
    out = []
    with _lock:
        items = _load()
        for i in items:
            if i["enabled"] and datetime.fromisoformat(i["next_run"]) <= now:
                out.append(dict(i))
                i["last_run"] = now.isoformat(timespec="seconds")
                i["next_run"] = next_run(i["when"], now).isoformat(timespec="seconds")
        if out:
            _save(items)
    return out


class Scheduler(threading.Thread):
    def __init__(self, on_due: Callable[[dict], None], interval: float = 20):
        super().__init__(daemon=True, name="emanusia-scheduler")
        self.on_due, self.interval, self._stop_evt = on_due, interval, threading.Event()

    def run(self):
        while not self._stop_evt.wait(self.interval):
            for item in due():
                try:
                    self.on_due(item)
                except Exception as e:  # nunca derruba o agendador
                    record_result(item["id"], f"erro: {e}")

    def stop(self): self._stop_evt.set()
