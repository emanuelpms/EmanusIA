"""Gera o código (YAML) de um bot a partir de uma descrição em português, usando o LLM local.
O validador devolve os erros ao modelo, que corrige, até ficar válido ou esgotar as tentativas."""
import re

import yaml

from . import auth, learning
from .engine import validate
from .tools import load_all, registry

PROMPT = """Você escreve o CÓDIGO de bots de automação empresarial em YAML. Responda SOMENTE com o YAML.
Formato:
params: {{nome_param: valor_padrao}}
steps:
  - tool: <ferramenta>
    args: {{...}}
    save_as: variavel          # opcional
Controle: `if: "expr"` + then/else · `for_each: "lista"` + as + steps · `try:` + catch ·
`set: {{v: "=expr"}}` · `call_bot: id` · `log: "texto {{{{var}}}}"` · retry: N · on_error: continue.
Use {{{{variavel}}}} para inserir valores. NUNCA escreva senhas: use web_fill_secret com env_var.
Ferramentas disponíveis (⚠ = pede autorização do usuário ao rodar):
{tools}
{examples}"""

EXAMPLE = """Exemplo — "baixar a tabela de pedidos do portal e mandar por e-mail":
params: {url: "https://portal.exemplo.com/pedidos", destino: "financeiro@empresa.com"}
steps:
  - tool: web_open
    args: {url: "{{url}}"}
  - tool: web_table
    args: {selector: "table"}
    save_as: pedidos
  - if: "len(pedidos) <= 1"
    then:
      - fail: "Nenhum pedido encontrado"
  - tool: excel_write
    args: {path: "pedidos.xlsx", sheets: {Pedidos: "{{pedidos}}"}}
  - tool: email_send
    args: {to: "{{destino}}", subject: "Pedidos do dia", body: "Segue planilha.", attachments: ["pedidos.xlsx"]}
"""


def tool_catalog(user: auth.User) -> str:
    lines = []
    for t in registry.all_tools():
        if not user.may_use_tool(t.name) or t.name in ("save_workflow", "run_bot"):
            continue
        args = ", ".join(a + ("*" if a in t.required else "") for a in t.parameters)
        lines.append(f"- {t.name}({args}){' ⚠' if t.risky else ''}: {t.description[:110]}")
    return "\n".join(lines)


def extract_yaml(text: str) -> str:
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def generate(description: str, llm, user: auth.User, max_attempts: int = 3) -> dict:
    load_all()
    hints = learning.similar(description, k=2)
    examples = EXAMPLE + "".join(
        f"\nJá funcionou antes para \"{h['task']}\":\n"
        f"{yaml.safe_dump({'steps': h['steps']}, allow_unicode=True)}" for h in hints)
    messages = [{"role": "system", "content": PROMPT.format(tools=tool_catalog(user), examples=examples)},
                {"role": "user", "content": f"Crie o bot: {description}"}]
    best = None
    for attempt in range(1, max_attempts + 1):
        text = llm.chat(messages).get("content", "")
        raw = extract_yaml(text)
        try:
            code = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            code, rep = None, {"ok": False, "errors": [f"YAML inválido: {e}"], "warnings": []}
        else:
            if not isinstance(code, dict):
                rep = {"ok": False, "errors": ["a resposta não é um objeto YAML com steps"], "warnings": []}
            else:
                rep = validate(code, user)
                if not code.get("steps"):
                    rep = {**rep, "ok": False, "errors": rep["errors"] + ["nenhum passo em steps"]}
        best = {"yaml": raw, "code": code if isinstance(code, dict) else None, "validation": rep,
                "attempts": attempt}
        if rep["ok"]:
            return best
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "O validador encontrou erros. Corrija e responda só "
                      "com o YAML completo:\n- " + "\n- ".join(rep["errors"][:10])}]
    return best
