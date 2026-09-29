"""Loop do agente: LLM decide -> executa ferramentas -> observa -> repete.
Cada tarefa concluída vira experiência (learning.py) que ajuda nas próximas e gera sugestões de bots."""
import json
from typing import Callable

from . import auth, config, learning
from .tools import load_all, registry

SYSTEM = """Você é o EmanusIA, um funcionário-robô que opera computadores para empresas.
Você automatiza sites (navegador), analisa dados, gera planilhas Excel, dashboards (Excel e HTML),
datasets Power BI, envia e-mails (Outlook) e opera o SAP e outros programas. Use SEMPRE as
ferramentas para agir; não invente resultados. Para analisar uma planilha desconhecida, comece por
data_profile. Para entrar em sistemas use web_login/sap_login com o NOME do acesso (list_credentials):
nunca peça, digite ou exiba senhas. Trabalhe em passos curtos, confira cada resultado e ao terminar responda em português
com um resumo objetivo e os caminhos dos arquivos gerados. Se já existir um bot para a tarefa
(list_bots), prefira executá-lo com run_bot."""


def _shorten(obj, limit=4000) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"... [truncado {len(s) - limit} chars]"


def _hints(task: str) -> str:
    out = []
    for h in learning.similar(task, k=2):
        steps = "; ".join(f"{s['tool']}({_shorten(s['args'], 160)})" for s in h["steps"][:8])
        out.append(f"- \"{h['task'][:120]}\": {steps}")
    return ("\n\nExperiência anterior (tarefas parecidas que deram certo; adapte os valores):\n"
            + "\n".join(out)) if out else ""


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

    def tools_for(self, task: str) -> list[dict]:
        groups = registry.groups_for(task)
        return [t.schema() for t in registry.all_tools()
                if t.group in groups and self.user.may_use_tool(t.name)]

    def _run(self, task: str, history: list[dict] | None) -> str:
        auth.audit(self.user.name, "agent_task", task=task)
        messages = [{"role": "system", "content": SYSTEM + _hints(task)}, *(history or []),
                    {"role": "user", "content": task}]
        schemas = self.tools_for(task)
        calls: list[dict] = []
        for _ in range(config.MAX_STEPS):
            msg = self.llm.chat(messages, schemas)
            messages.append(msg)
            batch = msg.get("tool_calls") or []
            if not batch:
                ok = bool(calls) and calls[-1]["ok"]
                learning.record_task(self.user.name, task, calls, ok)
                return msg.get("content", "")
            for c in batch:
                fn = c["function"]
                name, args = fn["name"], fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                self.on_event("tool_call", {"name": name, "args": args})
                result = self._execute(name, args)
                self.on_event("tool_result", {"name": name, "result": result})
                calls.append({"name": name, "args": args,
                              "ok": not (isinstance(result, dict) and "error" in result)})
                messages.append({"role": "tool", "name": name, "content": _shorten(result)})
        learning.record_task(self.user.name, task, calls, False)
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
