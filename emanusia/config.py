"""Configuração via variáveis de ambiente + ajustes salvos pela tela (data/settings.json)."""
import json
import os
from pathlib import Path

HEADLESS = os.environ.get("EMANUSIA_HEADLESS", "0") == "1"


def workspace() -> Path:
    """Pasta de trabalho dos robôs (caminhos relativos começam aqui)."""
    p = Path(os.environ.get("EMANUSIA_WORKSPACE", "workspace")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def workflows_dir() -> Path:
    p = Path(os.environ.get("EMANUSIA_WORKFLOWS", "workflows")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    """Estado da aplicação: usuários, bots, agendamentos, auditoria, ajustes."""
    p = Path(os.environ.get("EMANUSIA_DATA", "data")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def allowed_roots() -> list[Path]:
    """Pastas onde os robôs podem ler/gravar: a pasta de trabalho + as liberadas pelo admin em Ajustes
    (ex.: C:\\Notas Fiscais, \\\\servidor\\financeiro)."""
    roots = [workspace()]
    for f in settings().get("allowed_folders") or []:
        try:
            p = Path(str(f)).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if str(f).strip() and p not in roots:
            roots.append(p)
    return roots


def safe_path(path: str, mkdir: bool = True) -> Path:
    """Resolve um caminho dentro das pastas permitidas; bloqueia o resto (inclusive ../)."""
    raw = Path(str(path)).expanduser()
    p = (raw if raw.is_absolute() else workspace() / raw).resolve()
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise PermissionError(f"Pasta não liberada para os robôs: {path}. O admin libera em Ajustes → Pastas liberadas.")
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def display_path(p: Path) -> str:
    """Caminho para mostrar/guardar: relativo à pasta de trabalho quando possível, sempre com '/'."""
    try:
        return p.resolve().relative_to(workspace()).as_posix()
    except ValueError:
        return str(p)


DEFAULTS = {"email_provider": "outlook", "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_from": "",
            "imap_host": "", "imap_user": "", "company_name": "EmanusIA S.A.", "allowed_folders": []}


def settings() -> dict:
    base = dict(DEFAULTS)
    f = data_dir() / "settings.json"
    if f.exists():
        base.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if k in DEFAULTS})
    return base


def save_settings(**kw) -> dict:
    # só chaves conhecidas; senhas nunca são salvas aqui (ficam no cofre / variáveis de ambiente)
    s = {**settings(), **{k: v for k, v in kw.items() if k in DEFAULTS and v is not None}}
    (data_dir() / "settings.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    return s
