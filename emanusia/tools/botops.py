"""Ferramentas para o agente conversar com os bots da empresa."""
from .. import auth, bots
from .registry import tool


@tool("list_bots", "Lista os bots (funcionários-robô) da empresa: id, nome, time, status.")
def list_bots():
    return [{"id": b.id, "name": b.name, "team": b.team, "role": b.role, "status": b.status,
             "description": b.description} for b in bots.list_bots(auth.CURRENT_USER.get())]


@tool("create_bot", "Cria um robô novo a partir da descrição do passo a passo que ele deve fazer. Use quando "
      "pedirem para criar/automatizar uma rotina. O robô fica em rascunho para a pessoa conferir e ensaiar.",
      {"name": {"type": "string", "description": "nome curto do robô"},
       "description": {"type": "string", "description": "o que ele faz, passo a passo, com sites/arquivos/e-mails"},
       "team": {"type": "string"}}, ("name", "description"))
def create_bot(name: str, description: str, team: str = "Geral"):
    from .. import plain
    from ..botgen import generate
    from ..llm import CURRENT, OllamaClient
    user = auth.CURRENT_USER.get()
    if not user.can_run:
        raise PermissionError("perfil somente leitura não cria robôs")
    if len(description.strip()) < 10:
        raise ValueError("descreva melhor o que o robô deve fazer (passo a passo)")
    res = generate(description, CURRENT.get() or OllamaClient(), user)
    code = res["code"] or {"params": {}, "steps": []}
    base, n = (name.strip() or "Robô novo")[:50], 1
    while True:  # nome repetido: "Robô (2)"
        try:
            b = bots.create_bot(base if n == 1 else f"{base} ({n})", user, code, team or "Geral",
                                description=description[:500], origin="ia")
            break
        except ValueError as e:
            if "já existe" not in str(e) or n > 20:
                raise
            n += 1
    rep = bots.validate_bot(b.id, user)
    return {"created": b.id, "name": b.name, "ok": rep["ok"],
            "steps": [s["text"] for s in plain.describe_code(code)],
            "problems": plain.explain_validation(rep, code),
            "next": "O robô está em rascunho. Abra a aba Bots para conferir os passos, fazer o ensaio e pedir revisão."}


@tool("run_bot", "Executa um bot validado pelo id, com parâmetros opcionais.",
      {"bot_id": {"type": "string"}, "params": {"type": "object"}}, ("bot_id",), risky=True)
def run_bot(bot_id: str, params: dict | None = None):
    from ..engine import Runner
    user = auth.CURRENT_USER.get()
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(f"bot '{bot_id}' não existe")
    bots.ensure_runnable(b, user)
    try:
        res = Runner(user, auth.CURRENT_CONFIRM.get(), b.id).run(b.code, params)
    except Exception:
        bots.record_run(b.id, False)
        raise
    bots.record_run(b.id, True)
    return res
