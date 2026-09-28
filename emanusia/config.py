"""Configuração via variáveis de ambiente."""
import os
from pathlib import Path

OLLAMA_URL = os.environ.get("EMANUSIA_OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("EMANUSIA_MODEL", "qwen2.5:14b")
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
