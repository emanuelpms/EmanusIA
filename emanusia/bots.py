"""Bots: "funcionários" com identidade (nome, time, cargo) cujo código é um workflow validado.

Ciclo de vida:  draft ──validar──► validated ──(ensaio)──(revisão)──ativar──► active ⇄ paused
- Ensaio: bots que mexem na tela (site, SAP, programas) precisam de um ensaio real que funcionou;
  "validado" só garante que o código está bem escrito, o ensaio garante que funciona no sistema.
- Revisão: bot criado por quem não é padrinho/admin só é ativado depois que um padrinho aprova.
Qualquer edição do código volta o bot para draft (a validação fica presa ao hash do código),
e a validação é refeita no momento de cada execução ("sempre validado pelo sistema")."""
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field

import yaml

from . import auth, config, events
from .engine import code_hash, validate

ROLES = ("worker", "boss")
STATUSES = ("draft", "validated", "active", "paused")
PALETTE = ["#4f8cff", "#22c7a9", "#ffb020", "#ff6b6b", "#a78bfa", "#38bdf8", "#f472b6", "#84cc16"]
_lock = threading.Lock()


@dataclass
class Bot:
    id: str
    name: str
    owner: str
    team: str = "Geral"
    role: str = "worker"
    description: str = ""
    color: str = ""
    code: dict = field(default_factory=lambda: {"params": {}, "steps": []})
    status: str = "draft"
    validation: dict = field(default_factory=dict)
    stats: dict = field(default_factory=lambda: {"runs": 0, "ok": 0, "errors": 0,
                                                 "last_run": None, "last_ok": None})
    origin: str = "manual"  # manual | construtor | gravacao | sap
    rehearsal: dict = field(default_factory=dict)   # {ok, hash, at, steps:[{path, text, ok, error, shot}]}
    review: dict = field(default_factory=dict)      # {status: pending|approved|changes, hash, by, at, note}
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        raise ValueError("nome de bot inválido")
    return s[:40]


def _dir():
    p = config.data_dir() / "bots"
    p.mkdir(exist_ok=True)
    return p


def _file(bot_id: str):
    if not re.fullmatch(r"[a-z0-9\-]+", bot_id or ""):
        raise ValueError("id de bot inválido")
    return _dir() / f"{bot_id}.yaml"


def _write(b: Bot):
    _file(b.id).write_text(yaml.safe_dump(b.public(), allow_unicode=True, sort_keys=False),
                           encoding="utf-8")


def get_bot(bot_id: str) -> Bot | None:
    try:
        f = _file(bot_id)
    except ValueError:
        return None
    if not f.exists():
        return None
    return Bot(**yaml.safe_load(f.read_text(encoding="utf-8")))


def list_bots(user: auth.User | None = None) -> list[Bot]:
    out = [Bot(**yaml.safe_load(f.read_text(encoding="utf-8"))) for f in sorted(_dir().glob("*.yaml"))]
    return [b for b in out if user is None or user.is_admin or b.owner == user.name]


def can_edit(b: Bot, user: auth.User) -> bool:
    return user.can_run and (user.is_admin or b.owner == user.name)


def create_bot(name: str, owner: auth.User, code: dict | None = None, team: str = "Geral",
               role: str = "worker", description: str = "", origin: str = "manual") -> Bot:
    if role not in ROLES:
        raise ValueError(f"cargo deve ser {ROLES}")
    bid = slug(name)
    with _lock:
        if _file(bid).exists():
            raise ValueError(f"já existe um bot chamado '{name}'")
        n = len(list(_dir().glob("*.yaml")))
        b = Bot(bid, name.strip()[:60], owner.name, team.strip()[:40] or "Geral", role,
                description[:500], PALETTE[n % len(PALETTE)],
                code or {"params": {}, "steps": []}, origin=origin)
        _write(b)
    auth.audit(owner.name, "bot_create", bot=bid, origin=origin)
    events.emit("bot_created", bot=bid, text=f"{b.name} foi contratado(a)!")
    return b


def update_bot(bot_id: str, user: auth.User, **fields) -> Bot:
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        if not can_edit(b, user):
            raise PermissionError("sem permissão para editar este bot")
        for k in ("team", "role", "description", "color", "name"):
            if fields.get(k) is not None:
                if k == "role" and fields[k] not in ROLES:
                    raise ValueError(f"cargo deve ser {ROLES}")
                setattr(b, k, str(fields[k])[:500])
        if fields.get("code") is not None:
            if not isinstance(fields["code"], dict):
                raise ValueError("código deve ser um objeto YAML")
            b.code = fields["code"]
            if b.validation.get("hash") != code_hash(b.code):
                b.status = "draft"  # código mudou: precisa validar de novo
        _write(b)
    auth.audit(user.name, "bot_update", bot=bot_id, fields=[k for k, v in fields.items() if v is not None])
    return b


def delete_bot(bot_id: str, user: auth.User):
    b = get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not can_edit(b, user):
        raise PermissionError("sem permissão")
    _file(bot_id).unlink()
    auth.audit(user.name, "bot_delete", bot=bot_id)
    events.emit("bot_removed", bot=bot_id)


def validate_bot(bot_id: str, user: auth.User) -> dict:
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        owner = auth.get_user(b.owner) or user
        rep = validate({**b.code, "name": ""}, owner)
        if bot_id in rep["bots"]:
            rep["errors"].append("o bot chama a si mesmo (call_bot)")
            rep["ok"] = False
        if not b.code.get("steps"):
            rep["errors"].append("o bot não tem passos")
            rep["ok"] = False
        rep.update(hash=code_hash(b.code), at=time.strftime("%Y-%m-%d %H:%M:%S"), by=user.name)
        b.validation = rep
        if rep["ok"] and b.status == "draft":
            b.status = "validated"
        elif not rep["ok"]:
            b.status = "draft"
        _write(b)
    auth.audit(user.name, "bot_validate", bot=bot_id, ok=rep["ok"], errors=rep["errors"][:5])
    return rep


SCREEN_GROUPS = {"web", "desktop", "sap", "access"}


def needs_rehearsal(code: dict) -> bool:
    from .tools import registry
    groups = set()
    for name in signature(code):
        try:
            groups.add(registry.get(name).group)
        except KeyError:
            pass
    return bool(groups & SCREEN_GROUPS)


def rehearsal_ok(b: Bot) -> bool:
    return not needs_rehearsal(b.code) or (b.rehearsal.get("ok") and b.rehearsal.get("hash") == code_hash(b.code))


def review_ok(b: Bot) -> bool:
    owner = auth.get_user(b.owner)
    if owner is None or owner.is_reviewer:
        return True  # padrinho/admin (ou modo local) libera o próprio bot
    return b.review.get("status") == "approved" and b.review.get("hash") == code_hash(b.code)


def next_step(b: Bot) -> dict:
    """O que falta para o bot trabalhar — mostrado como um único botão na tela."""
    if b.status == "draft" or b.validation.get("hash") != code_hash(b.code):
        return {"step": "validate", "text": "Conferir o robô"}
    if not rehearsal_ok(b):
        return {"step": "rehearse", "text": "Fazer o ensaio"}
    if not review_ok(b):
        if b.review.get("status") == "pending" and b.review.get("hash") == code_hash(b.code):
            return {"step": "waiting_review", "text": "Aguardando a revisão do padrinho"}
        return {"step": "submit_review", "text": "Enviar para revisão"}
    if b.status != "active":
        return {"step": "activate", "text": "Colocar para trabalhar"}
    return {"step": "done", "text": "Trabalhando"}


def _activation_block(b: Bot) -> str | None:
    if b.status == "draft" or b.validation.get("hash") != code_hash(b.code):
        return "valide o bot antes de ativar"
    if not rehearsal_ok(b):
        return "faça um ensaio com sucesso antes de ativar (o robô mexe em sistemas de verdade)"
    if not review_ok(b):
        return "o bot precisa ser aprovado por um padrinho/admin antes de ativar"
    return None


def set_active(bot_id: str, user: auth.User, active: bool) -> Bot:
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        if not can_edit(b, user):
            raise PermissionError("sem permissão")
        if active:
            why = _activation_block(b)
            if why:
                raise ValueError(why)
            b.status = "active"
        elif b.status == "active":
            b.status = "paused"
        _write(b)
    auth.audit(user.name, "bot_active" if active else "bot_pause", bot=bot_id)
    events.emit("bot_status", bot=bot_id, text=b.status)
    return b


def record_rehearsal(bot_id: str, rep: dict):
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            return
        b.rehearsal = rep
        _write(b)


def submit_review(bot_id: str, user: auth.User, note: str = "") -> Bot:
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        if not can_edit(b, user):
            raise PermissionError("sem permissão")
        if b.status == "draft" or not rehearsal_ok(b):
            raise ValueError("confira o robô e faça o ensaio antes de enviar para revisão")
        b.review = {"status": "pending", "hash": code_hash(b.code), "by": None, "at": time.strftime("%Y-%m-%d %H:%M"),
                    "requested_by": user.name, "note": note[:500]}
        _write(b)
    auth.audit(user.name, "review_request", bot=bot_id)
    events.emit("review_requested", bot=bot_id, text=f"{b.name} aguarda revisão")
    return b


def decide_review(bot_id: str, reviewer: auth.User, approve: bool, note: str = "") -> Bot:
    if not reviewer.is_reviewer:
        raise PermissionError("apenas padrinhos/admins revisam bots")
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        if b.owner == reviewer.name and not reviewer.is_admin:
            raise PermissionError("peça a outro padrinho para revisar o seu bot")
        if b.review.get("status") != "pending" or b.review.get("hash") != code_hash(b.code):
            raise ValueError("este bot não está aguardando revisão (ou mudou depois do pedido)")
        b.review.update(status="approved" if approve else "changes", by=reviewer.name,
                        at=time.strftime("%Y-%m-%d %H:%M"), note=note[:500])
        if approve and not _activation_block(b):
            b.status = "active"
        _write(b)
    auth.audit(reviewer.name, "review", bot=bot_id, approved=approve, note=note)
    events.emit("review_done", bot=bot_id, ok=approve,
                text=f"{b.name} {'aprovado e trabalhando' if approve else 'precisa de ajustes'}")
    return b


def pending_reviews() -> list[Bot]:
    return [b for b in list_bots() if b.review.get("status") == "pending"
            and b.review.get("hash") == code_hash(b.code)]


def ensure_runnable(b: Bot, user: auth.User, require_active: bool = False):
    """Revalida na hora de rodar: código idêntico ao validado e ainda válido para o usuário."""
    if not user.can_run:
        raise PermissionError("perfil sem permissão de execução")
    if not (user.is_admin or b.owner == user.name or b.status == "active"):
        raise PermissionError(f"bot '{b.name}' pertence a outro usuário")
    if b.status == "draft" or b.validation.get("hash") != code_hash(b.code):
        raise PermissionError(f"bot '{b.name}' não está validado")
    if require_active and b.status != "active":
        raise PermissionError(f"bot '{b.name}' não está ativo")
    rep = validate({**b.code, "name": ""}, user)
    if not rep["ok"]:
        raise PermissionError(f"bot '{b.name}' falhou na revalidação: " + "; ".join(rep["errors"][:3]))


def record_run(bot_id: str, ok: bool):
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            return
        st = b.stats
        st["runs"] += 1
        st["ok" if ok else "errors"] += 1
        st["last_run"], st["last_ok"] = time.strftime("%Y-%m-%d %H:%M:%S"), ok
        _write(b)


def signature(code: dict) -> tuple:
    """Sequência de ferramentas do bot (usada para não sugerir bots repetidos)."""
    out = []

    def walk(steps):
        for s in steps or []:
            if "tool" in s:
                out.append(s["tool"])
            for k in ("then", "else", "steps", "try", "catch"):
                walk(s.get(k))
    walk(code.get("steps"))
    return tuple(out)
