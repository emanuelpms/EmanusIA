"""Ferramentas para o agente conversar com os bots da empresa."""
from .. import auth, bots
from .registry import tool


@tool("list_bots", "Lista os bots (funcionários-robô) da empresa: id, nome, time, status.")
def list_bots():
    return [{"id": b.id, "name": b.name, "team": b.team, "role": b.role, "status": b.status,
             "description": b.description} for b in bots.list_bots(auth.CURRENT_USER.get())]


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
