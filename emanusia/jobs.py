"""Fila de tarefas: agente (chat), bots, workflows, gravações e geração de código.
Uma tarefa por vez roda no 'worker' (o Playwright e o SAP GUI não são thread-safe); as demais
ficam na fila. A gravação usa thread própria. Ações de risco pausam a tarefa (waiting_approval)
até alguém aprovar na GUI. Tudo que acontece vira evento para o painel do escritório."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import logging

from . import auth, bots, events, plain, scheduler
from .agent import Agent
from .llm import OllamaClient
from .tools import load_all
from .tools.workflow import load_workflow, run_steps

APPROVAL_TIMEOUT = 600
ASSISTANT = "assistente"  # personagem do escritório que representa o chat


@dataclass
class Job:
    kind: str
    title: str
    user: str
    bot: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "queued"  # queued|running|waiting_approval|done|error
    events: list = field(default_factory=list)
    result: object = None
    pending: dict | None = None
    plain_error: str | None = None
    created: float = field(default_factory=time.time)
    _approval: threading.Event = field(default_factory=threading.Event, repr=False)
    _approved: bool = False
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self, full: bool = True) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title, "user": self.user,
             "bot": self.bot, "status": self.status, "pending": self.pending, "created": self.created,
             "plain_error": self.plain_error}
        if full:
            d.update(events=self.events[-200:], result=self.result)
        return d


class JobManager:
    def __init__(self, llm_factory: Callable = OllamaClient):
        load_all()
        self.jobs: dict[str, Job] = {}
        self.llm_factory = llm_factory
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emanusia-worker")

    # ---- infra ----
    def _confirm_for(self, job: Job, auto: bool | None) -> Callable[[str, dict], bool]:
        def confirm(name: str, args: dict) -> bool:
            if auto is not None:
                return auto
            job.pending = {"tool": name, "args": args, "text": plain.describe_action(name, args)}
            job.status = "waiting_approval"
            events.emit("waiting", bot=job.bot or ASSISTANT, job=job.id, text=f"aprovar {name}?")
            job._approval.clear()
            got = job._approval.wait(APPROVAL_TIMEOUT)
            ok = got and job._approved
            job.pending, job.status = None, "running"
            job.events.append({"kind": "approval", "tool": name, "approved": bool(ok)})
            return bool(ok)
        return confirm

    def _start(self, job: Job, fn: Callable[[Job], object], own_thread=False) -> Job:
        self.jobs[job.id] = job
        actor = job.bot or (ASSISTANT if job.kind == "agent" else None)

        def runner():
            job.status = "running"
            if actor:
                events.emit("job_start", bot=actor, job=job.id, text=job.title[:60])
            try:
                job.result = fn(job)
                job.status = "done"
            except Exception as e:
                job.result, job.status = f"{type(e).__name__}: {e}", "error"
                job.plain_error = plain.explain_error(job.result)
                logging.getLogger("emanusia").exception("tarefa %s (%s: %s) falhou", job.id, job.kind, job.title)
                auth.audit(job.user, "job_error", job=job.id, kind=job.kind, error=str(e))
            if actor:
                events.emit("job_done", bot=actor, job=job.id, ok=job.status == "done",
                            text="" if job.status == "done" else str(job.result)[:120])
        if own_thread:
            threading.Thread(target=runner, daemon=True).start()
        else:
            self._pool.submit(runner)
        return job

    def active_bots(self) -> dict[str, str]:
        """bot_id -> status das tarefas em andamento/fila (para o escritório)."""
        out = {}
        for j in self.jobs.values():
            actor = j.bot or (ASSISTANT if j.kind == "agent" else None)
            if actor and j.status in ("queued", "running", "waiting_approval"):
                if out.get(actor) != "running":
                    out[actor] = j.status
        return out

    # ---- tipos de tarefa ----
    def submit_agent(self, task: str, user: auth.User, history: list | None = None) -> Job:
        job = Job("agent", task[:80], user.name)

        def on_event(k, d, j=job):
            j.events.append({"kind": k, **d})
            if k == "tool_call":
                events.emit("step", bot=ASSISTANT, text=d["name"])

        def fn(j: Job):
            agent = Agent(self.llm_factory(), confirm=self._confirm_for(j, None), user=user,
                          on_event=on_event)
            return agent.run(task, history)
        return self._start(job, fn)

    def _auto(self, approve_risky, user):
        """True = aprova tudo (checkbox / agendamento 'sem supervisão'); False = nega;
        None = pergunta passo a passo na GUI. Quem não pode aprovar nunca aprova."""
        return False if (approve_risky and not user.can_approve) else approve_risky

    def submit_workflow(self, name: str, params: dict, user: auth.User,
                        approve_risky: bool | None = None, source: str = "manual") -> Job:
        wf = load_workflow(name)
        job = Job("workflow", f"{name} ({source})", user.name)
        auto = self._auto(approve_risky, user)

        def fn(j: Job):
            return run_steps(wf, params, confirm=self._confirm_for(j, auto), user=user,
                             emit=lambda k, ev: j.events.append(ev))
        return self._start(job, fn)

    def submit_bot(self, bot_id: str, params: dict, user: auth.User,
                   approve_risky: bool | None = None, source: str = "manual",
                   require_active: bool = False) -> Job:
        b = bots.get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        bots.ensure_runnable(b, user, require_active)  # falha cedo, antes de entrar na fila
        job = Job("bot", f"{b.name} ({source})", user.name, bot=b.id)
        auto = self._auto(approve_risky, user)

        def fn(j: Job):
            fresh = bots.get_bot(bot_id)
            bots.ensure_runnable(fresh, user, require_active)  # revalida na hora de rodar
            try:
                res = run_steps({**fresh.code, "name": ""}, params, confirm=self._confirm_for(j, auto),
                                user=user, bot=fresh.id, emit=lambda k, ev: j.events.append(ev))
            except Exception:
                bots.record_run(fresh.id, False)
                raise
            bots.record_run(fresh.id, True)
            return res
        return self._start(job, fn)

    def submit_teach(self, url: str, name: str, user: auth.User, team: str = "Geral",
                     description: str = "", credential: str = "") -> Job:
        from .rehearsal import teach
        job = Job("teach", f"Ensinando: {name}", user.name)
        auth.audit(user.name, "teach_start", url=url, name=name)
        return self._start(job, lambda j: teach(url, name, user, j._stop, team, description, credential).public(),
                           own_thread=True)

    def submit_rehearsal(self, bot_id: str, user: auth.User, params: dict | None = None) -> Job:
        from .rehearsal import rehearse
        b = bots.get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        job = Job("rehearsal", f"Ensaio: {b.name}", user.name)  # sem `bot`: o ensaio emite os eventos

        def fn(j: Job):
            return rehearse(bot_id, user, self._confirm_for(j, None), params,
                            emit=lambda k, ev: j.events.append(ev))
        return self._start(job, fn)

    def submit_fix(self, bot_id: str, path: str, user: auth.User) -> Job:
        from .rehearsal import fix_step
        b = bots.get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        job = Job("fix", f"Mostrando onde fica: {b.name}", user.name)
        return self._start(job, lambda j: fix_step(bot_id, path, user, j._stop, self._confirm_for(j, None)))

    def submit_insert(self, bot_id: str, after: int, url: str, user: auth.User) -> Job:
        """Adicionar passos mostrando: roda no worker (usa o navegador dos bots) até a pessoa clicar em Terminei."""
        from .rehearsal import record_insert
        b = bots.get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        job = Job("teach", f"Ensinando mais passos: {b.name}", user.name)
        return self._start(job, lambda j: record_insert(bot_id, after, user, j._stop, url, self._confirm_for(j, None)))

    def submit_generate(self, description: str, user: auth.User) -> Job:
        from .botgen import generate
        job = Job("generate", f"Gerar bot: {description[:60]}", user.name)
        return self._start(job, lambda j: generate(description, self.llm_factory(), user))

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
        try:
            if item.get("bot"):
                job = self.submit_bot(item["bot"], item["params"], owner, bool(item["unattended"]),
                                      "agendado", require_active=True)
            else:
                job = self.submit_workflow(item["workflow"], item["params"], owner,
                                           approve_risky=bool(item["unattended"]), source="agendado")
        except Exception as e:
            scheduler.record_result(item["id"], f"erro: {e}")
            return
        scheduler.record_result(item["id"], f"disparado (job {job.id})")
