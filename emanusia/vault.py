"""Cofre de acessos: cada usuário mapeia o SEU login/senha para cada sistema (site, SAP, programa).

- Bots referenciam o acesso só pelo nome (ex.: `credential: portal-fornecedor`); na execução, o
  sistema usa o acesso de QUEM está rodando (agendamentos: o dono do agendamento). Assim o mesmo
  bot serve para a equipe inteira, cada um com seu usuário.
- Acessos "compartilhados" (conta de serviço) só podem ser criados por admin e valem para todos
  que não tiverem um acesso próprio com o mesmo nome.
- Senhas criptografadas (Fernet/AES). No Windows a chave é protegida pelo DPAPI (só o usuário do
  Windows que rodou o EmanusIA consegue abri-la). Senha nunca volta pela API, nunca vai para o código do robô,
  para o YAML, para eventos ou para a auditoria."""
import base64
import json
import os
import re
import sys
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

from . import config

SHARED = "*"
KINDS = ("web", "sap", "desktop", "api", "other")
_lock = threading.Lock()
_fernet: Fernet | None = None
_key_for: str | None = None


def _key() -> Fernet:
    global _fernet, _key_for
    kf = config.data_dir() / "vault.key"
    if _fernet is not None and _key_for == str(kf):
        return _fernet
    env = os.environ.get("EMANUSIA_MASTER_KEY")
    if env:
        key = env.encode()
    elif kf.exists():
        key = _unprotect(kf.read_bytes())
    else:
        key = Fernet.generate_key()
        kf.write_bytes(_protect(key))
        try:
            os.chmod(kf, 0o600)
        except OSError:
            pass
    _fernet, _key_for = Fernet(key), str(kf)
    return _fernet


def _protect(key: bytes) -> bytes:
    if sys.platform == "win32":
        try:
            import win32crypt
            return b"DPAPI:" + base64.b64encode(win32crypt.CryptProtectData(key, "EmanusIA", None, None, None, 0))
        except ImportError:
            pass
    return key


def _unprotect(blob: bytes) -> bytes:
    if blob.startswith(b"DPAPI:"):
        import win32crypt
        return win32crypt.CryptUnprotectData(base64.b64decode(blob[6:]), None, None, None, 0)[1]
    return blob.strip()


def _file():
    return config.data_dir() / "vault.json"


def _load() -> dict:
    return json.loads(_file().read_text(encoding="utf-8")) if _file().exists() else {}


def _save(d: dict):
    _file().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(_file(), 0o600)
    except OSError:
        pass


def check_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_.]{0,59}", name or ""):
        raise ValueError("nome do acesso: minúsculas, números, - _ . (ex.: portal-fornecedor)")
    return name


def set_credential(owner: str, name: str, username: str = "", password: str | None = None,
                   label: str = "", kind: str = "web", url: str = "", extra: dict | None = None) -> dict:
    """password=None mantém a senha atual (para editar só o usuário/URL sem redigitar)."""
    check_name(name)
    if kind not in KINDS:
        raise ValueError(f"tipo deve ser um de {KINDS}")
    extra = {str(k): str(v) for k, v in (extra or {}).items() if v not in (None, "")}
    with _lock:
        d = _load()
        cur = d.setdefault(owner, {}).get(name, {})
        if password is None and not cur.get("password"):
            raise ValueError("informe a senha")
        enc = cur.get("password") if password is None else _key().encrypt(password.encode()).decode()
        d[owner][name] = {"label": label or name, "kind": kind, "url": url, "username": username,
                          "password": enc, "extra": extra, "updated": time.strftime("%Y-%m-%d %H:%M")}
        _save(d)
    return public(owner, name, d[owner][name])


def delete_credential(owner: str, name: str):
    with _lock:
        d = _load()
        if name not in d.get(owner, {}):
            raise KeyError(name)
        del d[owner][name]
        _save(d)


def public(owner: str, name: str, c: dict) -> dict:
    return {"name": name, "label": c["label"], "kind": c["kind"], "url": c["url"],
            "username": c["username"], "extra": c.get("extra", {}), "updated": c.get("updated"),
            "shared": owner == SHARED, "has_password": bool(c.get("password"))}


def list_credentials(user: str) -> list[dict]:
    """Acessos visíveis ao usuário: os próprios + os compartilhados (sem senha)."""
    d = _load()
    own = {n: public(user, n, c) for n, c in d.get(user, {}).items()}
    shared = {n: public(SHARED, n, c) for n, c in d.get(SHARED, {}).items() if n not in own}
    return sorted([*own.values(), *shared.values()], key=lambda c: c["name"])


def exists(user: str, name: str) -> bool:
    d = _load()
    return name in d.get(user, {}) or name in d.get(SHARED, {})


def resolve(user: str, name: str) -> dict:
    """USO INTERNO das ferramentas: devolve o acesso com a senha em claro."""
    d = _load()
    c = d.get(user, {}).get(name) or d.get(SHARED, {}).get(name)
    if c is None:
        raise KeyError(f"Acesso '{name}' não mapeado para o usuário '{user}'. Cadastre em 🔑 Acessos.")
    try:
        pwd = _key().decrypt(c["password"].encode()).decode()
    except InvalidToken:
        raise PermissionError("Não foi possível abrir o cofre (chave diferente da que criptografou).")
    return {**c, "password": pwd, "name": name}
