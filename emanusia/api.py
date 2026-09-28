"""API local da GUI. Só escuta em 127.0.0.1; sessão por cookie HttpOnly/SameSite=Strict;
valida o header Host (anti DNS-rebinding); arquivos do workspace são servidos em sandbox."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import auth, config, scheduler
from .jobs import JobManager
from .llm import OllamaClient
from .tools import registry
from .tools.workflow import _path, list_workflows

STATIC = Path(__file__).parent / "static"
manager: JobManager | None = None
_sched: scheduler.Scheduler | None = None
COOKIE = "emanusia_token"


@asynccontextmanager
async def lifespan(app):
    global manager, _sched
    manager = JobManager()
    if not os.environ.get("EMANUSIA_NO_SCHEDULER"):
        _sched = scheduler.Scheduler(manager.on_scheduled)
        _sched.start()
    yield
    if _sched:
        _sched.stop()


app = FastAPI(title="EmanusIA", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def host_guard(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse({"detail": "host inválido"}, status_code=400)
    return await call_next(request)


def current_user(request: Request) -> auth.User:
    u = auth.user_from_token(request.cookies.get(COOKIE))
    if u is None:
        raise HTTPException(401, "login necessário")
    return u


def admin(u: auth.User = Depends(current_user)) -> auth.User:
    if not u.is_admin:
        raise HTTPException(403, "apenas admin")
    return u


def runner(u: auth.User = Depends(current_user)) -> auth.User:
    if not u.can_run:
        raise HTTPException(403, "perfil somente leitura")
    return u


# ---------------- sessão ----------------
class Login(BaseModel):
    user: str
    password: str


@app.post("/api/login")
def login(b: Login, resp: Response):
    tok = auth.login(b.user, b.password)
    auth.audit(b.user, "login", ok=bool(tok))
    if not tok:
        raise HTTPException(401, "usuário ou senha inválidos")
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="strict", max_age=auth.SESSION_TTL)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    auth.logout(request.cookies.get(COOKIE, ""))
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    u = auth.user_from_token(request.cookies.get(COOKIE))
    return {"auth_enabled": auth.auth_enabled(),
            "user": None if u is None else {"name": u.name, "role": u.role}}


@app.get("/api/config")
def get_config(u: auth.User = Depends(current_user)):
    llm = OllamaClient()
    return {"settings": config.settings(), "installed_models": llm.models(),
            "workspace": str(config.workspace())}


class Settings(BaseModel):
    model: str | None = None
    vision_model: str | None = None


@app.post("/api/settings")
def set_settings(b: Settings, u: auth.User = Depends(admin)):
    auth.audit(u.name, "settings", **b.model_dump(exclude_none=True))
    return config.save_settings(**b.model_dump(exclude_none=True))


@app.get("/api/tools")
def tools(u: auth.User = Depends(current_user)):
    return [{"name": t.name, "description": t.description, "risky": bool(t.risky),
             "allowed": u.may_use_tool(t.name)} for t in registry.all_tools()]


# ---------------- tarefas ----------------
class AgentTask(BaseModel):
    task: str
    history: list = []


class WfRun(BaseModel):
    name: str
    params: dict = {}
    approve_risky: bool | None = None  # None = perguntar passo a passo


class Rec(BaseModel):
    url: str
    name: str


def _visible(u: auth.User, job) -> bool:
    return u.is_admin or job.user == u.name


def _job(job_id: str, u: auth.User):
    j = manager.jobs.get(job_id)
    if not j or not _visible(u, j):
        raise HTTPException(404, "tarefa não encontrada")
    return j


@app.post("/api/jobs/agent")
def job_agent(b: AgentTask, u: auth.User = Depends(runner)):
    return manager.submit_agent(b.task, u, b.history).public()


@app.post("/api/jobs/workflow")
def job_workflow(b: WfRun, u: auth.User = Depends(runner)):
    if not u.may_run_workflow(b.name):
        raise HTTPException(403, "sem permissão neste workflow")
    try:
        return manager.submit_workflow(b.name, b.params, u, b.approve_risky).public()
    except FileNotFoundError:
        raise HTTPException(404, "workflow não existe")


@app.post("/api/jobs/record")
def job_record(b: Rec, u: auth.User = Depends(runner)):
    _path(b.name)  # valida o nome
    return manager.submit_record(b.url, b.name, u).public()


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str, u: auth.User = Depends(runner)):
    _job(job_id, u)
    manager.stop_record(job_id)
    return {"ok": True}


class Approval(BaseModel):
    ok: bool


@app.post("/api/jobs/{job_id}/approve")
def job_approve(job_id: str, b: Approval, u: auth.User = Depends(runner)):
    _job(job_id, u)
    manager.approve(job_id, b.ok, u)
    return {"ok": True}


@app.get("/api/jobs")
def jobs(u: auth.User = Depends(current_user)):
    js = [j.public(full=False) for j in manager.jobs.values() if _visible(u, j)]
    return sorted(js, key=lambda j: -j["created"])[:50]


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str, u: auth.User = Depends(current_user)):
    return _job(job_id, u).public()


# ---------------- workflows ----------------
@app.get("/api/workflows")
def workflows(u: auth.User = Depends(current_user)):
    out = []
    for n in list_workflows():
        wf = yaml.safe_load(_path(n).read_text(encoding="utf-8")) or {}
        out.append({"name": n, "description": wf.get("description", ""),
                    "params": wf.get("params", {}), "steps": len(wf.get("steps", [])),
                    "allowed": u.may_run_workflow(n)})
    return out


@app.get("/api/workflows/{name}")
def workflow_get(name: str, u: auth.User = Depends(current_user)):
    try:
        return {"name": name, "yaml": _path(name).read_text(encoding="utf-8")}
    except FileNotFoundError:
        raise HTTPException(404)


class WfSave(BaseModel):
    yaml: str


@app.put("/api/workflows/{name}")
def workflow_put(name: str, b: WfSave, u: auth.User = Depends(runner)):
    try:
        wf = yaml.safe_load(b.yaml)
        assert isinstance(wf, dict) and isinstance(wf.get("steps"), list), "precisa de 'steps:' (lista)"
        for i, s in enumerate(wf["steps"], 1):
            registry.get(s["tool"])
            if not u.may_use_tool(s["tool"]):
                raise PermissionError(f"passo {i}: sem permissão para {s['tool']}")
    except (yaml.YAMLError, AssertionError, KeyError, TypeError, PermissionError) as e:
        raise HTTPException(422, f"Workflow inválido: {e}")
    _path(name).write_text(b.yaml, encoding="utf-8")
    auth.audit(u.name, "workflow_save", workflow=name)
    return {"ok": True}


@app.delete("/api/workflows/{name}")
def workflow_del(name: str, u: auth.User = Depends(runner)):
    _path(name).unlink(missing_ok=True)
    auth.audit(u.name, "workflow_delete", workflow=name)
    return {"ok": True}


# ---------------- agendamentos ----------------
class Sched(BaseModel):
    workflow: str
    when: dict
    params: dict = {}
    unattended: bool = False


@app.get("/api/schedules")
def schedules(u: auth.User = Depends(current_user)):
    return [s for s in scheduler.list_schedules() if u.is_admin or s["owner"] == u.name]


@app.post("/api/schedules")
def schedule_add(b: Sched, u: auth.User = Depends(runner)):
    if not u.may_run_workflow(b.workflow):
        raise HTTPException(403, "sem permissão neste workflow")
    if b.unattended and not u.is_admin:
        raise HTTPException(403, "apenas admin pode liberar execução sem supervisão (ações de risco)")
    try:
        item = scheduler.add_schedule(b.workflow, b.when, b.params, u.name, b.unattended)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    auth.audit(u.name, "schedule_add", **item)
    return item


@app.delete("/api/schedules/{sid}")
def schedule_del(sid: str, u: auth.User = Depends(runner)):
    if not any(s["id"] == sid and (u.is_admin or s["owner"] == u.name) for s in scheduler.list_schedules()):
        raise HTTPException(404)
    scheduler.remove_schedule(sid)
    auth.audit(u.name, "schedule_delete", id=sid)
    return {"ok": True}


class Toggle(BaseModel):
    enabled: bool


@app.post("/api/schedules/{sid}/toggle")
def schedule_toggle(sid: str, b: Toggle, u: auth.User = Depends(runner)):
    if not any(s["id"] == sid and (u.is_admin or s["owner"] == u.name) for s in scheduler.list_schedules()):
        raise HTTPException(404)
    scheduler.set_enabled(sid, b.enabled)
    return {"ok": True}


# ---------------- arquivos ----------------
@app.get("/api/files")
def files(u: auth.User = Depends(current_user)):
    root = config.workspace()
    return sorted(({"path": str(p.relative_to(root)), "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime} for p in root.rglob("*") if p.is_file()),
                  key=lambda f: -f["mtime"])[:300]


@app.get("/files/{path:path}")
def file_get(path: str, u: auth.User = Depends(current_user)):
    try:
        p = config.safe_path(path)
    except PermissionError:
        raise HTTPException(403)
    if not p.is_file():
        raise HTTPException(404)
    # sandbox: HTML gerado por workflows roda com origem opaca (sem acesso à API/cookies)
    return FileResponse(p, headers={"Content-Security-Policy": "sandbox allow-scripts",
                                    "X-Content-Type-Options": "nosniff"})


# ---------------- admin ----------------
class NewUser(BaseModel):
    name: str
    password: str
    role: str = "operator"
    tools: list[str] | None = None
    workflows: list[str] | None = None


@app.get("/api/users")
def users(u: auth.User = Depends(admin)):
    return auth.list_users()


@app.post("/api/users")
def user_add(b: NewUser, u: auth.User = Depends(admin)):
    try:
        auth.add_user(b.name, b.password, b.role, b.tools, b.workflows)
    except ValueError as e:
        raise HTTPException(422, str(e))
    auth.audit(u.name, "user_add", name=b.name, role=b.role)
    return {"ok": True}


@app.delete("/api/users/{name}")
def user_del(name: str, u: auth.User = Depends(admin)):
    try:
        auth.remove_user(name)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e))
    auth.audit(u.name, "user_remove", name=name)
    return {"ok": True}


@app.get("/api/audit")
def audit_log(u: auth.User = Depends(admin)):
    return auth.read_audit()


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
