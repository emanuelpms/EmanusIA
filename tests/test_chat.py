"""Chat de ponta a ponta pelo HTTP (como no Windows com o Ollama): criar robô pelo chat, erros da IA
explicados, respostas tortas de modelos pequenos e tamanho do contexto."""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from emanusia import bots, config
from emanusia.agent import Agent, TOOL_BUDGET, _tokens, parse_calls
from emanusia.llm import LLMError, OllamaClient

from fake_ollama import FakeOllama

H = {"host": "127.0.0.1"}


@pytest.fixture
def ollama(monkeypatch):
    servers = []

    def make(reply, **kw):
        f = FakeOllama(reply, **kw)
        servers.append(f)
        monkeypatch.setattr(config, "OLLAMA_URL", f.url)
        return f
    yield make
    for s in servers:
        s.close()


@pytest.fixture
def client():
    from emanusia import api
    with TestClient(api.app, headers=H) as c:
        yield c


def run_chat(client, task, history=None, timeout=30):
    jid = client.post("/api/jobs/agent", json={"task": task, "history": history or []}).json()["id"]
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError(j)


def last_user(req):
    return next(m["content"] for m in reversed(req["messages"]) if m["role"] == "user")


# ---------------------------------------------------------------- criar robô pelo chat
def test_chat_creates_a_bot(ollama, client):
    YAML = ("```yaml\nparams: {destino: \"equipe@empresa.com\"}\nsteps:\n  - tool: email_send\n"
            "    args: {to: \"{{destino}}\", subject: \"Bom dia!\", body: \"Bom dia, equipe!\"}\n```")

    def reply(req):
        system = req["messages"][0]["content"]
        if "CÓDIGO de bots" in system:  # gerador de robôs (sem ferramentas)
            assert not req.get("tools")
            return {"content": YAML}
        if req["messages"][-1]["role"] == "tool":
            res = json.loads(req["messages"][-1]["content"])
            return {"content": f"Criei o robô {res['name']} com {len(res['steps'])} passo(s). {res['next']}"}
        names = {t["function"]["name"] for t in req["tools"]}
        assert "create_bot" in names
        return {"tool_calls": [{"function": {"name": "create_bot", "arguments": {
            "name": "Bom dia da equipe", "description": "todo dia de manhã manda um e-mail de bom dia para a equipe"}}}]}
    fake = ollama(reply)
    j = run_chat(client, "crie um robô que mande um bom dia para a equipe por e-mail")
    assert j["status"] == "done", j
    assert "Criei o robô Bom dia da equipe" in j["result"]
    b = bots.get_bot("bom-dia-da-equipe")
    assert b and b.origin == "ia" and b.status == "validated" and b.code["steps"][0]["tool"] == "email_send"
    assert fake.problems == []


def test_chat_bot_name_collision_gets_suffix(ollama, client):
    def reply(req):
        if "CÓDIGO de bots" in req["messages"][0]["content"]:
            return {"content": "steps:\n  - log: oi\n"}
        if req["messages"][-1]["role"] == "tool":
            return {"content": json.loads(req["messages"][-1]["content"])["name"]}
        return {"tool_calls": [{"function": {"name": "create_bot", "arguments": {"name": "Relatório", "description": "anota oi no histórico todo dia"}}}]}
    ollama(reply)
    assert run_chat(client, "crie um robô de relatório")["result"] == "Relatório"
    assert run_chat(client, "crie outro robô de relatório")["result"] == "Relatório (2)"


# ---------------------------------------------------------------- erros da IA em português
def test_model_not_installed(ollama, client):
    ollama(lambda r: {"content": "oi"}, models=["llama3:8b"])
    j = run_chat(client, "oi")
    assert j["status"] == "error" and "ollama pull qwen2.5:7b" in j["plain_error"]
    assert client.get("/api/health").json()["message"].endswith("ollama pull qwen2.5:7b")


def test_ollama_not_running(monkeypatch, client):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")  # porta fechada
    j = run_chat(client, "oi")
    assert j["status"] == "error" and j["plain_error"].startswith("O Ollama não está aberto")
    h = client.get("/api/health").json()
    assert not h["ok"] and not h["running"]


def test_model_without_tools(ollama, client):
    ollama(lambda r: {"content": "oi"}, tools_supported=False)
    j = run_chat(client, "oi")
    assert "não sabe usar ferramentas" in j["plain_error"]


def test_error_inside_create_bot_is_reported_not_swallowed(ollama, client):
    """Se a IA cair no meio da criação do robô, o chat mostra o motivo (e não finge que deu certo)."""
    def reply(req):
        if "CÓDIGO de bots" in req["messages"][0]["content"]:
            return (404, 'model "qwen2.5:7b" not found, try pulling it first')
        if req["messages"][-1]["role"] == "tool":
            return {"content": "Pronto."}
        return {"tool_calls": [{"function": {"name": "create_bot", "arguments": {"name": "X", "description": "faz uma coisa muito útil"}}}]}
    ollama(reply)
    j = run_chat(client, "crie um robô")
    assert j["status"] == "error" and "não está instalado" in j["plain_error"]
    assert bots.get_bot("x") is None


# ---------------------------------------------------------------- respostas tortas de modelos pequenos
def test_tool_call_written_as_text_and_broken_json(ollama, client):
    step = {"n": 0}

    def reply(req):
        step["n"] += 1
        if step["n"] == 1:  # JSON quebrado nos argumentos
            return {"tool_calls": [{"function": {"name": "file_write", "arguments": "{path: sem aspas"}}]}
        if step["n"] == 2:
            assert "JSON inválido" in req["messages"][-1]["content"]
            return {"content": '<tool_call>\n{"name": "file_write", "arguments": {"path": "oi.txt", "content": "olá"}}\n</tool_call>'}
        return {"content": "Arquivo salvo."}
    fake = ollama(reply)
    j = run_chat(client, "salve um arquivo oi.txt com olá")
    assert j["result"] == "Arquivo salvo." and (config.workspace() / "oi.txt").read_text(encoding="utf-8") == "olá"
    assert fake.problems == []


def test_parse_calls_variants():
    assert parse_calls({"content": '```json\n{"name": "list_bots", "arguments": {}}\n```'}) == [("list_bots", {})]
    assert parse_calls({"content": '{"name": "nao_existe", "arguments": {}}'}) == []
    assert parse_calls({"content": "Olá! Posso ajudar?"}) == []
    assert parse_calls({"tool_calls": [{"function": {"name": "x", "arguments": '{"a": 1}'}}]}) == [("x", {"a": 1})]


def test_empty_answer_gets_fallback(ollama, client):
    ollama(lambda r: {"content": ""})
    assert run_chat(client, "hum")["result"].startswith("Não entendi")


# ---------------------------------------------------------------- tamanho do contexto
TASKS = ["oi", "crie um robô que me mande um bom dia", "preciso automatizar o fechamento do mês",
         "crie um bot que entra no portal do fornecedor, faz login, baixa as notas em pdf, extrai as tabelas, "
         "junta com a planilha do sap, monta um dashboard e manda por email pelo outlook",
         "abra o programa do banco, leia a tela, consulte o sql e mande para a api do erp e para o power bi"]


@pytest.mark.parametrize("task", TASKS)
def test_tools_always_fit_the_budget(task):
    schemas = Agent(object()).tools_for(task)
    assert _tokens(schemas) <= int(config.NUM_CTX * TOOL_BUDGET)
    assert "create_bot" in {s["function"]["name"] for s in schemas}


def test_long_conversation_never_overflows_context(ollama, client):
    big = "x" * 20000
    n = {"i": 0}

    def reply(req):
        n["i"] += 1
        if n["i"] < 6:
            return {"tool_calls": [{"function": {"name": "file_write", "arguments": {"path": f"f{n['i']}.txt", "content": big}}},
                                   {"function": {"name": "file_read", "arguments": {"path": f"f{n['i']}.txt"}}}]}
        return {"content": "ok"}
    fake = ollama(reply)
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "y" * 5000} for i in range(20)]
    assert run_chat(client, "salve e leia arquivos grandes", history)["result"] == "ok"
    assert fake.problems == [], fake.problems


def test_botgen_prompt_fits_and_retries_do_not_grow(ollama):
    from emanusia import auth
    from emanusia.botgen import generate
    fake = ollama(lambda r: {"content": "steps:\n  - tool: nao_existe\n"})
    res = generate("entra no portal do fornecedor, faz login, baixa notas, junta planilhas e manda e-mail",
                   OllamaClient(), auth.LOCAL_ADMIN)
    assert not res["validation"]["ok"] and res["attempts"] == 3
    sizes = [len(json.dumps(r["messages"])) for r in fake.requests]
    assert sizes[2] <= sizes[1] * 1.05 and fake.problems == []


# ---------------------------------------------------------------- Windows
def test_cli_survives_windows_console_encoding(monkeypatch):
    from emanusia import cli
    raw = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli.main(["tools"])  # imprime ⚠️ e acentos
    import sys
    sys.stdout.flush()
    assert "⚠" in raw.getvalue().decode("utf-8")


def test_llm_error_messages():
    with pytest.raises(LLMError, match="não está aberto"):
        OllamaClient(url="http://127.0.0.1:9").chat([{"role": "user", "content": "oi"}])


# ---------------------------------------------------------------- diagnóstico
def by_name(rep):
    return {i["name"]: i for i in rep["items"]}


def test_doctor_all_good(ollama, headless):
    from emanusia import doctor
    ollama(lambda r: {"tool_calls": [{"function": {"name": "confirmar", "arguments": {"ok": True}}}]})
    rep = doctor.run()
    items = by_name(rep)
    for k in ("Python", "Pastas de trabalho", "Pacotes do sistema", "Navegador dos robôs", "Ollama (IA local)",
              "Modelo qwen2.5:7b", "IA usando ferramentas", "Placa de vídeo (GPU)", "Cofre de acessos"):
        assert items[k]["ok"], (k, items[k])
    assert rep["ok"]


def test_doctor_explains_problems(ollama, headless):
    from emanusia import doctor
    f = ollama(lambda r: {"content": "não sei"})
    f.vram = 0
    items = by_name(doctor.run())
    assert items["IA usando ferramentas"]["level"] == "aviso"
    assert items["Placa de vídeo (GPU)"]["level"] == "aviso" and "driver NVIDIA" in items["Placa de vídeo (GPU)"]["fix"]
    f.models = ["llama3:8b"]
    items = by_name(doctor.run())
    assert not items["Modelo qwen2.5:7b"]["ok"] and items["Modelo qwen2.5:7b"]["fix"] == "No Prompt de Comando: ollama pull qwen2.5:7b"


def test_doctor_ollama_down_and_errors_logged(monkeypatch, client):
    from emanusia import doctor
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    run_chat(client, "oi")  # falha -> vai para o log com o rastro
    rep = doctor.run(full=False)
    assert not by_name(rep)["Ollama (IA local)"]["ok"] and not rep["ok"]
    assert any("tarefa" in e and "falhou" in e for e in rep["errors"])
    log = (config.data_dir() / "logs" / "emanusia.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "LLMError" in log
    assert client.get("/api/doctor").status_code == 200


# ---------------------------------------------------------------- abrir o sistema (abrir.bat)
def test_gui_reuses_running_instance_and_skips_busy_port(monkeypatch):
    import socket
    import threading

    import uvicorn

    from emanusia import gui
    shown = []
    monkeypatch.setattr(gui, "_show", lambda url, wait_forever: shown.append(url))
    # 1) já existe um EmanusIA aberto nessa porta: só mostra, não sobe outro servidor
    srv = uvicorn.Server(uvicorn.Config("emanusia.api:app", host="127.0.0.1", port=8877, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    try:
        gui.launch(8877)
        assert shown == ["http://127.0.0.1:8877/"]
    finally:
        srv.should_exit = True
    # 2) porta ocupada por OUTRO programa: usa a próxima livre (antes travava para sempre)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 8878))
    busy.listen(1)
    try:
        gui.launch(8878)
        assert shown[-1] == "http://127.0.0.1:8879/"
    finally:
        busy.close()
