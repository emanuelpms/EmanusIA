"""Fila de tarefas: agente, workflows e gravações. Uma tarefa por vez roda no 'worker' (o Playwright
e o SAP GUI não são thread-safe); a gravação usa thread própria. Ações de risco pausam a tarefa
(status waiting_approval) até o usuário aprovar na GUI."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import yaml

from . import auth, config, scheduler
from .agent import Agent
from .llm import OllamaClient
from .tools import load_all
from .tools.workflow import _path, run_steps

APPROVAL_TIMEOUT = 600


@dataclass
class Job:
    kind: str
    title: str
    user: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "queued"  # queued|running|waiting_approval|done|error
    events: list = field(default_factory=list)
    result: object = None
    pending: dict | None = None
    created: float = field(default_factory=time.time)
    _approval: threading.Event = field(default_factory=threading.Event, repr=False)
    _approved: bool = False
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self, full: bool = True) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title, "user": self.user,
             "status": self.status, "pending": self.pending, "created": self.created}
        if full:
            d.update(events=self.events[-200:], result=self.result)
        return d


class JobManager:
    def __init__(self):
        load_all()
        self.jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emanusia-worker")

    # ---- infra ----
    def _confirm_for(self, job: Job, auto: bool | None) -> Callable[[str, dict], bool]:
        def confirm(name: str, args: dict) -> bool:
            if auto is not None:
                return auto
            job.pending, job.status = {"tool": name, "args": args}, "waiting_approval"
            job._approval.clear()
            got = job._approval.wait(APPROVAL_TIMEOUT)
            ok = got and job._approved
            job.pending, job.status = None, "running"
            job.events.append({"kind": "approval", "tool": name, "approved": bool(ok)})
            return bool(ok)
        return confirm

    def _start(self, job: Job, fn: Callable[[Job], object], own_thread=False) -> Job:
        self.jobs[job.id] = job

        def runner():
            job.status = "running"
            try:
                job.result = fn(job)
                job.status = "done"
            except Exception as e:
                job.result, job.status = f"{type(e).__name__}: {e}", "error"
                auth.audit(job.user, "job_error", job=job.id, kind=job.kind, error=str(e))
        if own_thread:
            threading.Thread(target=runner, daemon=True).start()
        else:
            self._pool.submit(runner)
        return job

    # ---- tipos de tarefa ----
    def submit_agent(self, task: str, user: auth.User, history: list | None = None) -> Job:
        job = Job("agent", task[:80], user.name)

        def fn(j: Job):
            agent = Agent(OllamaClient(), confirm=self._confirm_for(j, None), user=user,
                          on_event=lambda k, d: j.events.append({"kind": k, **d}))
            return agent.run(task, history)
        return self._start(job, fn)

    def submit_workflow(self, name: str, params: dict, user: auth.User,
                        approve_risky: bool | None = None, source: str = "manual") -> Job:
        """approve_risky: True = aprova tudo (checkbox do usuário / agendamento 'unattended');
        False = nega; None = pergunta passo a passo na GUI."""
        wf = yaml.safe_load(_path(name).read_text(encoding="utf-8"))
        job = Job("workflow", f"{name} ({source})", user.name)
        auto = approve_risky if not (approve_risky and not user.can_approve) else False

        def fn(j: Job):
            return run_steps(wf, params, confirm=self._confirm_for(j, auto), user=user)
        return self._start(job, fn)

    def submit_record(self, url: str, name: str, user: auth.User) -> Job:
        from .recorder import record
        job = Job("record", f"Gravando {name}", user.name)
        auth.audit(user.name, "record_start", url=url, name=name)
        return self._start(job, lambda j: record(url, name, j._stop), own_thread=True)

    def stop_record(self, job_id: str):
        self.jobs[job_id]._stop.set()

    def approve(self, job_id: str, ok: bool, user: auth.User):
        job = self.jobs[job_id]
        if not user.can_approve:
            raise PermissionError("Sem permissão para aprovar")
        auth.audit(user.name, "approval", job=job_id, approved=ok, pending=job.pending)
        job._approved = ok
        job._approval.set()

    # ---- agendador ----
    def on_scheduled(self, item: dict):
        owner = auth.get_user(item["owner"])
        if owner is None:
            scheduler.record_result(item["id"], "erro: usuário removido")
            return
        job = self.submit_workflow(item["workflow"], item["params"], owner,
                                   approve_risky=bool(item["unattended"]), source="agendado")
        scheduler.record_result(item["id"], f"disparado (job {job.id})")
