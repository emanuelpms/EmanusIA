"""Registro de ferramentas: usado tanto pelo agente (LLM) quanto pelos workflows (YAML)."""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

_TOOLS: dict[str, "Tool"] = {}

# Grupos sempre oferecidos ao LLM + palavras que ativam os demais. Um modelo 7B com 8k de
# contexto erra muito mais quando recebe 60 ferramentas; aqui ele recebe só as do assunto.
CORE_GROUPS = {"files", "excel", "dashboard", "workflow", "botops"}
GROUP_KEYWORDS = {
    "web": r"site|portal|pagina|web|url|http|navegador|login|link|formulario|baixar|download|www|\.com",
    "sap": r"sap|transacao|va01|mm0|fb\d|me2|vf0|alv",
    "powerbi": r"power ?bi|pbi|dataset",
    "mail": r"e-?mail|outlook|anexo|caixa de entrada|enviar para",
    "connectors": r"api|rest|sql|banco|query|consulta|endpoint|erp|crm",
    "desktop": r"programa|aplicativo|janela|desktop|\.exe|clicar na tela|sistema legado",
    "vision": r"tela|print|screenshot|imagem|enxergar|olhar",
    "data": r"planilha|excel|xlsx|csv|dados|analis|dashboard|painel|relatorio|tabela|pivot|dinamica|procv|"
            r"cruz|consolid|pdf|indicador|kpi|grafico|soma|media|total|filtr|agrup|limp|duplicad|extrair",
    "access": r"login|logar|entrar|senha|acesso|credencia|autentic|logon",
}
# Ferramentas avançadas: continuam disponíveis nos robôs (YAML), mas não são oferecidas ao chat,
# para não encher o contexto do modelo com opções que ele raramente usa bem.
AGENT_HIDDEN = {"web_eval", "web_hover", "web_scroll", "web_type", "web_check", "web_count", "web_attr",
                "web_back", "web_tabs", "web_switch_tab", "web_frame", "web_on_dialog", "web_upload",
                "web_save_session", "web_fill_secret", "web_exists", "desktop_click_xy", "desktop_hotkey",
                "sap_call", "sap_select", "vision_locate", "http_send", "sql_execute", "powerbi_push_rows",
                "run_workflow", "save_workflow", "list_workflows", "excel_read"}
# Se a ferramenta não couber no orçamento, sai primeiro quem está no começo desta lista.
DROP_ORDER = ["vision", "powerbi", "connectors", "desktop", "sap", "access", "mail", "dashboard", "data",
              "web", "excel", "files", "workflow"]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema das propriedades
    fn: Callable[..., Any]
    risky: bool | Callable[[dict], bool] = False  # bool, ou função(args)->bool p/ risco dependente dos argumentos
    required: tuple = ()
    group: str = ""

    def is_risky(self, args: dict | None = None) -> bool:
        return bool(self.risky(args or {})) if callable(self.risky) else bool(self.risky)

    def schema(self, compact: bool = False) -> dict:
        desc = self.description
        if compact:  # 1ª frase, curta: economiza contexto do modelo local
            desc = desc.split(". ")[0].rstrip(".")[:160]
        return {"type": "function", "function": {
            "name": self.name, "description": desc,
            "parameters": {"type": "object", "properties": self.parameters,
                           "required": list(self.required)}}}


def tool(name: str, description: str, parameters: dict | None = None,
         required: tuple = (), risky: bool = False):
    def deco(fn):
        group = fn.__module__.rsplit(".", 1)[-1]
        _TOOLS[name] = Tool(name, description, parameters or {}, fn, risky, required, group)
        return fn
    return deco


def get(name: str) -> Tool:
    if name not in _TOOLS:
        raise KeyError(f"Ferramenta desconhecida: {name}")
    return _TOOLS[name]


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())


def groups_for(text: str) -> set[str]:
    """Grupos relevantes para a tarefa: os básicos + os que o texto menciona.
    (Nunca "todos": 80+ ferramentas não cabem no contexto de um modelo 7B.)"""
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return CORE_GROUPS | {g for g, rx in GROUP_KEYWORDS.items() if re.search(rx, t)}


def call(name: str, args: dict | None = None) -> Any:
    return get(name).fn(**(args or {}))
