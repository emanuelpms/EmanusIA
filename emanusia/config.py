"""Configuração via variáveis de ambiente."""
import os
from pathlib import Path

OLLAMA_URL = os.environ.get("EMANUSIA_OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("EMANUSIA_MODEL", "qwen2.5:7b")  # cabe em 6 GB de VRAM (RTX 4050)
VISION_MODEL = os.environ.get("EMANUSIA_VISION_MODEL", "qwen2.5vl:3b")
NUM_CTX = int(os.environ.get("EMANUSIA_NUM_CTX", "8192"))
MAX_STEPS = int(os.environ.get("EMANUSIA_MAX_STEPS", "15"))
HEADLESS = os.environ.get("EMANUSIA_HEADLESS", "0") == "1"


def workspace() -> Path:
    """Pasta onde o robô lê/escreve arquivos (nunca sai dela)."""
    p = Path(os.environ.get("EMANUSIA_WORKSPACE", "workspace")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def workflows_dir() -> Path:
    p = Path(os.environ.get("EMANUSIA_WORKFLOWS", "workflows")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_path(rel: str) -> Path:
    """Resolve `rel` dentro do workspace; bloqueia path traversal."""
    root = workspace()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        raise PermissionError(f"Caminho fora do workspace: {rel}")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    """Estado da aplicação: usuários, agendamentos, auditoria, ajustes."""
    p = Path(os.environ.get("EMANUSIA_DATA", "data")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings() -> dict:
    """Ajustes editáveis pela GUI (sobrepõem as variáveis de ambiente)."""
    import json
    base = dict(DEFAULTS)
    f = data_dir() / "settings.json"
    if f.exists():
        base.update(json.loads(f.read_text(encoding="utf-8")))
    return base


DEFAULTS = {"model": MODEL, "vision_model": VISION_MODEL, "email_provider": "outlook",
            "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_from": "",
            "imap_host": "", "imap_user": "", "auto_create_bots": False,
            "suggest_min_repeats": 3, "company_name": "EmanusIA S.A."}


def save_settings(**kw) -> dict:
    import json
    # só chaves conhecidas; senhas nunca são salvas aqui (ficam em variáveis de ambiente)
    s = {**settings(), **{k: v for k, v in kw.items() if k in DEFAULTS and v is not None}}
    (data_dir() / "settings.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s
