import smtplib
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from emanusia import auth, bots, config, events, learning
from emanusia.engine import Runner, WorkflowError, safe_eval, validate
from emanusia.tools import registry
from emanusia.tools.registry import tool

H = {"host": "127.0.0.1"}
ADMIN = auth.LOCAL_ADMIN


# ferramenta de teste que falha N vezes antes de funcionar
_calls = {"n": 0}


@tool("t_flaky", "teste", {"fail_times": {"type": "integer"}})
def t_flaky(fail_times: int = 0):
    _calls["n"] += 1
    if _calls["n"] <= fail_times:
        raise RuntimeError("instável")
    return _calls["n"]


@tool("t_echo", "teste", {"value": {}}, ("value",))
def t_echo(value=None):
    return value


def run(steps, params=None, **kw):
    return Runner(ADMIN, **kw).run({"steps": steps}, params)


# ---------------------------------------------------------------- expressões
def test_safe_eval_allows_business_logic():
    ctx = {"linhas": [["a", 1], ["b", 5]], "total": 10, "nome": "Ana"}
    assert safe_eval("len(linhas) > 1 and total >= 10", ctx) is True
    assert safe_eval("linhas[1][1] * 2 + total", ctx) == 20
    assert safe_eval("'an' in lower(nome)", ctx) is True
    assert safe_eval("linhas[1:]", ctx) == [["b", 5]]
    assert safe_eval("'ok' if total > 5 else 'baixo'", ctx) == "ok"


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "nome.upper()", "(lambda: 1)()",
                                  "[x for x in linhas]", "open('f')", "'a' * 10**9"])
def test_safe_eval_blocks_code_execution(expr):
    with pytest.raises((WorkflowError, TypeError)):
        safe_eval(expr, {"nome": "a", "linhas": []})


# ---------------------------------------------------------------- motor
def test_engine_control_flow():
    steps = [
        {"set": {"itens": [3, 8, 1], "soma": 0, "grandes": []}},
        {"for_each": "itens", "as": "n", "steps": [
            {"set": {"soma": "=soma + n"}},
            {"if": "n > 2", "then": [{"set": {"grandes": "=grandes + [n]"}}]}]},
        {"if": "soma == 12", "then": [{"set": {"r": "ok"}}], "else": [{"fail": "soma errada"}]},
        {"set": {"i": 0}},
        {"while": "i < 3", "steps": [{"set": {"i": "=i + 1"}}]},
        {"try": [{"fail": "boom {{i}}"}], "catch": [{"set": {"capturado": "{{erro}}"}}]},
        {"tool": "t_echo", "args": {"value": "{{grandes}}"}, "save_as": "eco"},
        {"tool": "t_echo", "args": {"value": "pulado"}, "if": "False", "save_as": "nao"},
    ]
    out = run(steps)["outputs"]
    assert out["r"] == "ok" and out["i"] == 3 and "boom 3" in out["capturado"]
    assert "nao" not in out


def test_engine_retry_and_on_error():
    _calls["n"] = 0
    res = run([{"tool": "t_flaky", "args": {"fail_times": 2}, "retry": 2, "retry_delay": 0, "save_as": "x"}])
    assert res["outputs"]["x"] == 3
    _calls["n"] = 0
    res = run([{"tool": "t_flaky", "args": {"fail_times": 5}, "on_error": "continue", "save_as": "x"},
               {"set": {"seguiu": True}}])
    assert res["outputs"]["seguiu"] is True and res["steps"][0]["ok"] is False


def test_engine_while_limit():
    with pytest.raises(WorkflowError):
        run([{"while": "True", "max": 5, "steps": [{"log": "x"}]}])


def test_engine_risky_needs_confirmation():
    step = [{"tool": "sql_execute", "args": {"database": "a.db", "sql": "create table t(a)"}}]
    with pytest.raises(PermissionError):
        run(step)
    run(step, confirm=lambda n, a: True)


# ---------------------------------------------------------------- validação
def test_validation_catches_llm_mistakes():
    wf = {"steps": [
        {"tool": "nao_existe"},
        {"tool": "web_open", "args": {"link": "x"}},                         # arg inventado + falta url
        {"tool": "web_fill", "args": {"selector": "#senha", "value": "123"}},  # senha fixa
        {"tool": "http_get", "args": {"url": "u", "auth_env": "X"}},
        {"if": "import os", "then": []},
        {"call": "nao-existe"},
        {"call_bot": "fantasma"},
        {"tool": "email_send", "args": {"to": "a@b", "subject": "s", "body": "b"}},
        {"banana": 1},
    ]}
    rep = validate(wf)
    text = "\n".join(rep["errors"])
    for needle in ("'nao_existe' não existe", "não aceita o argumento 'link'", "exige o argumento 'url'",
                   "web_fill_secret", "Expressão", "workflow 'nao-existe'", "bot 'fantasma'",
                   "exatamente um tipo de passo"):
        assert needle in text, needle
    assert not rep["ok"] and rep["risky_tools"] == ["email_send"]


def test_validation_literal_password_arg_rejected():
    rep = validate({"steps": [{"tool": "t_echo", "args": {"value": 1}},
                              {"tool": "web_fill_secret", "args": {"selector": "#p", "env_var": "SENHA_X"}}]})
    assert rep["ok"]


# ---------------------------------------------------------------- bots
CODE = {"params": {"arq": "a.txt"}, "steps": [{"tool": "file_write", "args": {"path": "{{arq}}", "content": "oi"}}]}


def test_bot_lifecycle_and_revalidation():
    b = bots.create_bot("Bot Arquivo", ADMIN, CODE, "Financeiro")
    assert b.id == "bot-arquivo" and b.status == "draft"
    with pytest.raises(PermissionError):
        bots.ensure_runnable(b, ADMIN)
    with pytest.raises(ValueError):
        bots.set_active(b.id, ADMIN, True)
    assert bots.validate_bot(b.id, ADMIN)["ok"]
    assert bots.set_active(b.id, ADMIN, True).status == "active"
    bots.ensure_runnable(bots.get_bot(b.id), ADMIN, require_active=True)
    # editar o código derruba a validação
    b2 = bots.update_bot(b.id, ADMIN, code={**CODE, "steps": CODE["steps"] * 2})
    assert b2.status == "draft"
    with pytest.raises(ValueError):
        bots.create_bot("bot arquivo", ADMIN)  # nome duplicado (mesmo slug)


def test_bot_invalid_code_stays_draft():
    b = bots.create_bot("Quebrado", ADMIN, {"steps": [{"tool": "xyz"}]})
    rep = bots.validate_bot(b.id, ADMIN)
    assert not rep["ok"] and bots.get_bot(b.id).status == "draft"


def test_call_bot_handoff_emits_event_and_runs_subordinate():
    worker = bots.create_bot("Worker", ADMIN, CODE)
    bots.validate_bot(worker.id, ADMIN)
    boss = bots.create_bot("Boss", ADMIN, {"steps": [{"call_bot": "worker", "params": {"arq": "b.txt"}}]}, role="boss")
    assert bots.validate_bot(boss.id, ADMIN)["ok"]
    cur = events.last_id()
    Runner(ADMIN, bot=boss.id).run(boss.code)
    assert (config.workspace() / "b.txt").read_text() == "oi"
    kinds = [(e["kind"], e.get("bot"), e.get("target")) for e in events.since(cur)]
    assert ("handoff", "boss", "worker") in kinds and ("job_start", "worker", None) in kinds
    assert bots.get_bot("worker").stats["runs"] == 1


def test_self_call_rejected():
    b = bots.create_bot("Loop", ADMIN, {"steps": [{"log": "x"}]})
    bots.update_bot(b.id, ADMIN, code={"steps": [{"call_bot": "loop"}]})
    assert not bots.validate_bot(b.id, ADMIN)["ok"]


def test_other_users_cannot_run_unvalidated_or_foreign_draft():
    ana = auth.User("ana", "operator")
    bob = auth.User("bob", "operator")
    b = bots.create_bot("Da Ana", ana, CODE)
    bots.validate_bot(b.id, ana)
    with pytest.raises(PermissionError):
        bots.ensure_runnable(bots.get_bot(b.id), bob)  # validado mas não ativo: só a dona
    bots.set_active(b.id, ana, True)
    bots.ensure_runnable(bots.get_bot(b.id), bob)      # ativo: a equipe pode rodar
    with pytest.raises(PermissionError):
        bots.update_bot(b.id, bob, description="x")


# ---------------------------------------------------------------- geração por IA
class ScriptLLM:
    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def chat(self, messages, tools=None):
        self.seen.append(messages[-1]["content"])
        return {"role": "assistant", "content": self.answers.pop(0)}


def test_botgen_self_corrects_with_validator_feedback():
    from emanusia.botgen import generate
    bad = "```yaml\nsteps:\n  - tool: excel_criar\n    args: {path: a.xlsx}\n```"
    good = "```yaml\nparams: {}\nsteps:\n  - tool: excel_write\n    args: {path: a.xlsx, sheets: {A: [[1]]}}\n```"
    llm = ScriptLLM([bad, good])
    res = generate("gerar uma planilha de teste", llm, ADMIN)
    assert res["validation"]["ok"] and res["attempts"] == 2
    assert "excel_criar" in llm.seen[1]  # o erro do validador voltou para o modelo


def test_botgen_returns_best_effort_when_never_valid():
    from emanusia.botgen import generate
    res = generate("algo impossível", ScriptLLM(["não sei"] * 3), ADMIN)
    assert not res["validation"]["ok"] and res["attempts"] == 3


# ---------------------------------------------------------------- aprendizado
def _learn(task, url):
    learning.record_task("local", task, [
        {"name": "web_open", "args": {"url": url}, "ok": True},
        {"name": "web_table", "args": {"selector": "table"}, "ok": True},
        {"name": "web_click", "args": {"selector": "#x"}, "ok": False},  # erro não entra na receita
        {"name": "excel_write", "args": {"path": "r.xlsx", "sheets": {"A": [[1]]}}, "ok": True}], True)


def test_learning_suggests_parametrized_bot_and_accept_creates_it():
    for i, u in enumerate(["https://a.com/1", "https://a.com/2", "https://a.com/3"]):
        _learn(f"baixar tabela de pedidos do portal {i}", u)
    sg = learning.suggestions()
    assert len(sg) == 1
    s = sg[0]
    assert s["count"] == 3 and s["tools"] == ["web_open", "web_table", "excel_write"]
    assert s["name"] == "Baixar tabela pedidos portal"
    assert s["code"]["params"] == {"url": "https://a.com/3"}
    assert s["code"]["steps"][0]["args"]["url"] == "{{url}}"
    assert s["code"]["steps"][2]["args"]["path"] == "r.xlsx"  # igual sempre: fica fixo
    b = learning.accept(s["id"], ADMIN, "Coletor Pedidos", "Vendas")
    assert b.status == "validated" and b.origin == "sugestao"
    assert learning.suggestions() == []  # não sugere de novo


def test_learning_similar_tasks_become_hints():
    _learn("baixar tabela de pedidos do portal", "https://a.com")
    hints = learning.similar("Baixar a tabela de pedidos do portal hoje")
    assert hints and hints[0]["steps"][0]["tool"] == "web_open"
    assert learning.similar("mandar flores") == []


def test_auto_create_makes_draft_bots_only_when_enabled():
    config.save_settings(auto_create_bots=True, suggest_min_repeats=2)
    _learn("rotina x", "https://a.com/1")
    _learn("rotina x", "https://a.com/2")
    made = [b for b in bots.list_bots() if b.origin == "auto"]
    assert len(made) == 1 and made[0].status in ("validated", "draft") and made[0].status != "active"


def test_agent_records_successful_tasks():
    from emanusia.agent import Agent

    class LLM:
        def __init__(self): self.n = 0
        def chat(self, m, tools):
            self.n += 1
            if self.n == 1:
                return {"role": "assistant", "tool_calls": [{"function": {"name": "file_write",
                        "arguments": {"path": "x.txt", "content": "a"}}}]}
            return {"role": "assistant", "content": "feito"}
    Agent(LLM()).run("escrever arquivo x")
    t = learning.load_tasks()
    assert t[-1]["success"] and t[-1]["steps"][0]["tool"] == "file_write"


def test_tool_routing_keeps_prompt_small():
    from emanusia.agent import Agent
    names = lambda task: {s["function"]["name"] for s in Agent(ScriptLLM([])).tools_for(task)}
    web = names("abra o portal www.fornecedor.com e baixe a nota")
    assert "web_open" in web and "sap_run_transaction" not in web and "excel_write" in web
    sap = names("rodar a transação VA01 no SAP")
    assert "sap_run_transaction" in sap and "web_click" not in sap
    generic = names("xyz qualquer coisa")  # sem pista: só o básico (tudo não cabe no contexto de 8k)
    assert "create_bot" in generic and "sap_run_transaction" not in generic and len(generic) < 20


# ---------------------------------------------------------------- e-mail
def test_email_default_is_outlook(monkeypatch):
    from emanusia.tools import mail
    sent = {}
    monkeypatch.setattr(mail, "outlook_send", lambda *a: sent.setdefault("outlook", a))
    r = registry.call("email_send", {"to": "a@x.com; b@x.com", "subject": "s", "body": "b"})
    assert r == {"sent_to": ["a@x.com", "b@x.com"], "provider": "outlook"} and "outlook" in sent


def test_email_outlook_off_windows_has_clear_error():
    import sys
    if sys.platform == "win32":
        pytest.skip()
    with pytest.raises(RuntimeError, match="SMTP"):
        registry.call("email_send", {"to": "a@x.com", "subject": "s", "body": "b"})


def test_email_smtp_starttls(monkeypatch):
    log = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): log.append(("connect", host, port))
        def starttls(self): log.append(("starttls",))
        def login(self, u, p): log.append(("login", u, p))
        def send_message(self, m): log.append(("send", m["To"], m["Subject"]))
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("EMAIL_SMTP_PASS", "segredo")
    config.save_settings(email_provider="smtp", smtp_host="smtp.x.com", smtp_port=587, smtp_user="bot@x.com")
    (config.workspace() / "r.xlsx").write_bytes(b"x")
    registry.call("email_send", {"to": "a@x.com", "subject": "Rel", "body": "ok", "attachments": ["r.xlsx"]})
    assert log == [("connect", "smtp.x.com", 587), ("starttls",), ("login", "bot@x.com", "segredo"),
                   ("send", "a@x.com", "Rel")]
    assert "segredo" not in (config.data_dir() / "settings.json").read_text()


def test_settings_reject_unknown_keys():
    s = config.save_settings(smtp_pass="x", hacker=1, company_name="ACME")
    assert "smtp_pass" not in s and "hacker" not in s and s["company_name"] == "ACME"


# ---------------------------------------------------------------- web avançada
def test_web_complex_site_iframe_tabs_extract_wait(headless):
    page = config.workspace() / "app.html"
    inner = config.workspace() / "inner.html"
    inner.write_text("<input id='q'><button id='go' onclick=\"document.body.insertAdjacentHTML('beforeend',"
                     "'<p id=ok>feito '+q.value+'</p>')\">ir</button>")
    page.write_text(f"""<select id=s><option value=1>Um</option><option value=2>Dois</option></select>
      <div class=card><h3>Pedido A</h3><a href='/a'>ver</a></div><div class=card><h3>Pedido B</h3><a href='/b'>ver</a></div>
      <button id=lazy onclick="setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<i id=pronto>ok</i>'),300)">x</button>
      <a id=pop href='{inner.as_uri()}' target=_blank>abrir</a>
      <iframe id=fr src='{inner.as_uri()}'></iframe>""")
    try:
        registry.call("web_open", {"url": page.as_uri()})
        registry.call("web_select", {"selector": "#s", "value": "Dois"})
        assert registry.call("web_attr", {"selector": "#s", "name": "value"}) == "2"
        assert registry.call("web_extract", {"item_selector": ".card", "fields": {"t": "h3", "link": "a@href"}}) == \
            [{"t": "Pedido A", "link": "/a"}, {"t": "Pedido B", "link": "/b"}]
        registry.call("web_click", {"selector": "#lazy"})
        assert registry.call("web_exists", {"selector": "#pronto"}) is False
        registry.call("web_wait", {"selector": "#pronto", "timeout_s": 3})
        # iframe
        registry.call("web_frame", {"selector": "#fr"})
        registry.call("web_fill", {"selector": "#q", "value": "42"})
        registry.call("web_click", {"selector": "#go"})
        assert registry.call("web_text", {"selector": "#ok"}) == "feito 42"
        registry.call("web_frame", {})
        # nova aba (pop-up)
        assert registry.call("web_click", {"selector": "#pop", "new_tab": True})["tab"] == 1
        assert registry.call("web_exists", {"selector": "#q"}) is True
        assert len(registry.call("web_tabs", {})) == 2
        registry.call("web_switch_tab", {"index": 0})
        assert registry.call("web_exists", {"selector": "#s"}) is True
        registry.call("web_save_session", {"name": "portal"})
        assert (config.data_dir() / "web_sessions" / "portal.json").exists()
    finally:
        registry.call("web_close", {})


# ---------------------------------------------------------------- API / escritório
@pytest.fixture
def client():
    from emanusia import api
    with TestClient(api.app, headers=H) as c:
        yield c


def _wait(client, jid, want=("done", "error"), timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in want:
            return j
        time.sleep(0.05)
    raise AssertionError(j)


def test_api_bot_flow_and_office(client):
    y = yaml.safe_dump(CODE)
    b = client.post("/api/bots", json={"name": "Escritor", "team": "Adm", "yaml": y}).json()
    assert b["status"] == "validated"
    assert client.post("/api/schedules", json={"bot": "escritor", "when": {"every_minutes": 5}}).status_code == 422
    assert client.post("/api/bots/escritor/active", json={"active": True}).json()["status"] == "active"
    assert client.post("/api/schedules", json={"bot": "escritor", "when": {"every_minutes": 5}}).status_code == 200
    jid = client.post("/api/bots/escritor/run", json={"params": {"arq": "z.txt"}}).json()["id"]
    assert _wait(client, jid)["status"] == "done"
    assert (config.workspace() / "z.txt").exists()
    office = client.get("/api/office").json()
    ev = [e["kind"] for e in office["events"] if e.get("bot") == "escritor"]
    assert "job_start" in ev and "job_done" in ev
    assert office["bots"][0]["stats"]["runs"] == 1 and office["runs_today"] == 1
    assert "<canvas" in client.get("/office").text
    # editar código => volta a rascunho e não roda
    client.put("/api/bots/escritor", json={"yaml": yaml.safe_dump({"steps": [{"log": "novo"}]})})
    assert client.post("/api/bots/escritor/run", json={}).status_code == 403


def test_api_validate_endpoint(client):
    r = client.post("/api/validate", json={"yaml": "steps:\n  - tool: web_open\n"}).json()
    assert not r["ok"] and "exige o argumento 'url'" in r["errors"][0]
    assert not client.post("/api/validate", json={"yaml": "steps: [\n"}).json()["ok"]


def test_api_generate_job(client, monkeypatch):
    from emanusia import api
    ok = "params: {}\nsteps:\n  - log: oi\n"
    monkeypatch.setattr(api.manager, "llm_factory", lambda: ScriptLLM([ok]))
    jid = client.post("/api/bots/generate", json={"description": "um bot que diz oi no log"}).json()["id"]
    j = _wait(client, jid)
    assert j["status"] == "done" and j["result"]["validation"]["ok"]


def test_office_hides_other_users_details(client):
    auth.add_user("ana", "senha-forte-1", "admin")
    auth.add_user("bob", "senha-forte-2", "operator")
    ana = TestClient(client.app, headers=H)
    ana.post("/api/login", json={"user": "ana", "password": "senha-forte-1"})
    ana.post("/api/bots", json={"name": "Secreto", "description": "folha de pagamento", "yaml": yaml.safe_dump(CODE)})
    bob = TestClient(client.app, headers=H)
    bob.post("/api/login", json={"user": "bob", "password": "senha-forte-2"})
    o = bob.get("/api/office").json()["bots"][0]
    assert o["name"] == "Secreto" and o["description"] == "" and not o["mine"] and not o["can_run"]
    assert bob.get("/api/bots/secreto").status_code == 404        # não ativo: invisível
    assert bob.put("/api/bots/secreto", json={"team": "x"}).status_code == 404


def test_lan_mode_requires_users(monkeypatch, client):
    from emanusia import api
    monkeypatch.setattr(api, "LAN", True)
    r = client.get("/api/me", headers={"host": "192.168.0.10:8765"})
    assert r.status_code == 503
    auth.add_user("ana", "senha-forte-1", "admin")
    assert client.get("/api/me", headers={"host": "192.168.0.10:8765"}).status_code == 200


def test_demo_company_runs(client):
    from emanusia.demo import seed
    seed()
    bs = {b["id"]: b for b in client.get("/api/bots").json()}
    assert bs["chefe-financeiro"]["status"] == "active" and bs["conciliador-sap"]["status"] == "draft"
    jid = client.post("/api/bots/chefe-financeiro/run", json={}).json()["id"]
    assert _wait(client, jid)["status"] == "done"
    assert (config.workspace() / "demo" / "dashboard.html").exists()
    assert bs["coletor-de-vendas"]["stats"]["runs"] == 0
    assert client.get("/api/bots/coletor-de-vendas").json()["stats"]["runs"] == 1


def test_repo_example_workflows_are_valid():
    import pathlib
    for f in sorted((pathlib.Path(__file__).parent.parent / "workflows").glob("*.yaml")):
        rep = validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        assert rep["ok"], (f.name, rep["errors"])


def test_excel_formulas_in_args_are_not_evaluated():
    out = run([{"set": {"n": "=1 + 1"}},
               {"tool": "t_echo", "args": {"value": "=SOMA(B2:B{{n}})"}, "save_as": "f"}])["outputs"]
    assert out == {"n": 2, "f": "=SOMA(B2:B2)"}
