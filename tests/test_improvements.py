"""Melhorias do modo simples: consertar em qualquer nível, adicionar passos mostrando, iframes,
ações prontas e ensinar no SAP."""
import os
import threading

import pytest
from fastapi.testclient import TestClient

from emanusia import auth, bots, config, plain, sapimport
from emanusia.engine import Runner, StopBefore, get_step, insert_steps, replace_step
from emanusia.recorder import Recorder

H = {"host": "127.0.0.1"}
ADMIN = auth.LOCAL_ADMIN


def page(name, html):
    p = config.workspace() / name
    p.write_text(html, encoding="utf-8")
    return p.as_uri()


# ---------------------------------------------------------------- caminhos
def test_paths_distinguish_else_and_catch_and_stop_before():
    code = {"steps": [{"if": "x > 1", "then": [{"log": "a"}], "else": [{"log": "b"}, {"set": {"y": 1}}]},
                      {"try": [{"fail": "boom"}], "catch": [{"set": {"z": 2}}]}]}
    seen = []
    r = Runner(ADMIN, on_step=None)
    r.emit = lambda k, **d: seen.append(r.path) if k == "log" else None
    r.run(code, {"x": 0})
    assert seen[0] == "1.else.1"  # (o 2º registro é o "erro tratado" do try, no passo 2)
    assert get_step(code, "1.else.2") == {"set": {"y": 1}} and get_step(code, "2.catch.1") == {"set": {"z": 2}}
    with pytest.raises(StopBefore):
        Runner(ADMIN, stop_before="1.else.2").run(code, {"x": 0})
    new = replace_step(code, "1.else.1", {"log": "c"})
    assert new["steps"][0]["else"][0] == {"log": "c"} and code["steps"][0]["else"][0] == {"log": "b"}
    assert [s.get("log") for s in insert_steps({"steps": [{"log": 1}, {"log": 2}]}, 1, [{"log": 9}])["steps"]] == [1, 9, 2]


def test_rehearsal_reports_nested_failure_path(headless, monkeypatch):
    from emanusia.rehearsal import rehearse
    from emanusia.tools import web
    monkeypatch.setattr(web, "TIMEOUT_MS", 1000)
    url = page("n.html", "<b>oi</b>")
    b = bots.create_bot("Aninhado", ADMIN, {"steps": [{"tool": "web_open", "args": {"url": url}},
        {"if": "True", "then": [{"tool": "web_click", "args": {"selector": "#salvar"}, "label": "Salvar"}]}]})
    bots.validate_bot(b.id, ADMIN)
    rep = rehearse(b.id, ADMIN)
    assert rep["failed_at"] == "2.1" and "Não encontrei “Salvar”" in rep["error"]


# ---------------------------------------------------------------- mostrar onde fica (qualquer nível)
def test_fix_step_inside_if(headless, monkeypatch):
    from emanusia.rehearsal import fix_step
    from emanusia.tools import web
    monkeypatch.setattr(web, "TIMEOUT_MS", 1500)
    url = page("f.html", "<button id=novo>Gravar agora</button>")
    code = {"steps": [{"tool": "web_open", "args": {"url": url}},
                      {"if": "True", "then": [{"log": "x"}, {"tool": "web_click", "args": {"selector": "#velho"}, "label": "Gravar"}]}]}
    b = bots.create_bot("Conserta", ADMIN, code)
    bots.validate_bot(b.id, ADMIN)
    res = fix_step(b.id, "2.2", ADMIN, threading.Event(), _simulate_click="#novo")
    assert res["text"] == "Clicar em “Gravar agora”"
    assert get_step(bots.get_bot(b.id).code, "2.2")["args"]["selector"] == "#novo"
    with pytest.raises(ValueError, match="não aconteceu"):
        bots.update_bot(b.id, ADMIN, code={"steps": [code["steps"][0], {**code["steps"][1], "if": "False"}]})
        fix_step(b.id, "2.2", ADMIN, threading.Event(), _simulate_click="#novo")


# ---------------------------------------------------------------- adicionar passos mostrando
def test_record_insert_adds_steps_in_the_middle(headless):
    from emanusia.rehearsal import record_insert
    url = page("i.html", """<input id=busca aria-label="Buscar pedido"><button id=ir>Pesquisar</button>
      <button id=exp onclick="1">Exportar</button>""")
    b = bots.create_bot("Pedidos", ADMIN, {"steps": [{"tool": "web_open", "args": {"url": url}},
                                                    {"tool": "web_click", "args": {"selector": "#exp"}, "label": "Exportar"}]})
    bots.validate_bot(b.id, ADMIN)

    def act(pg):
        pg.fill("#busca", "123")
        pg.click("#ir")
    res = record_insert(b.id, 1, ADMIN, threading.Event(), _simulate=act)
    assert res["steps"] == ["Escrever “123” em “Buscar pedido”", "Clicar em “Pesquisar”"]
    texts = [s["text"] for s in plain.describe_code(bots.get_bot(b.id).code)]
    assert texts[1:] == ["Escrever “123” em “Buscar pedido”", "Clicar em “Pesquisar”", "Clicar em “Exportar”"]
    assert bots.get_bot(b.id).status == "validated"


def test_recorder_handles_iframes(headless):
    from playwright.sync_api import sync_playwright
    url = page("fr.html", """<meta charset=utf-8><iframe id=conteudo srcdoc="<input id=cod aria-label='Código'><button id=ok>Confirmar</button>"></iframe>
      <button id=sair>Sair</button>""")
    rec = Recorder(url)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get("EMANUSIA_CHROMIUM"), headless=True)
        ctx = b.new_context()
        rec.attach(ctx)
        pg = ctx.new_page()
        pg.goto(url)
        fr = pg.frame_locator("#conteudo")
        fr.locator("#cod").fill("77")
        fr.locator("#ok").click()
        pg.click("#sair")
        pg.wait_for_timeout(200)
        b.close()
    steps = rec.steps()
    assert [(s["tool"], s["args"].get("selector")) for s in steps[1:]] == [
        ("web_frame", "#conteudo"), ("web_fill", "#cod"), ("web_click", "#ok"), ("web_frame", ""), ("web_click", "#sair")]
    assert [plain.describe_step(s) for s in steps[1:3]] == ["Entrar no quadro interno da página", "Escrever “77” em “Código”"]


def test_recorded_iframe_steps_replay(headless):
    """Os passos gravados dentro do iframe funcionam de verdade no robô."""
    from emanusia.tools import web
    url = page("fr2.html", """<iframe id=conteudo srcdoc="<input id=cod><button id=ok onclick=&quot;document.body.insertAdjacentHTML('beforeend','<p id=r>'+cod.value+'</p>')&quot;>OK</button>"></iframe>""")
    code = {"steps": [{"tool": "web_open", "args": {"url": url}}, {"tool": "web_frame", "args": {"selector": "#conteudo"}},
                      {"tool": "web_fill", "args": {"selector": "#cod", "value": "77"}}, {"tool": "web_click", "args": {"selector": "#ok"}},
                      {"tool": "web_text", "args": {"selector": "#r"}, "save_as": "r"}]}
    try:
        assert Runner(ADMIN).run(code)["outputs"]["r"] == "77"
    finally:
        web.web_close()


# ---------------------------------------------------------------- ações prontas
def test_simple_steps_by_form():
    from emanusia.rehearsal import add_simple_step
    b = bots.create_bot("Relatorio", ADMIN, {"steps": [{"tool": "web_open", "args": {"url": "https://x.com"}}]})
    add_simple_step(b.id, ADMIN, "table_to_excel", 1, {"path": "notas.xlsx"})
    r = add_simple_step(b.id, ADMIN, "email", 3, {"to": "g@x.com", "subject": "Notas", "body": "Segue", "attachment": "notas.xlsx"})
    assert r["ok"]
    texts = [s["text"] for s in plain.describe_code(bots.get_bot(b.id).code)]
    assert texts == ["Abrir o site https://x.com", "Copiar a tabela da página", "Criar a planilha notas.xlsx",
                     "Enviar e-mail para g@x.com — “Notas”"]
    with pytest.raises(ValueError):
        add_simple_step(b.id, ADMIN, "voar", 0, {})


# ---------------------------------------------------------------- SAP
VBS = r'''If Not IsObject(application) Then
   Set SapGuiAuto  = GetObject("SAPGUI")
End If
session.findById("wnd[0]").maximize
session.findById("wnd[0]/usr/txtRSYST-MANDT").text = "300"
session.findById("wnd[0]/usr/txtRSYST-BNAME").text = "MARIA"
session.findById("wnd[0]/usr/pwdRSYST-BCODE").text = "********"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]/tbar[0]/okcd").text = "/nVA03"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]/usr/ctxtVBAK-VBELN").text = "4500012"
session.findById("wnd[0]/usr/ctxtVBAK-VBELN").caretPosition = 7
session.findById("wnd[0]/usr/chkP_TEST").selected = true
session.findById("wnd[0]/usr/cmbLANG").key = "PT"
session.findById("wnd[0]/usr/tabsTAB/tabpT\02").select
session.findById("wnd[0]/tbar[1]/btn[8]").press
session.findById("wnd[0]/usr/cntlGRID/shellcont/shell").pressToolbarButton "&MB_EXPORT"
session.findById("wnd[0]/usr/cntlGRID/shellcont/shell").setCurrentCell -1,"VBELN"
session.findById("wnd[0]").sendVKey 3
'''


def test_sap_recording_becomes_bot_steps():
    res = sapimport.parse(VBS, credential="sap-prd")
    steps = res["code"]["steps"]
    assert [s["tool"] for s in steps] == ["sap_login", "sap_run_transaction", "sap_set_field", "sap_select", "sap_select",
                                          "sap_select", "sap_press", "sap_call", "sap_call", "sap_press"]
    assert steps[2]["args"] == {"field_id": "wnd[0]/usr/ctxtVBAK-VBELN", "value": "4500012"}
    assert steps[8]["args"]["args"] == [-1, "VBELN"]
    assert [plain.describe_step(s) for s in steps] == [
        "Entrar no SAP com o acesso “sap-prd”", "Abrir a transação VA03 no SAP", "Preencher “VBAK-VBELN” no SAP com “4500012”",
        "Marcar “P_TEST” no SAP", "Escolher “PT” em “LANG” no SAP", "Abrir “T\\02” no SAP", "Clicar em “Executar (F8)” no SAP",
        "Clicar no botão “&MB_EXPORT” do relatório SAP", "Selecionar uma célula do relatório SAP", "Voltar (F3) no SAP"]
    assert "********" not in str(steps) and "MARIA" not in str(steps)


def test_sap_recording_without_credential_warns_and_is_valid():
    from emanusia.engine import validate
    res = sapimport.parse(VBS)
    assert any("senha" in w for w in res["warnings"]) and res["code"]["steps"][0]["tool"] == "sap_run_transaction"
    assert validate(res["code"])["ok"]
    with pytest.raises(ValueError, match="Não encontrei"):
        sapimport.parse("MsgBox \"oi\"")


def test_api_teach_sap_and_add_step():
    from emanusia import api
    with TestClient(api.app, headers=H) as c:
        r = c.post("/api/teach/sap", json={"name": "Consulta ordem", "script": VBS}).json()
        assert r["bot"]["status"] == "validated" and r["bot"]["next"]["step"] == "rehearse" and r["warnings"]
        assert c.post("/api/teach/sap", json={"name": "Nada", "script": "x"}).status_code == 422
        r = c.post("/api/bots/consulta-ordem/steps", json={"kind": "wait", "after": 0, "fields": {"seconds": "3"}}).json()
        assert r["ok"] and bots.get_bot("consulta-ordem").code["steps"][0] == {"sleep": 3}
        assert "table_to_excel" in c.get("/api/simple-steps").json()
