"""Loop do agente: LLM decide -> executa ferramentas -> observa -> repete."""
import json
from typing import Callable

from . import config
from .tools import load_all, registry

SYSTEM = """Você é o EmanusIA, um funcionário-robô que opera computadores para empresas.
Você automatiza sites (navegador), gera planilhas Excel, datasets Power BI, dashboards HTML e
opera o SAP via SAP GUI Scripting. Use SEMPRE as ferramentas para agir; não invente resultados.
Regras: trabalhe em passos curtos, confira o resultado de cada ferramenta, nunca peça/exiba
senhas (elas vêm de variáveis de ambiente), e ao terminar responda em português com um resumo
objetivo e os caminhos dos arquivos gerados. Se um usuário quiser repetir uma tarefa, salve-a
com save_workflow."""


def _shorten(obj, limit=4000) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"... [truncado {len(s) - limit} chars]"


class Agent:
    def __init__(self, llm, confirm: Callable[[str, dict], bool] | None = None,
                 on_event: Callable[[str, dict], None] | None = None):
        load_all()
        self.llm = llm
        # sem callback de confirmação => ações de risco são bloqueadas (seguro por padrão)
        self.confirm = confirm or (lambda name, args: False)
        self.on_event = on_event or (lambda kind, data: None)

    def run(self, task: str, history: list[dict] | None = None) -> str:
        messages = [{"role": "system", "content": SYSTEM}, *(history or []),
                    {"role": "user", "content": task}]
        schemas = [t.schema() for t in registry.all_tools()]
        for _ in range(config.MAX_STEPS):
            msg = self.llm.chat(messages, schemas)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content", "")
            for c in calls:
                fn = c["function"]
                name, args = fn["name"], fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                self.on_event("tool_call", {"name": name, "args": args})
                result = self._execute(name, args)
                self.on_event("tool_result", {"name": name, "result": result})
                messages.append({"role": "tool", "name": name, "content": _shorten(result)})
        return "Parei: limite de passos atingido sem concluir a tarefa."

    def _execute(self, name: str, args: dict):
        try:
            t = registry.get(name)
            if t.risky and not self.confirm(name, args):
                return {"error": "Ação de risco não autorizada pelo usuário."}
            return registry.call(name, args)
        except Exception as e:  # o LLM precisa ver o erro para se corrigir
            return {"error": f"{type(e).__name__}: {e}"}
