"""API da GUI. Padrão: só 127.0.0.1, com checagem do header Host (anti DNS-rebinding).
Modo LAN (EMANUSIA_LAN=1 / `emanusia serve --lan`): a equipe acessa pela rede; exige usuários
cadastrados (login obrigatório). Sessão por cookie HttpOnly/SameSite=Strict; arquivos do
workspace são servidos em sandbox."""
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import auth, bots, config, events, plain, scheduler, vault
from .engine import code_hash, validate
from .jobs import JobManager
from .tools import registry
from .tools.workflow import _path, list_workflows

LAN = os.environ.get("EMANUSIA_LAN") == "1"

STATIC = Path(__file__).parent / "static"
manager: JobManager | None = None
_sched: scheduler.Scheduler | None = None
COOKIE = "emanusia_token"


@asynccontextmanager
async def lifespan(app):
    global manager, _sched
    from .doctor import setup_logging
    setup_logging()
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
    if LAN:
        if not auth.auth_enabled():  # rede sem login = qualquer um vira admin: proibido
            return JSONResponse({"detail": "Modo LAN exige usuários: rode `emanusia user add admin "
                                 "--role admin`"}, status_code=503)
    else:
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
            "user": None if u is None else {"name": u.name, "role": u.role, "reviewer": u.is_reviewer}}


@app.get("/api/config")
def get_config(u: auth.User = Depends(current_user)):
    return {"settings": config.settings(), "workspace": str(config.workspace())}


class Settings(BaseModel):
    email_provider: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_from: str | None = None
    imap_host: str | None = None
    imap_user: str | None = None
    company_name: str | None = None
    allowed_folders: list[str] | None = None


@app.get("/api/doctor")
def doctor_api(u: auth.User = Depends(admin)):
    """Diagnóstico completo do computador (Ajustes → Diagnóstico)."""
    from . import doctor
    return doctor.run()


@app.post("/api/settings")
def set_settings(b: Settings, u: auth.User = Depends(admin)):
    d = b.model_dump(exclude_none=True)
    if d.get("email_provider", "outlook") not in ("outlook", "smtp"):
        raise HTTPException(422, "email_provider deve ser outlook ou smtp")
    if "allowed_folders" in d:
        d["allowed_folders"] = [f.strip() for f in d["allowed_folders"] if f.strip()]
    auth.audit(u.name, "settings", **d)
    return config.save_settings(**d)


@app.get("/api/tools")
def tools(u: auth.User = Depends(current_user)):
    return [{"name": t.name, "description": t.description, "risky": bool(t.risky),
             "group": t.group, "args": list(t.parameters), "required": list(t.required),
             "allowed": u.may_use_tool(t.name)} for t in registry.all_tools()]


# ---------------- tarefas ----------------
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
        path = _path(name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    wf = _parse(b.yaml)
    rep = validate(wf, u)
    if not rep["ok"]:
        raise HTTPException(422, "Workflow inválido: " + "; ".join(rep["errors"][:8]))
    path.write_text(b.yaml, encoding="utf-8")
    auth.audit(u.name, "workflow_save", workflow=name)
    return {"ok": True}


@app.delete("/api/workflows/{name}")
def workflow_del(name: str, u: auth.User = Depends(runner)):
    _path(name).unlink(missing_ok=True)
    auth.audit(u.name, "workflow_delete", workflow=name)
    return {"ok": True}


# ---------------- agendamentos ----------------
class Sched(BaseModel):
    workflow: str | None = None
    bot: str | None = None
    when: dict
    params: dict = {}
    unattended: bool = False


@app.get("/api/schedules")
def schedules(u: auth.User = Depends(current_user)):
    return [s for s in scheduler.list_schedules() if u.is_admin or s["owner"] == u.name]


@app.post("/api/schedules")
def schedule_add(b: Sched, u: auth.User = Depends(runner)):
    if bool(b.workflow) == bool(b.bot):
        raise HTTPException(422, "informe workflow OU bot")
    if b.workflow and not u.may_run_workflow(b.workflow):
        raise HTTPException(403, "sem permissão neste workflow")
    if b.bot:
        bt = bots.get_bot(b.bot)
        if bt is None or not bots.can_edit(bt, u):
            raise HTTPException(404, "bot não encontrado")
        if bt.status != "active":
            raise HTTPException(422, "ative o bot (validado) antes de agendar")
    if b.unattended and not u.is_admin:
        raise HTTPException(403, "apenas admin pode liberar execução sem supervisão (ações de risco)")
    try:
        item = scheduler.add_schedule(b.workflow, b.when, b.params, u.name, b.unattended, bot=b.bot)
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
    return sorted(({"path": p.relative_to(root).as_posix(), "size": p.stat().st_size,
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
    reviewer: bool = False


@app.get("/api/users")
def users(u: auth.User = Depends(admin)):
    return auth.list_users()


@app.post("/api/users")
def user_add(b: NewUser, u: auth.User = Depends(admin)):
    try:
        auth.add_user(b.name, b.password, b.role, b.tools, b.workflows, b.reviewer)
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


# ---------------- validação ----------------
def _parse(text: str) -> dict:
    try:
        wf = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(422, f"YAML inválido: {e}")
    if not isinstance(wf, dict):
        raise HTTPException(422, "o código deve ser um objeto YAML com 'steps:'")
    return wf


class Code(BaseModel):
    yaml: str


@app.post("/api/validate")
def validate_code(b: Code, u: auth.User = Depends(current_user)):
    try:
        wf = yaml.safe_load(b.yaml)
    except yaml.YAMLError as e:
        return {"ok": False, "errors": [f"YAML inválido: {e}"], "warnings": [], "risky_tools": [],
                "plain_errors": ["O texto do robô está mal formatado (YAML)."]}
    rep = validate(wf, u)
    return {**rep, "plain_errors": plain.explain_validation(rep, wf if isinstance(wf, dict) else None)}


# ---------------- bots ----------------
class BotIn(BaseModel):
    name: str | None = None
    team: str | None = None
    role: str | None = None
    description: str | None = None
    color: str | None = None
    yaml: str | None = None
    code: dict | None = None  # passos editados na tela "simples" (sem YAML)
    origin: str = "manual"


def _bot(bot_id: str, u: auth.User, edit: bool = False) -> bots.Bot:
    b = bots.get_bot(bot_id)
    reviewing = u.is_reviewer and b is not None and b.review.get("status") == "pending"
    if b is None or (edit and not bots.can_edit(b, u)) or \
            (not edit and not (u.is_admin or b.owner == u.name or b.status == "active" or reviewing)):
        raise HTTPException(404, "bot não encontrado")
    return b


def _bot_out(b: bots.Bot, u: auth.User) -> dict:
    d = b.public()
    d["yaml"] = yaml.safe_dump(b.code, allow_unicode=True, sort_keys=False) \
        if bots.can_edit(b, u) else None
    d["can_edit"] = bots.can_edit(b, u)
    d["next"] = bots.next_step(b)
    d["params"] = [{"name": k, "label": plain.param_label(k), "value": v} for k, v in (b.code.get("params") or {}).items()]
    d["plain_errors"] = plain.explain_validation(b.validation, b.code) if b.validation else []
    if not d["can_edit"]:
        d["code"] = None
    return d


@app.get("/api/bots")
def bots_list(u: auth.User = Depends(current_user)):
    """Meus bots (admin: todos) + bots ativos de outras pessoas, que qualquer operador pode rodar."""
    return [_bot_out(b, u) for b in bots.list_bots()
            if u.is_admin or b.owner == u.name or b.status == "active"]


@app.post("/api/bots")
def bot_create(b: BotIn, u: auth.User = Depends(runner)):
    code = b.code if b.code is not None else (_parse(b.yaml) if b.yaml else None)
    try:
        bt = bots.create_bot(b.name or "", u, code, b.team or "Geral", b.role or "worker",
                             b.description or "", origin=b.origin if b.origin in
                             ("manual", "construtor", "gravacao") else "manual")
    except ValueError as e:
        raise HTTPException(422, str(e))
    if code:
        bots.validate_bot(bt.id, u)
    return _bot_out(bots.get_bot(bt.id), u)


@app.get("/api/bots/{bot_id}")
def bot_get(bot_id: str, u: auth.User = Depends(current_user)):
    return _bot_out(_bot(bot_id, u), u)


@app.put("/api/bots/{bot_id}")
def bot_update(bot_id: str, b: BotIn, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    try:
        bt = bots.update_bot(bot_id, u, team=b.team, role=b.role, description=b.description,
                             color=b.color, name=b.name, code=b.code if b.code is not None else
                             (_parse(b.yaml) if b.yaml else None))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _bot_out(bt, u)


@app.delete("/api/bots/{bot_id}")
def bot_delete(bot_id: str, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    bots.delete_bot(bot_id, u)
    return {"ok": True}


@app.post("/api/bots/{bot_id}/validate")
def bot_validate(bot_id: str, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    return bots.validate_bot(bot_id, u)


class Active(BaseModel):
    active: bool


@app.post("/api/bots/{bot_id}/active")
def bot_active(bot_id: str, b: Active, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    try:
        return _bot_out(bots.set_active(bot_id, u, b.active), u)
    except ValueError as e:
        raise HTTPException(422, str(e))


class BotRun(BaseModel):
    params: dict = {}
    approve_risky: bool | None = None


@app.post("/api/bots/{bot_id}/run")
def bot_run(bot_id: str, b: BotRun, u: auth.User = Depends(runner)):
    _bot(bot_id, u)
    try:
        return manager.submit_bot(bot_id, b.params, u, b.approve_risky).public()
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ---------------- modo simples: ensinar mostrando, ensaio, revisão ----------------
class Teach(BaseModel):
    name: str
    url: str
    team: str = "Geral"
    description: str = ""
    credential: str = ""


@app.post("/api/teach")
def teach(b: Teach, u: auth.User = Depends(runner)):
    if not b.url.startswith(("http://", "https://", "file://")):
        raise HTTPException(422, "informe o endereço do site começando com https://")
    if bots.get_bot(bots.slug(b.name)):
        raise HTTPException(422, f"já existe um bot chamado '{b.name}'")
    if b.credential and not vault.exists(u.name, b.credential):
        raise HTTPException(422, "cadastre esse acesso em 🔑 Acessos primeiro")
    return manager.submit_teach(b.url, b.name, u, b.team, b.description, b.credential).public()


@app.get("/api/bots/{bot_id}/steps")
def bot_steps(bot_id: str, u: auth.User = Depends(current_user)):
    b = _bot(bot_id, u)
    if not (bots.can_edit(b, u) or u.is_reviewer):
        raise HTTPException(404)
    return {"steps": plain.describe_code(b.code), "next": bots.next_step(b), "status": b.status,
            "needs_rehearsal": bots.needs_rehearsal(b.code), "rehearsal": b.rehearsal, "review": b.review,
            # ensaio da versão atual do robô (deu certo OU falhou: a falha é o que mais importa mostrar)
            "rehearsal_current": bool(b.rehearsal) and b.rehearsal.get("hash") == code_hash(b.code),
            "params": [{"name": k, "label": plain.param_label(k), "value": v} for k, v in (b.code.get("params") or {}).items()],
            "plain_errors": plain.explain_validation(b.validation, b.code) if b.validation else [],
            "fields": {k: plain.field_label(k) for k in plain.FIELD}}


class Rehearse(BaseModel):
    params: dict = {}


@app.post("/api/bots/{bot_id}/rehearse")
def bot_rehearse(bot_id: str, b: Rehearse, u: auth.User = Depends(runner)):
    bt = _bot(bot_id, u, edit=True)
    if bt.status == "draft":
        raise HTTPException(422, "confira o robô antes do ensaio: " + "; ".join(
            plain.explain_validation(bt.validation, bt.code)[:3]))
    return manager.submit_rehearsal(bot_id, u, b.params).public()


class Fix(BaseModel):
    path: str


@app.post("/api/bots/{bot_id}/fix")
def bot_fix(bot_id: str, b: Fix, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    return manager.submit_fix(bot_id, b.path, u).public()


class Insert(BaseModel):
    after: int
    url: str = ""


@app.post("/api/bots/{bot_id}/record")
def bot_record_more(bot_id: str, b: Insert, u: auth.User = Depends(runner)):
    """Adicionar passos mostrando (grava a partir do passo `after`)."""
    _bot(bot_id, u, edit=True)
    url = b.url.strip()
    if url and "://" not in url:
        url = "https://" + url
    return manager.submit_insert(bot_id, b.after, url, u).public()


class SimpleStep(BaseModel):
    kind: str
    after: int
    fields: dict = {}


@app.get("/api/simple-steps")
def simple_steps(u: auth.User = Depends(current_user)):
    from .rehearsal import SIMPLE_STEPS
    return SIMPLE_STEPS


@app.post("/api/bots/{bot_id}/steps")
def bot_add_step(bot_id: str, b: SimpleStep, u: auth.User = Depends(runner)):
    from .rehearsal import add_simple_step
    _bot(bot_id, u, edit=True)
    try:
        return add_simple_step(bot_id, u, b.kind, b.after, b.fields)
    except ValueError as e:
        raise HTTPException(422, str(e))


class SapTeach(BaseModel):
    name: str
    script: str
    team: str = "Geral"
    credential: str = ""


@app.post("/api/teach/sap")
def teach_sap(b: SapTeach, u: auth.User = Depends(runner)):
    """Ensinar no SAP: importa a gravação do gravador de scripts do SAP (.vbs)."""
    from . import sapimport
    if b.credential and not vault.exists(u.name, b.credential):
        raise HTTPException(422, "cadastre esse acesso em 🔑 Acessos primeiro")
    try:
        res = sapimport.parse(b.script, b.credential)
        bt = bots.create_bot(b.name, u, res["code"], b.team, description="Aprendido de uma gravação do SAP",
                             origin="gravacao")
    except ValueError as e:
        raise HTTPException(422, str(e))
    bots.validate_bot(bt.id, u)
    return {"bot": _bot_out(bots.get_bot(bt.id), u), "warnings": res["warnings"]}


@app.get("/api/bots/{bot_id}/shots/{name}")
def bot_shot(bot_id: str, name: str, u: auth.User = Depends(current_user)):
    from .rehearsal import shots_dir
    b = _bot(bot_id, u)
    if not (bots.can_edit(b, u) or u.is_reviewer) or not re.fullmatch(r"[\w]+\.jpg", name):
        raise HTTPException(404)
    p = shots_dir(b.id) / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


class ReviewReq(BaseModel):
    note: str = ""


@app.post("/api/bots/{bot_id}/review/submit")
def review_submit(bot_id: str, b: ReviewReq, u: auth.User = Depends(runner)):
    _bot(bot_id, u, edit=True)
    try:
        return _bot_out(bots.submit_review(bot_id, u, b.note), u)
    except ValueError as e:
        raise HTTPException(422, str(e))


class ReviewDecision(BaseModel):
    approve: bool
    note: str = ""


@app.post("/api/bots/{bot_id}/review")
def review_decide(bot_id: str, b: ReviewDecision, u: auth.User = Depends(runner)):
    _bot(bot_id, u)
    if not b.approve and len(b.note.strip()) < 3:
        raise HTTPException(422, "explique o que precisa ser ajustado")
    try:
        return _bot_out(bots.decide_review(bot_id, u, b.approve, b.note), u)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/reviews")
def reviews(u: auth.User = Depends(current_user)):
    if not u.is_reviewer:
        return []
    return [{**_bot_out(b, u), "steps": plain.describe_code(b.code)} for b in bots.pending_reviews()
            if u.is_admin or b.owner != u.name]


# ---------------- cofre de acessos ----------------
class Cred(BaseModel):
    username: str = ""
    password: str | None = None  # None = manter a atual
    label: str = ""
    kind: str = "web"
    url: str = ""
    extra: dict = {}
    shared: bool = False


@app.get("/api/credentials")
def credentials(u: auth.User = Depends(current_user)):
    return vault.list_credentials(u.name)


@app.put("/api/credentials/{name}")
def credential_put(name: str, b: Cred, u: auth.User = Depends(runner)):
    if b.shared and not u.is_admin:
        raise HTTPException(403, "apenas admin cria acessos compartilhados")
    try:
        c = vault.set_credential(vault.SHARED if b.shared else u.name, name, b.username, b.password or None,
                                 b.label, b.kind, b.url, b.extra)
    except ValueError as e:
        raise HTTPException(422, str(e))
    auth.audit(u.name, "credential_set", name=name, shared=b.shared, password_changed=b.password is not None)
    return c


@app.delete("/api/credentials/{name}")
def credential_del(name: str, shared: bool = False, u: auth.User = Depends(runner)):
    if shared and not u.is_admin:
        raise HTTPException(403, "apenas admin")
    try:
        vault.delete_credential(vault.SHARED if shared else u.name, name)
    except KeyError:
        raise HTTPException(404)
    auth.audit(u.name, "credential_delete", name=name, shared=shared)
    return {"ok": True}


# ---------------- escritório ----------------
GENERIC = {"step": "trabalhando", "log": "trabalhando", "job_done": "", "waiting": "aguardando aprovação"}


@app.get("/api/office")
def office(since: int = 0, u: auth.User = Depends(current_user)):
    active = manager.active_bots()
    visible = {}
    out = []
    for b in bots.list_bots():
        mine = u.is_admin or b.owner == u.name
        visible[b.id] = mine
        out.append({"id": b.id, "name": b.name, "team": b.team, "role": b.role, "color": b.color,
                    "status": b.status, "activity": active.get(b.id, "idle"), "stats": b.stats,
                    "description": b.description if mine or b.status == "active" else "",
                    "can_run": b.status == "active" or (mine and b.status == "validated"),
                    "mine": mine})
    evs = []
    for e in events.since(since):
        if e.get("bot") not in visible:
            continue
        if not visible.get(e.get("bot"), u.is_admin) and e["kind"] in GENERIC:
            e = {**e, "text": GENERIC[e["kind"]]}  # não vaza dados de bots de outras pessoas
        evs.append(e)
    today = time.strftime("%Y-%m-%d")
    return {"company": config.settings().get("company_name"), "bots": out,
            "events": evs, "cursor": events.last_id(),
            "runs_today": sum(1 for j in manager.jobs.values()
                              if j.kind in ("bot", "workflow")
                              and time.strftime("%Y-%m-%d", time.localtime(j.created)) == today)}


# ---------------- construtor de robôs (sem código) ----------------
@app.get("/api/catalog")
def catalog_api(u: auth.User = Depends(current_user)):
    from . import catalog
    return catalog.catalog(u)


def _err_path(e: str) -> str | None:
    """'steps[3].else[1]: ...' -> '3.else.1' (mesmo formato de caminho do motor e do ensaio)."""
    m = re.match(r"steps\[(\d+)\]((?:\.\w+\[\d+\])*)", e)
    if not m:
        return None
    parts = [m.group(1)]
    for key, n in re.findall(r"\.(\w+)\[(\d+)\]", m.group(2) or ""):
        parts += [key, n] if key in ("else", "catch") else [n]
    return ".".join(parts)


class BuilderCode(BaseModel):
    code: dict


@app.post("/api/builder/check")
def builder_check(b: BuilderCode, u: auth.User = Depends(current_user)):
    """Confere o robô montado no construtor e diz, passo a passo, o que falta."""
    rep = validate(b.code, u)
    friendly = plain.explain_validation(rep, b.code)
    problems = [{"path": _err_path(e), "text": re.sub(r"^Passo [\d →]+: ", "", t), "tech": e}
                for e, t in zip(rep["errors"], friendly)]
    warnings = [{"path": _err_path(w), "text": re.sub(r"^steps\[[^:]*:\s*", "", w)} for w in rep["warnings"]]
    return {"ok": rep["ok"], "problems": problems, "warnings": warnings, "risky_tools": rep["risky_tools"],
            "steps": plain.describe_code(b.code), "yaml": yaml.safe_dump(b.code, allow_unicode=True, sort_keys=False)}


@app.get("/builder", response_class=HTMLResponse)
def builder_page():
    return (STATIC / "builder.html").read_text(encoding="utf-8")


@app.get("/office", response_class=HTMLResponse)
def office_page():
    return (STATIC / "office.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
