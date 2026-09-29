"""Workflows: automações salvas em workflows/*.yaml (sub-rotinas reutilizáveis pelos bots).
A linguagem está documentada em emanusia/engine.py."""
import re

import yaml

from .. import auth, config
from ..engine import Runner, WorkflowError, render, validate  # noqa: F401 (reexport)
from .registry import tool


def _path(name: str):
    if not re.fullmatch(r"[\w\-]+", name or ""):
        raise ValueError("Nome inválido (use letras, números, - e _)")
    return config.workflows_dir() / f"{name}.yaml"


def load_workflow(name: str) -> dict:
    p = _path(name)
    if not p.exists():
        raise FileNotFoundError(f"workflow '{name}' não existe")
    wf = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    wf.setdefault("name", name)
    return wf


def run_steps(wf: dict, params: dict | None = None, confirm=None, user=None, bot=None,
              emit=None) -> dict:
    """Valida e executa. Ações de risco exigem `confirm` (sem ele, são negadas)."""
    user = user or auth.CURRENT_USER.get()
    confirm = confirm or auth.CURRENT_CONFIRM.get()
    if wf.get("name") and not user.may_run_workflow(wf["name"]):
        raise PermissionError(f"Usuário '{user.name}' sem permissão no workflow '{wf['name']}'")
    rep = validate(wf, user)
    if not rep["ok"]:
        raise WorkflowError("Validação falhou: " + "; ".join(rep["errors"][:5]))
    auth.audit(user.name, "workflow_start", workflow=wf.get("name"), bot=bot, params=params)
    return Runner(user, confirm, bot, emit).run(wf, params)


@tool("save_workflow", "Salva uma automação reutilizável. steps=[{tool,args,save_as?}] "
      "(aceita if/for_each/try/set/call).",
      {"name": {"type": "string"}, "steps": {"type": "array"}, "params": {"type": "object"},
       "description": {"type": "string"}}, ("name", "steps"))
def save_workflow(name: str, steps: list, params: dict | None = None, description: str = ""):
    wf = {"name": name, "description": description, "params": params or {}, "steps": steps}
    rep = validate(wf, auth.CURRENT_USER.get())
    if not rep["ok"]:
        return {"saved": False, "errors": rep["errors"]}
    _path(name).write_text(yaml.safe_dump(wf, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"saved": name, "warnings": rep["warnings"]}


@tool("list_workflows", "Lista as automações salvas.")
def list_workflows():
    return [p.stem for p in sorted(config.workflows_dir().glob("*.yaml"))]


@tool("run_workflow", "Executa uma automação salva, com parâmetros opcionais.",
      {"name": {"type": "string"}, "params": {"type": "object"}}, ("name",), risky=True)
def run_workflow(name: str, params: dict | None = None):
    return run_steps(load_workflow(name), params)
