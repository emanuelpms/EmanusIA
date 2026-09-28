"""Workflows: automações salvas em YAML que o usuário pode criar/editar/rodar sem LLM.

  name: relatorio-vendas
  params: {url: https://...}
  steps:
    - {tool: web_open, args: {url: "{{url}}"}}
    - {tool: web_table, args: {selector: table}, save_as: tabela}
    - {tool: excel_write, args: {path: vendas.xlsx, sheets: {Vendas: "{{tabela}}"}}}
"""
import re

import yaml

from .. import config
from . import registry
from .registry import tool

_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def render(value, ctx: dict):
    """Substitui {{var}}; se a string for só um placeholder, preserva o tipo original."""
    if isinstance(value, str):
        m = _VAR.fullmatch(value.strip())
        if m:
            return _lookup(ctx, m.group(1))
        return _VAR.sub(lambda m: str(_lookup(ctx, m.group(1))), value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def run_steps(wf: dict, params: dict | None = None, confirm=None) -> dict:
    ctx = {**wf.get("params", {}), **(params or {})}
    log = []
    for i, step in enumerate(wf["steps"], 1):
        t = registry.get(step["tool"])
        if t.risky and confirm is not None and not confirm(t.name, step.get("args", {})):
            raise PermissionError(f"Passo {i} ({t.name}) não autorizado")
        result = t.fn(**render(step.get("args", {}), ctx))
        if step.get("save_as"):
            ctx[step["save_as"]] = result
        log.append({"step": i, "tool": t.name, "ok": True})
    return {"steps": log, "vars": [k for k in ctx if k not in wf.get("params", {})]}


def _path(name: str):
    if not re.fullmatch(r"[\w\-]+", name):
        raise ValueError("Nome de workflow inválido (use letras, números, - e _)")
    return config.workflows_dir() / f"{name}.yaml"


@tool("save_workflow", "Salva uma automação reutilizável. steps=[{tool,args,save_as?}].",
      {"name": {"type": "string"}, "steps": {"type": "array"}, "params": {"type": "object"},
       "description": {"type": "string"}}, ("name", "steps"))
def save_workflow(name: str, steps: list, params: dict | None = None, description: str = ""):
    for s in steps:
        registry.get(s["tool"])  # valida nomes de ferramentas
    _path(name).write_text(yaml.safe_dump(
        {"name": name, "description": description, "params": params or {}, "steps": steps},
        allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"saved": name}


@tool("list_workflows", "Lista as automações salvas.")
def list_workflows():
    return [p.stem for p in sorted(config.workflows_dir().glob("*.yaml"))]


@tool("run_workflow", "Executa uma automação salva, com parâmetros opcionais.",
      {"name": {"type": "string"}, "params": {"type": "object"}}, ("name",), risky=True)
def run_workflow(name: str, params: dict | None = None):
    wf = yaml.safe_load(_path(name).read_text(encoding="utf-8"))
    return run_steps(wf, params)
