import argparse
import json
import sys

from . import config
from .agent import Agent
from .llm import OllamaClient
from .tools import load_all
from .tools.workflow import load_workflow, run_steps


def _confirm(name: str, args: dict) -> bool:
    print(f"\n⚠️  Ação de risco: {name} {json.dumps(args, ensure_ascii=False)[:300]}")
    return input("Autorizar? [s/N] ").strip().lower() in ("s", "y")


def _event(kind: str, data: dict):
    if kind == "tool_call":
        print(f"  → {data['name']}({json.dumps(data['args'], ensure_ascii=False)[:200]})")


def main(argv=None):
    ap = argparse.ArgumentParser("emanusia")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat", help="conversa com o robô no terminal")
    r = sub.add_parser("run", help="executa uma tarefa única em linguagem natural")
    r.add_argument("task")
    w = sub.add_parser("workflow", help="roda uma automação salva (sem LLM)")
    w.add_argument("name")
    w.add_argument("-p", "--param", action="append", default=[], help="chave=valor")
    s = sub.add_parser("serve", help="API + interface web (sem abrir janela)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--lan", action="store_true",
                   help="libera acesso pela rede para a equipe (exige usuários cadastrados)")
    b = sub.add_parser("bot", help="gerencia bots")
    b.add_argument("action", choices=["list", "validate", "run", "activate", "pause"])
    b.add_argument("id", nargs="?")
    b.add_argument("-p", "--param", action="append", default=[], help="chave=valor")
    sub.add_parser("demo", help="cria uma empresa de exemplo com bots para ver o escritório")
    g = sub.add_parser("gui", help="abre a interface gráfica (janela nativa ou navegador)")
    g.add_argument("--port", type=int, default=8765)
    rec = sub.add_parser("record", help="grava uma automação web usando o navegador")
    rec.add_argument("url")
    rec.add_argument("name")
    u = sub.add_parser("user", help="gerencia usuários (ativa login na GUI)")
    u.add_argument("action", choices=["add", "list", "remove"])
    u.add_argument("name", nargs="?")
    u.add_argument("--role", default="operator", choices=["admin", "operator", "viewer"])
    u.add_argument("--tools", help="whitelist de ferramentas, separadas por vírgula")
    u.add_argument("--reviewer", action="store_true", help="padrinho: revisa e libera bots de outras pessoas")
    sub.add_parser("tools", help="lista as ferramentas disponíveis")
    a = ap.parse_args(argv)

    if a.cmd == "tools":
        load_all()
        from .tools import registry
        for t in registry.all_tools():
            print(f"{'⚠️ ' if t.risky else '   '}{t.name:22} {t.description[:80]}")
    elif a.cmd == "gui":
        from .gui import launch
        launch(a.port)
    elif a.cmd == "record":
        import threading
        from .recorder import record
        stop = threading.Event()
        print("Gravando... use o navegador e feche a janela para salvar.")
        record(a.url, a.name, stop)
        print(f"Salvo em workflows/{a.name}.yaml")
    elif a.cmd == "user":
        from . import auth
        import getpass
        if a.action == "list":
            for u_ in auth.list_users():
                print(u_["name"], u_["role"])
        elif a.action == "remove":
            auth.remove_user(a.name)
        else:
            auth.add_user(a.name, getpass.getpass("Senha: "), a.role,
                          a.tools.split(",") if a.tools else None, reviewer=a.reviewer)
            print("Usuário criado. A GUI passa a exigir login.")
    elif a.cmd == "serve":
        import os
        import uvicorn
        from . import auth
        if a.lan:
            if not auth.auth_enabled():
                print("Modo LAN exige login. Crie o admin: emanusia user add admin --role admin")
                return 1
            os.environ["EMANUSIA_LAN"] = "1"
            print(f"Equipe acessa em http://<ip-deste-pc>:{a.port}/ (escritório em /office). "
                  "Libere a porta no Firewall do Windows. Tráfego sem HTTPS: use só em rede interna.")
        uvicorn.run("emanusia.api:app", host="0.0.0.0" if a.lan else "127.0.0.1", port=a.port)
    elif a.cmd == "demo":
        load_all()
        from .demo import seed
        made = seed()
        print(f"{len(made)} bots criados. Rode `emanusia gui` e abra a aba Escritório.")
    elif a.cmd == "bot":
        load_all()
        from . import auth, bots
        user = auth.LOCAL_ADMIN
        if a.action == "list":
            for bt in bots.list_bots():
                print(f"{bt.id:28} {bt.status:10} {bt.team:14} {bt.role:7} {bt.name}")
        elif a.action == "validate":
            rep = bots.validate_bot(a.id, user)
            print("OK" if rep["ok"] else "ERROS:\n- " + "\n- ".join(rep["errors"]))
            for w_ in rep["warnings"]:
                print("aviso:", w_)
        elif a.action in ("activate", "pause"):
            print(bots.set_active(a.id, user, a.action == "activate").status)
        else:
            bt = bots.get_bot(a.id)
            bots.ensure_runnable(bt, user)
            params = dict(p.split("=", 1) for p in a.param)
            res = run_steps({**bt.code, "name": ""}, params, confirm=_confirm, user=user, bot=bt.id)
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    elif a.cmd == "workflow":
        load_all()
        wf = load_workflow(a.name)
        params = dict(p.split("=", 1) for p in a.param)
        print(json.dumps(run_steps(wf, params, confirm=_confirm), ensure_ascii=False, indent=2))
    else:
        agent = Agent(OllamaClient(), confirm=_confirm, on_event=_event)
        if a.cmd == "run":
            print(agent.run(a.task))
        else:
            print(f"EmanusIA ({config.MODEL}) — workspace: {config.workspace()}. Ctrl+C sai.")
            hist: list[dict] = []
            while True:
                try:
                    task = input("\nvocê> ").strip()
                except (EOFError, KeyboardInterrupt):
                    return 0
                if task:
                    ans = agent.run(task, hist)
                    hist += [{"role": "user", "content": task}, {"role": "assistant", "content": ans}]
                    print(f"\nrobô> {ans}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
