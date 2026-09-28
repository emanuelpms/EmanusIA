"""Registro de ferramentas: usado tanto pelo agente (LLM) quanto pelos workflows (YAML)."""
from dataclasses import dataclass
from typing import Any, Callable

_TOOLS: dict[str, "Tool"] = {}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema das propriedades
    fn: Callable[..., Any]
    risky: bool = False  # ações que alteram sistemas externos pedem confirmação
    required: tuple = ()

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.parameters,
                           "required": list(self.required)}}}


def tool(name: str, description: str, parameters: dict | None = None,
         required: tuple = (), risky: bool = False):
    def deco(fn):
        _TOOLS[name] = Tool(name, description, parameters or {}, fn, risky, required)
        return fn
    return deco


def get(name: str) -> Tool:
    if name not in _TOOLS:
        raise KeyError(f"Ferramenta desconhecida: {name}")
    return _TOOLS[name]


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())


def call(name: str, args: dict | None = None) -> Any:
    return get(name).fn(**(args or {}))
