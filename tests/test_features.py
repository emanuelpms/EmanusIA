import sqlite3
import time
from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from emanusia import auth, config, scheduler
from emanusia.recorder import Recorder
from emanusia.tools import registry
from emanusia.tools.workflow import run_steps

H = {"host": "127.0.0.1"}


# ---------- gravador ----------
def test_recorder_turns_browsing_into_workflow(headless):
    from playwright.sync_api import sync_playwright
    import os
    page_file = config.workspace() / "login.html"
    page_file.write_text("""<form onsubmit="return false">
      <input name="user"><input type="password" name="pwd">
      <select name="tipo"><option>a</option><option>b</option></select>
      <button id="go">Entrar</button></form>""")
    rec = Recorder(page_file.as_uri())
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get("EMANUSIA_CHROMIUM"), headless=True)
        ctx = b.new_context()
        rec.attach(ctx)
        p = ctx.new_page()
        p.goto(page_file.as_uri())
        p.fill("[name=user]", "maria")
        p.fill("[name=pwd]", "SEGREDO123")
        p.select_option("[name=tipo]", "b")
        p.click("#go")
        p.wait_for_timeout(200)
        b.close()
    steps = rec.steps()
    core = [{"tool": s["tool"], "args": s["args"]} for s in steps]  # sem os rótulos humanos
    assert core[0] == {"tool": "web_open", "args": {"url": page_file.as_uri()}}
    assert {"tool": "web_fill", "args": {"selector": "input[name=\"user\"]", "value": "maria"}} in core
    assert {"tool": "web_fill_secret", "args": {"selector": "input[name=\"pwd\"]", "env_var": "SENHA_PWD"}} in core
    assert {"tool": "web_select", "args": {"selector": "select[name=\"tipo\"]", "value": "b"}} in core
    assert core[-1] == {"tool": "web_click", "args": {"selector": "#go"}} and steps[-1]["label"] == "Entrar"
    assert "SEGREDO123" not in yaml.safe_dump(steps)  # senha nunca é gravada
    rec.save("login-teste")
    assert "login-teste" in registry.call("list_workflows", {})


# ---------- agendador ----------
def test_next_run_interval_and_daily():
    now = datetime(2026, 9, 28, 10, 0)  # segunda
    assert scheduler.next_run({"every_minutes": 30}, now) == datetime(2026, 9, 28, 10, 30)
    assert scheduler.next_run({"daily_at": "08:30"}, now) == datetime(2026, 9, 29, 8, 30)
    # só sábado (5): pula até o próximo sábado
    assert scheduler.next_run({"daily_at": "09:00", "days": [5]}, now) == datetime(2026, 10, 3, 9, 0)
    with pytest.raises(ValueError):
        scheduler.next_run({}, now)


def test_due_returns_overdue_once_and_advances():
    t0 = datetime(2026, 1, 1, 8, 0)
    it = scheduler.add_schedule("w", {"every_minutes": 10}, now=t0)
    assert scheduler.due(datetime(2026, 1, 1, 8, 5)) == []
    got = scheduler.due(datetime(2026, 1, 1, 9, 0))  # atrasado 50 min: roda 1 vez só
    assert [g["id"] for g in got] == [it["id"]]
    assert scheduler.due(datetime(2026, 1, 1, 9, 1)) == []
    scheduler.set_enabled(it["id"], False)
    assert scheduler.due(datetime(2026, 1, 2)) == []


# ---------- usuários / permissões / auditoria ----------
def test_auth_password_login_and_last_admin_protected():
    assert not auth.auth_enabled() and auth.user_from_token(None) is auth.LOCAL_ADMIN
    auth.add_user("ana", "senha-forte-1", "admin")
    assert auth.auth_enabled() and auth.user_from_token(None) is None
    assert auth.login("ana", "errada") is None
    tok = auth.login("ana", "senha-forte-1")
    assert auth.user_from_token(tok).name == "ana"
    with pytest.raises(ValueError):
        auth.remove_user("ana")
    with pytest.raises(ValueError):
        auth.add_user("x", "curta", "operator")


def test_tool_whitelist_and_viewer_blocked():
    limited = auth.User("bob", "operator", tools=["file_read"])
    with pytest.raises(Exception, match="sem permissão"):
        run_steps({"steps": [{"tool": "file_write", "args": {"path": "x.txt", "content": "a"}}]}, user=limited)
    assert not (config.workspace() / "x.txt").exists()
    viewer = auth.User("v", "viewer")
    assert not viewer.may_use_tool("file_read")


def test_audit_redacts_secrets():
    auth.audit("u", "tool", tool="web_fill", args={"password": "abc", "nested": {"api_token": "z", "ok": 1}})
    rec = auth.read_audit()[0]
    assert rec["args"]["password"] == "***" and rec["args"]["nested"]["api_token"] == "***"
    assert rec["args"]["nested"]["ok"] == 1


def test_workflow_risky_step_denied_without_confirm():
    wf = {"name": "w", "steps": [{"tool": "sql_execute", "args": {"database": "d.db", "sql": "create table t(a)"}}]}
    with pytest.raises(PermissionError):
        run_steps(wf)
    run_steps(wf, confirm=lambda n, a: True)
    assert (config.workspace() / "d.db").exists()


def test_conditional_risk_for_web_click():
    t = registry.get("web_click")
    assert t.is_risky({"selector": "text=Excluir conta"}) and t.is_risky({"selector": "#pay"})
    assert not t.is_risky({"selector": "text=Próxima página"})


# ---------- conectores ----------
def test_sql_query_readonly_guard_and_result():
    db = config.safe_path("t.db")
    c = sqlite3.connect(db); c.execute("create table v(mes text, r int)"); c.execute("insert into v values('jan',10)"); c.commit(); c.close()
    assert registry.call("sql_query", {"database": "t.db", "sql": "select * from v"}) == [["mes", "r"], ["jan", 10]]
    for bad in ("delete from v", "select 1; drop table v"):
        with pytest.raises(PermissionError):
            registry.call("sql_query", {"database": "t.db", "sql": bad})


# ---------- API / GUI ----------
@pytest.fixture
def client():
    from emanusia import api
    with TestClient(api.app, headers=H) as c:
        yield c


def test_gui_served_and_local_mode(client):
    r = client.get("/")
    assert r.status_code == 200 and "EmanusIA" in r.text
    assert client.get("/api/me").json()["user"]["role"] == "admin"


def test_host_guard_blocks_dns_rebinding(client):
    assert client.get("/api/me", headers={"host": "evil.com"}).status_code == 400


def test_login_flow_and_roles(client):
    auth.add_user("ana", "senha-forte-1", "admin")
    auth.add_user("vic", "senha-forte-2", "viewer")
    assert client.get("/api/workflows").status_code == 401
    assert client.post("/api/login", json={"user": "ana", "password": "x"}).status_code == 401
    viewer = TestClient(client.app, headers=H)
    assert viewer.post("/api/login", json={"user": "vic", "password": "senha-forte-2"}).status_code == 200
    assert viewer.get("/api/workflows").status_code == 200
    assert viewer.post("/api/jobs/workflow", json={"name": "x"}).status_code == 403
    assert viewer.get("/api/users").status_code == 403
    assert client.post("/api/login", json={"user": "ana", "password": "senha-forte-1"}).status_code == 200
    assert client.get("/api/users").status_code == 200


def test_files_are_sandboxed_and_traversal_blocked(client):
    (config.workspace() / "d.html").write_text("<script>1</script>")
    r = client.get("/files/d.html")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    assert client.get("/files/..%2F..%2Fetc%2Fpasswd").status_code in (403, 404)


def test_workflow_crud_validates(client):
    good = yaml.safe_dump({"name": "a", "steps": [{"tool": "file_write", "args": {"path": "z", "content": "1"}}]})
    assert client.put("/api/workflows/a", json={"yaml": good}).status_code == 200
    assert client.put("/api/workflows/b", json={"yaml": "steps: [{tool: inexistente}]"}).status_code == 422
    assert client.put("/api/workflows/c", json={"yaml": "foo: bar"}).status_code == 422
    assert client.put("/api/workflows/..%2Fx", json={"yaml": good}).status_code in (404, 422, 400)
    assert [w["name"] for w in client.get("/api/workflows").json()] == ["a"]


def _wait(client, jid, want=("done", "error"), timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in want:
            return j
        time.sleep(0.1)
    raise AssertionError(j)


def test_job_workflow_with_interactive_approval(client):
    wf = {"name": "r", "steps": [{"tool": "sql_execute", "args": {"database": "q.db", "sql": "create table t(a)"}}]}
    client.put("/api/workflows/r", json={"yaml": yaml.safe_dump(wf)})
    jid = client.post("/api/jobs/workflow", json={"name": "r"}).json()["id"]
    j = _wait(client, jid, ("waiting_approval",))
    assert j["pending"]["tool"] == "sql_execute"
    client.post(f"/api/jobs/{jid}/approve", json={"ok": True})
    assert _wait(client, jid)["status"] == "done" and (config.workspace() / "q.db").exists()
    # negando
    jid = client.post("/api/jobs/workflow", json={"name": "r"}).json()["id"]
    _wait(client, jid, ("waiting_approval",))
    client.post(f"/api/jobs/{jid}/approve", json={"ok": False})
    assert _wait(client, jid)["status"] == "error"


def test_schedule_api_and_unattended_admin_only(client):
    auth.add_user("ana", "senha-forte-1", "admin")
    auth.add_user("op", "senha-forte-2", "operator")
    op = TestClient(client.app, headers=H)
    op.post("/api/login", json={"user": "op", "password": "senha-forte-2"})
    body = {"workflow": "w", "when": {"every_minutes": 5}}
    assert op.post("/api/schedules", json={**body, "unattended": True}).status_code == 403
    assert op.post("/api/schedules", json=body).status_code == 200
    assert op.post("/api/schedules", json={**body, "when": {}}).status_code == 422
