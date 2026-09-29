"""Bots: "funcionários" com identidade (nome, time, cargo) cujo código é um workflow validado.

Ciclo de vida:  draft ──validar──► validated ──ativar──► active ⇄ paused
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
    origin: str = "manual"  # manual | ia | sugestao | gravacao
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


def set_active(bot_id: str, user: auth.User, active: bool) -> Bot:
    with _lock:
        b = get_bot(bot_id)
        if b is None:
            raise KeyError(bot_id)
        if not can_edit(b, user):
            raise PermissionError("sem permissão")
        if active:
            if b.status == "draft" or b.validation.get("hash") != code_hash(b.code):
                raise ValueError("valide o bot antes de ativar")
            b.status = "active"
        elif b.status == "active":
            b.status = "paused"
        _write(b)
    auth.audit(user.name, "bot_active" if active else "bot_pause", bot=bot_id)
    events.emit("bot_status", bot=bot_id, text=b.status)
    return b


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
