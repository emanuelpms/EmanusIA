"""Loop do agente: LLM decide -> executa ferramentas -> observa -> repete.
Cada tarefa concluída vira experiência (learning.py) que ajuda nas próximas e gera sugestões de bots.

Pensado para um modelo local pequeno (7B, contexto de 8k tokens):
- só as ferramentas do assunto, em formato compacto e dentro de um orçamento de tokens;
- histórico e resultados longos são encurtados para a conversa caber no contexto;
- respostas "tortas" do modelo (JSON quebrado, chamada escrita como texto) são toleradas."""
import json
import re
from typing import Callable

from . import auth, config, learning, llm as llm_mod
from .tools import load_all, registry

SYSTEM = """Você é o EmanusIA, um funcionário-robô que opera computadores para empresas: sites, planilhas
Excel, dashboards, e-mail (Outlook), SAP e outros programas. Responda sempre em português.
Regras:
- Se o usuário pedir para CRIAR um robô/bot/automação que rode depois, use create_bot com um nome curto
  e uma descrição detalhada do passo a passo. Não execute a tarefa em si.
- Se ele pedir para FAZER algo agora, use as ferramentas; não invente resultados. Se já existir um bot
  para isso (list_bots), prefira run_bot.
- Para analisar uma planilha desconhecida, comece por data_profile.
- Para entrar em sistemas use web_login/sap_login com o NOME do acesso (list_credentials). Nunca peça,
  digite ou mostre senhas.
- Conversa simples (oi, dúvidas) responda direto, sem ferramentas.
Ao terminar, responda com um resumo curto e os arquivos gerados."""

CHARS_PER_TOKEN = 3.2          # estimativa conservadora para português + JSON
TOOL_BUDGET = 0.38             # fração do contexto reservada para as ferramentas
HISTORY_KEEP = 6               # mensagens anteriores do chat que vão junto
RESULT_LIMIT = 2500            # tamanho máximo de um resultado de ferramenta enviado ao modelo


def _tokens(obj) -> int:
    return int(len(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN)


def _shorten(obj, limit=RESULT_LIMIT) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"... [cortado: +{len(s) - limit} caracteres]"


def _hints(task: str) -> str:
    out = []
    for h in learning.similar(task, k=2):
        steps = "; ".join(f"{s['tool']}({_shorten(s['args'], 120)})" for s in h["steps"][:6])
        out.append(f"- \"{h['task'][:100]}\": {steps}")
    return ("\n\nExperiência anterior (tarefas parecidas que deram certo; adapte os valores):\n"
            + "\n".join(out)) if out else ""


_CALL_IN_TEXT = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>|```(?:json)?\s*(\{.*?\})\s*```|^\s*(\{.*\})\s*$", re.S)


def parse_calls(msg: dict) -> list[tuple[str, dict | None]]:
    """[(nome, args)] das chamadas de ferramenta. args=None quando o JSON veio quebrado.
    Aceita também a chamada escrita como texto (acontece com modelos pequenos)."""
    out = []
    for c in msg.get("tool_calls") or []:
        fn = (c or {}).get("function") or {}
        name, args = fn.get("name"), fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = None
        out.append((name, args if isinstance(args, dict) or args is None else None))
    if out:
        return out
    for m in _CALL_IN_TEXT.finditer(msg.get("content") or ""):
        try:
            data = json.loads(next(g for g in m.groups() if g))
        except (ValueError, StopIteration):
            continue
        name = data.get("name") if isinstance(data, dict) else None
        args = data.get("arguments", data.get("parameters", {})) if isinstance(data, dict) else None
        if isinstance(name, str) and name in {t.name for t in registry.all_tools()}:
            out.append((name, args if isinstance(args, dict) else {}))
    return out


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
        tokens = (auth.CURRENT_USER.set(self.user), auth.CURRENT_CONFIRM.set(self.confirm),
                  llm_mod.CURRENT.set(self.llm))
        try:
            return self._run(task, history)
        finally:
            auth.CURRENT_USER.reset(tokens[0])
            auth.CURRENT_CONFIRM.reset(tokens[1])
            llm_mod.CURRENT.reset(tokens[2])

    def tools_for(self, task: str) -> list[dict]:
        """Ferramentas do assunto, compactas, dentro do orçamento de tokens do contexto."""
        groups = registry.groups_for(task)
        budget = int(config.NUM_CTX * TOOL_BUDGET)

        def pick(gs):
            return [t.schema(compact=True) for t in registry.all_tools()
                    if t.group in gs and t.name not in registry.AGENT_HIDDEN and self.user.may_use_tool(t.name)]
        schemas = pick(groups)
        for g in registry.DROP_ORDER:
            if _tokens(schemas) <= budget:
                break
            if g in groups and g not in ("botops",):
                groups = groups - {g}
                schemas = pick(groups)
        return schemas

    def _fit(self, messages: list[dict], schemas: list[dict], task_idx: int):
        """Encurta o que for antigo/grande até a conversa caber no contexto (do mais antigo ao mais novo).
        Nunca mexe nas instruções nem no pedido atual."""
        limit = config.NUM_CTX - _tokens(schemas) - 800  # folga para a resposta

        def small(v, n):
            if isinstance(v, str):
                return v if len(v) <= n else v[:n] + "…"
            if isinstance(v, dict):
                return {k: small(x, n) for k, x in v.items()}
            if isinstance(v, list):
                return [small(x, n) for x in v[:20]]
            return v
        for n in (600, 200, 60):
            if _tokens(messages) <= limit:
                return
            for i, m in enumerate(messages):
                if i in (0, task_idx):
                    continue
                if len(m.get("content") or "") > n:
                    m["content"] = m["content"][:n] + " …[encurtado]"
                for tc in m.get("tool_calls") or []:
                    tc["function"]["arguments"] = small(tc["function"].get("arguments") or {}, n)
        while _tokens(messages) > limit and task_idx > 1:  # último recurso: esquece o histórico antigo
            messages.pop(1)
            task_idx -= 1

    def _run(self, task: str, history: list[dict] | None) -> str:
        auth.audit(self.user.name, "agent_task", task=task)
        hist = [{"role": h.get("role"), "content": _shorten(str(h.get("content", "")), 1500)}
                for h in (history or [])[-HISTORY_KEEP:] if h.get("role") in ("user", "assistant")]
        messages = [{"role": "system", "content": SYSTEM + _hints(task)}, *hist,
                    {"role": "user", "content": task}]
        schemas = self.tools_for(task)
        task_idx = len(messages) - 1
        calls: list[dict] = []
        for _ in range(config.MAX_STEPS):
            self._fit(messages, schemas, task_idx)
            task_idx = next(i for i in range(len(messages) - 1, -1, -1)
                            if messages[i]["role"] == "user" and messages[i]["content"] == task)
            msg = self.llm.chat(messages, schemas)
            batch = parse_calls(msg)
            # o que volta ao Ollama precisa estar no formato dele: argumentos sempre como objeto
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             **({"tool_calls": [{"function": {"name": n or "", "arguments": a or {}}}
                                                for n, a in batch]} if msg.get("tool_calls") else {})})
            if not batch:
                ok = bool(calls) and calls[-1]["ok"]
                learning.record_task(self.user.name, task, calls, ok)
                answer = (msg.get("content") or "").strip()
                return answer or ("Pronto." if calls else "Não entendi bem. Pode explicar de outro jeito?")
            for name, args in batch:
                if args is None:
                    result = {"error": "os argumentos vieram em JSON inválido; chame de novo com JSON válido"}
                    args = {}
                else:
                    self.on_event("tool_call", {"name": name, "args": args})
                    result = self._execute(name, args)
                    self.on_event("tool_result", {"name": name, "result": result})
                calls.append({"name": name, "args": args,
                              "ok": not (isinstance(result, dict) and "error" in result)})
                messages.append({"role": "tool", "name": name, "tool_name": name, "content": _shorten(result)})
        learning.record_task(self.user.name, task, calls, False)
        return "Parei: o limite de passos acabou antes de concluir a tarefa. Tente dividir o pedido em partes menores."

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
        except llm_mod.LLMError:
            raise  # problema com a IA: não adianta o modelo "tentar de novo"
        except Exception as e:  # o LLM precisa ver o erro para se corrigir
            auth.audit(self.user.name, "tool", tool=name, args=args, ok=False, error=str(e))
            return {"error": f"{type(e).__name__}: {e}"}
