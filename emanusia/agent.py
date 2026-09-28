"""Loop do agente: LLM decide -> executa ferramentas -> observa -> repete."""
import json
from typing import Callable

from . import auth, config
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
                 on_event: Callable[[str, dict], None] | None = None,
                 user: auth.User | None = None):
        load_all()
        self.llm = llm
        self.user = user or auth.LOCAL_ADMIN
        # sem callback de confirmação => ações de risco são bloqueadas (seguro por padrão)
        self.confirm = confirm or (lambda name, args: False)
        self.on_event = on_event or (lambda kind, data: None)

    def run(self, task: str, history: list[dict] | None = None) -> str:
        # contexto restaurado ao final: uma tarefa nunca herda usuário/aprovação da anterior
        t_user, t_conf = auth.CURRENT_USER.set(self.user), auth.CURRENT_CONFIRM.set(self.confirm)
        try:
            return self._run(task, history)
        finally:
            auth.CURRENT_USER.reset(t_user)
            auth.CURRENT_CONFIRM.reset(t_conf)

    def _run(self, task: str, history: list[dict] | None) -> str:
        auth.audit(self.user.name, "agent_task", task=task)
        messages = [{"role": "system", "content": SYSTEM}, *(history or []),
                    {"role": "user", "content": task}]
        schemas = [t.schema() for t in registry.all_tools() if self.user.may_use_tool(t.name)]
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
            if not self.user.may_use_tool(name):
                raise PermissionError(f"Usuário '{self.user.name}' sem permissão para {name}")
            if name == "run_workflow" and not self.user.may_run_workflow(args.get("name", "")):
                raise PermissionError(f"Usuário '{self.user.name}' sem permissão neste workflow")
            if t.is_risky(args) and not (self.user.can_approve and self.confirm(name, args)):
                auth.audit(self.user.name, "tool_denied", tool=name, args=args)
                return {"error": "Ação de risco não autorizada pelo usuário."}
            result = registry.call(name, args)
            auth.audit(self.user.name, "tool", tool=name, args=args, ok=True)
            return result
        except Exception as e:  # o LLM precisa ver o erro para se corrigir
            auth.audit(self.user.name, "tool", tool=name, args=args, ok=False, error=str(e))
            return {"error": f"{type(e).__name__}: {e}"}
