"""Multiusuário: papéis, permissões por ferramenta/workflow, tokens de sessão e trilha de auditoria.
Sem nenhum usuário cadastrado o app roda em modo local (1 pessoa = admin)."""
import hashlib
import hmac
import json
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

import yaml

from . import config

ROLES = ("admin", "operator", "viewer")
_SECRET_KEY = re.compile(r"pass|secret|token|senha|key", re.I)


@dataclass
class User:
    name: str
    role: str = "operator"
    tools: list[str] | None = None       # whitelist de ferramentas (None = todas)
    workflows: list[str] | None = None   # whitelist de workflows (None = todos)

    @property
    def is_admin(self): return self.role == "admin"
    @property
    def can_run(self): return self.role in ("admin", "operator")   # viewer só consulta
    @property
    def can_approve(self): return self.can_run

    def may_use_tool(self, name: str) -> bool:
        return self.can_run and (self.tools is None or name in self.tools)

    def may_run_workflow(self, name: str) -> bool:
        return self.can_run and (self.workflows is None or name in self.workflows)


LOCAL_ADMIN = User("local", "admin")

# contexto da execução atual (lido pelos workflows disparados dentro do agente)
CURRENT_USER: ContextVar[User] = ContextVar("current_user", default=LOCAL_ADMIN)
CURRENT_CONFIRM: ContextVar = ContextVar("current_confirm", default=None)


def _file(): return config.data_dir() / "users.yaml"


def _load() -> dict:
    return yaml.safe_load(_file().read_text(encoding="utf-8")) or {} if _file().exists() else {}


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def auth_enabled() -> bool:
    return bool(_load())


def add_user(name: str, password: str, role: str = "operator", tools=None, workflows=None):
    if role not in ROLES:
        raise ValueError(f"role deve ser um de {ROLES}")
    if len(password) < 8:
        raise ValueError("senha muito curta (mínimo 8)")
    users = _load()
    salt = secrets.token_bytes(16)
    users[name] = {"role": role, "salt": salt.hex(), "hash": _hash(password, salt),
                   "tools": tools, "workflows": workflows}
    _file().write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")


def remove_user(name: str):
    users = _load()
    if users.pop(name, None) is None:
        raise KeyError(name)
    if not any(u["role"] == "admin" for u in users.values()):
        # sem admin (ou sem ninguém) o login seria desligado silenciosamente
        raise ValueError("Não é possível remover o último admin")
    _file().write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")


def list_users() -> list[dict]:
    return [{"name": n, "role": u["role"], "tools": u.get("tools"), "workflows": u.get("workflows")}
            for n, u in _load().items()]


def _user(name: str, u: dict) -> User:
    return User(name, u["role"], u.get("tools"), u.get("workflows"))


def verify(name: str, password: str) -> User | None:
    u = _load().get(name)
    if not u:
        _hash(password, b"x" * 16)  # tempo constante aproximado
        return None
    ok = hmac.compare_digest(_hash(password, bytes.fromhex(u["salt"])), u["hash"])
    return _user(name, u) if ok else None


_SESSIONS: dict[str, tuple[str, float]] = {}
SESSION_TTL = 12 * 3600


def login(name: str, password: str) -> str | None:
    if not verify(name, password):
        return None
    tok = secrets.token_urlsafe(32)
    _SESSIONS[tok] = (name, time.time() + SESSION_TTL)
    return tok


def logout(token: str): _SESSIONS.pop(token, None)


def user_from_token(token: str | None) -> User | None:
    """Sem usuários cadastrados => admin local. Com usuários => exige token válido."""
    if not auth_enabled():
        return LOCAL_ADMIN
    sess = _SESSIONS.get(token or "")
    if not sess or sess[1] < time.time():
        return None
    u = _load().get(sess[0])
    return _user(sess[0], u) if u else None


# ---------- auditoria ----------
def _redact(v):
    if isinstance(v, dict):
        return {k: ("***" if _SECRET_KEY.search(k) else _redact(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact(x) for x in v]
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    return v


def audit(user: str, action: str, **data):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user, "action": action,
           **_redact(data)}
    with open(config.data_dir() / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def read_audit(limit: int = 200) -> list[dict]:
    f = config.data_dir() / "audit.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()[-limit:]][::-1]


def get_user(name: str) -> User | None:
    if not auth_enabled():
        return LOCAL_ADMIN
    u = _load().get(name)
    return _user(name, u) if u else None
