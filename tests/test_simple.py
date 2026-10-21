"""Modo simples: ensinar mostrando, passos em português, linguagem comum e revisão."""
import os
import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from emanusia import auth, bots, config, plain, vault
from emanusia.recorder import Recorder

H = {"host": "127.0.0.1"}
ADMIN = auth.LOCAL_ADMIN


def page(name, html):
    p = config.workspace() / name
    p.write_text(html, encoding="utf-8")
    return p.as_uri()


LOGIN = """<label for=u>E-mail</label><input id=u name=email><label for=p>Senha</label><input id=p type=password>
<select name=filial aria-label="Filial"><option>SP</option><option>RJ</option></select>
<button id=entrar onclick="document.body.innerHTML='<h1 id=ok>Bem-vindo</h1>'">Entrar</button>"""


# ---------------------------------------------------------------- passos em português
def test_recorded_steps_read_like_portuguese(headless):
    from playwright.sync_api import sync_playwright
    url = page("login.html", LOGIN)
    rec = Recorder(url, credential="portal", username="maria@x.com")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get("EMANUSIA_CHROMIUM"), headless=True)
        ctx = b.new_context()
        rec.attach(ctx)
        pg = ctx.new_page()
        pg.goto(url)
        pg.fill("#u", "maria@x.com")
        pg.fill("#p", "SegredoReal!")
        pg.select_option("[name=filial]", "RJ")
        pg.click("#entrar")
        pg.wait_for_timeout(200)
        b.close()
    code = {"steps": rec.steps()}
    assert "SegredoReal!" not in yaml.safe_dump(code) and "maria@x.com" not in yaml.safe_dump(code)
    texts = [s["text"] for s in plain.describe_code(code)]
    assert texts == [f"Abrir o site {url}" if len(url) <= 60 else texts[0],
                     "Preencher o usuário do acesso “portal”", "Preencher a senha do acesso “portal”",
                     "Escolher “RJ” em “Filial”", "Clicar em “Entrar”"]


def test_describe_control_flow_and_selectors():
    code = {"steps": [{"if": "len(pedidos) > 0", "then": [{"tool": "web_click", "args": {"selector": "text=Próxima"}}],
                       "else": [{"fail": "Sem pedidos"}]},
                      {"tool": "email_send", "args": {"to": "fin@x.com", "subject": "Relatório", "body": "."}, "retry": 2}]}
    d = plain.describe_code(code)
    assert [(x["path"], x["text"]) for x in d] == [
        ("1", "Se «Pedidos» tem algum item:"), ("1.1", "Clicar em “Próxima”"), ("1.else", "Senão:"),
        ("1.else.1", "Parar com o aviso: Sem pedidos"),
        ("2", "Enviar e-mail para fin@x.com — “Relatório” — tenta 3 vezes se falhar")]
    assert plain._step_at(code, "1.else.1") == {"fail": "Sem pedidos"}


# ---------------------------------------------------------------- linguagem comum
def test_errors_and_approvals_in_plain_language():
    step = {"tool": "web_click", "args": {"selector": "#entrar"}, "label": "Entrar"}
    assert plain.explain_error("TimeoutError: Locator.click: Timeout 20000ms exceeded. waiting for locator('#entrar')",
                               step).startswith("Não encontrei “Entrar” na tela")
    assert "cadastrou o seu acesso “erp”" in plain.explain_error("KeyError: \"Acesso 'erp' não mapeado para o usuário 'x'\"")
    assert "internet" in plain.explain_error("Error: net::ERR_NAME_NOT_RESOLVED at https://x")
    assert plain.explain_error("WorkflowError: Nenhum pedido hoje") == "Nenhum pedido hoje"
    msg = plain.describe_action("email_send", {"to": "fin@x.com", "subject": "Fechamento", "attachments": ["a.xlsx"]})
    assert msg == "O robô vai ENVIAR um e-mail para fin@x.com com o assunto “Fechamento” e 1 anexo(s). Pode enviar?"
    rep = {"errors": ["steps[2]: 'web_open' exige o argumento 'url'", "steps[1].then[3]: ferramenta 'voar' não existe",
                      "steps[4]: segredo escrito no código ('password')"]}
    assert plain.explain_validation(rep) == ["Passo 2: falta informar endereço do site.",
                                             "Passo 1 → 3: o robô não sabe fazer “voar”.",
                                             "Passo 4: tem uma senha escrita no robô. Use um acesso cadastrado em 🔑 Acessos."]


# ---------------------------------------------------------------- ensaio + mostrar onde fica
def test_rehearsal_screenshots_failure_and_fix_by_showing(headless, monkeypatch):
    from emanusia.rehearsal import fix_step, rehearse
    from emanusia.tools import web
    monkeypatch.setattr(web, "TIMEOUT_MS", 1500)
    url = page("app.html", "<button id=entrar onclick=\"document.body.insertAdjacentHTML('beforeend','<p id=ok>ok</p>')\">Entrar</button>")
    code = {"params": {}, "steps": [{"tool": "web_open", "args": {"url": url}},
                                    {"tool": "web_click", "args": {"selector": "#entrar"}, "label": "Entrar"},
                                    {"tool": "web_wait", "args": {"selector": "#ok"}}]}
    b = bots.create_bot("Clicador", ADMIN, code)
    bots.validate_bot(b.id, ADMIN)
    assert bots.next_step(bots.get_bot(b.id))["step"] == "rehearse"
    with pytest.raises(ValueError, match="ensaio"):
        bots.set_active(b.id, ADMIN, True)

    rep = rehearse(b.id, ADMIN)
    assert rep["ok"] and [s["ok"] for s in rep["steps"]] == [True, True, True]
    assert all((config.data_dir() / "rehearsals" / b.id / s["shot"]).exists() for s in rep["steps"])
    assert bots.set_active(b.id, ADMIN, True).status == "active"

    # o site mudou: o botão agora é outro
    page("app.html", "<button id=login-novo onclick=\"document.body.insertAdjacentHTML('beforeend','<p id=ok>ok</p>')\">Acessar</button>")
    rep = rehearse(b.id, ADMIN)
    assert not rep["ok"] and rep["failed_at"] == "2"
    assert rep["steps"][-1]["error"].startswith("Não encontrei “Entrar” na tela")

    fixed = fix_step(b.id, "2", ADMIN, threading.Event(), _simulate_click="#login-novo")
    assert fixed["selector"] == "#login-novo" and fixed["text"] == "Clicar em “Acessar”"
    nb = bots.get_bot(b.id)
    assert nb.status == "validated" and bots.next_step(nb)["step"] == "rehearse"  # mudou: ensaiar de novo
    assert rehearse(b.id, ADMIN)["ok"]


def test_rehearsal_not_needed_for_office_only_bots():
    b = bots.create_bot("Planilheiro", ADMIN, {"steps": [{"tool": "file_write", "args": {"path": "a", "content": "b"}}]})
    bots.validate_bot(b.id, ADMIN)
    assert not bots.needs_rehearsal(b.code) and bots.next_step(bots.get_bot(b.id))["step"] == "activate"


# ---------------------------------------------------------------- revisão pelo padrinho
@pytest.fixture
def team():
    from emanusia import api
    auth.add_user("admin", "senha-forte-0", "admin")
    auth.add_user("tia", "senha-forte-1", "operator")
    auth.add_user("padrinho", "senha-forte-2", "operator", reviewer=True)
    with TestClient(api.app, headers=H) as c:
        def login(name, pw):
            cl = TestClient(api.app, headers=H)
            assert cl.post("/api/login", json={"user": name, "password": pw}).status_code == 200
            return cl
        yield login("tia", "senha-forte-1"), login("padrinho", "senha-forte-2"), c


CODE = yaml.safe_dump({"steps": [{"tool": "file_write", "args": {"path": "limpeza.txt", "content": "ok"}}]})


def test_review_flow_non_technical_user(team):
    tia, padrinho, _ = team
    b = tia.post("/api/bots", json={"name": "Checklist Limpeza", "yaml": CODE}).json()
    assert b["next"]["step"] == "submit_review"
    r = tia.post("/api/bots/checklist-limpeza/active", json={"active": True})
    assert r.status_code == 422 and "padrinho" in r.json()["detail"]
    assert tia.post("/api/bots/checklist-limpeza/review/submit", json={"note": "primeiro bot!"}).status_code == 200
    assert tia.get("/api/reviews").json() == []
    pend = padrinho.get("/api/reviews").json()
    assert [p["id"] for p in pend] == ["checklist-limpeza"] and pend[0]["steps"][0]["text"] == "Salvar o arquivo limpeza.txt"
    assert padrinho.get("/api/bots/checklist-limpeza/steps").status_code == 200
    assert padrinho.post("/api/bots/checklist-limpeza/review", json={"approve": False}).status_code == 422  # precisa dizer o quê
    padrinho.post("/api/bots/checklist-limpeza/review", json={"approve": False, "note": "troque o nome do arquivo"})
    b = tia.get("/api/bots/checklist-limpeza").json()
    assert b["review"]["status"] == "changes" and b["next"]["step"] == "submit_review"
    tia.post("/api/bots/checklist-limpeza/review/submit", json={})
    b = padrinho.post("/api/bots/checklist-limpeza/review", json={"approve": True}).json()
    assert b["status"] == "active" and b["next"]["step"] == "done"
    # editar depois de aprovado exige nova revisão
    tia.put("/api/bots/checklist-limpeza", json={"code": {"steps": [{"log": "novo"}]}})
    tia.post("/api/bots/checklist-limpeza/validate")
    assert tia.get("/api/bots/checklist-limpeza").json()["next"]["step"] == "submit_review"


def test_reviewer_cannot_approve_own_bot(team):
    _, padrinho, _ = team
    padrinho.post("/api/bots", json={"name": "Do Padrinho", "yaml": CODE})
    b = padrinho.get("/api/bots/do-padrinho").json()
    assert b["next"]["step"] == "activate"  # padrinho libera o próprio sem revisão


def test_steps_endpoint_and_plain_validation(team):
    tia, _, _ = team
    tia.post("/api/bots", json={"name": "Quebrado", "yaml": "steps:\n  - tool: web_open\n"})
    d = tia.get("/api/bots/quebrado/steps").json()
    assert d["plain_errors"] == ["Passo 1: falta informar endereço do site."] and d["next"]["step"] == "validate"
    assert tia.post("/api/bots/quebrado/rehearse", json={}).status_code == 422
    v = tia.post("/api/validate", json={"yaml": "steps: [{tool: voar}]"}).json()
    assert v["plain_errors"] == ["Passo 1: o robô não sabe fazer “voar”."]


def test_job_approval_and_errors_in_plain_language():
    from emanusia import api
    with TestClient(api.app, headers=H) as c:
        c.put("/api/workflows/w", json={"yaml": yaml.safe_dump({"steps": [
            {"tool": "sql_execute", "args": {"database": "d.db", "sql": "create table t(a)"}}]})})
        jid = c.post("/api/jobs/workflow", json={"name": "w"}).json()["id"]
        for _ in range(100):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["status"] == "waiting_approval":
                break
            time.sleep(0.05)
        assert j["pending"]["text"] == "O robô vai ALTERAR dados no banco de dados. Pode continuar?"
        c.post(f"/api/jobs/{jid}/approve", json={"ok": False})
        for _ in range(100):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["status"] == "error":
                break
            time.sleep(0.05)
        assert j["plain_error"] == "A ação não foi autorizada, então o robô parou antes de fazê-la."


def test_failed_rehearsal_is_shown_on_bot_page(headless, monkeypatch):
    """Regressão: a tela escondia o ensaio que falhou (justamente o que a pessoa precisa ver)."""
    from emanusia import api
    from emanusia.rehearsal import rehearse
    from emanusia.tools import web
    monkeypatch.setattr(web, "TIMEOUT_MS", 1000)
    url = page("x.html", "<p>sem botão</p>")
    b = bots.create_bot("Falha", ADMIN, {"steps": [{"tool": "web_open", "args": {"url": url}},
                                                  {"tool": "web_click", "args": {"selector": "#nada"}, "label": "Enviar"}]})
    bots.validate_bot(b.id, ADMIN)
    rehearse(b.id, ADMIN)
    with TestClient(api.app, headers=H) as c:
        d = c.get("/api/bots/falha/steps").json()
    assert d["rehearsal_current"] and not d["rehearsal"]["ok"] and d["rehearsal"]["failed_at"] == "2"
    assert d["next"]["step"] == "rehearse"
