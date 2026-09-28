import os

import pytest
import yaml

from emanusia import config
from emanusia.agent import Agent
from emanusia.tools import load_all, registry
from emanusia.tools.workflow import render, run_steps


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EMANUSIA_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("EMANUSIA_WORKFLOWS", str(tmp_path / "wf"))
    load_all()


class FakeLLM:
    """Roteiro: 1) chama excel_write 2) responde."""
    def __init__(self, script): self.script = list(script)
    def chat(self, messages, tools): return self.script.pop(0)


def call(name, args): return {"function": {"name": name, "arguments": args}}


def test_path_traversal_blocked():
    with pytest.raises(PermissionError):
        config.safe_path("../fora.txt")


def test_agent_loop_runs_tools_and_returns_answer():
    llm = FakeLLM([
        {"role": "assistant", "tool_calls": [call("excel_write", {"path": "a.xlsx", "sheets": {"S": [["a", "b"], [1, 2]]}})]},
        {"role": "assistant", "content": "pronto"}])
    assert Agent(llm).run("faça") == "pronto"
    assert (config.workspace() / "a.xlsx").exists()


def test_risky_tool_blocked_without_confirmation():
    llm = FakeLLM([{"role": "assistant", "tool_calls": [call("sap_set_field", {"field_id": "x", "value": "1"})]},
                   {"role": "assistant", "content": "ok"}])
    events = []
    Agent(llm, on_event=lambda k, d: events.append((k, d))).run("x")
    assert "não autorizada" in events[-1][1]["result"]["error"]


def test_tool_error_is_fed_back_not_raised():
    llm = FakeLLM([{"role": "assistant", "tool_calls": [call("file_read", {"path": "nao-existe.txt"})]},
                   {"role": "assistant", "content": "falhou"}])
    assert Agent(llm).run("x") == "falhou"


def test_render_preserves_types():
    ctx = {"t": [["a"], ["b"]], "n": 3}
    assert render("{{t}}", ctx) == [["a"], ["b"]]
    assert render("{{t.1.0}}-{{n}}", ctx) == "b-3"


def test_dashboard_excel_powerbi_example_workflow():
    wf = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "workflows", "exemplo-relatorio.yaml")))
    run_steps(wf)
    ws = config.workspace()
    html = (ws / "dashboard.html").read_text()
    assert "Vendas Q1" in html and "Plotly.newPlot" in html
    assert (ws / "vendas.xlsx").exists() and (ws / "powerbi/vendas.pq").exists()
    assert "Csv.Document" in (ws / "powerbi/vendas.pq").read_text()


def test_save_and_run_workflow_with_params():
    registry.call("save_workflow", {"name": "w", "params": {"nome": "x"},
                  "steps": [{"tool": "file_write", "args": {"path": "{{nome}}.txt", "content": "oi"}}]})
    registry.call("run_workflow", {"name": "w", "params": {"nome": "y"}})
    assert (config.workspace() / "y.txt").read_text() == "oi"


def test_web_table_extraction_real_browser(monkeypatch):
    monkeypatch.setenv("EMANUSIA_HEADLESS", "1")
    monkeypatch.setattr(config, "HEADLESS", True)
    if os.path.exists("/opt/pw-browsers/chromium"):
        monkeypatch.setenv("EMANUSIA_CHROMIUM", "/opt/pw-browsers/chromium")
    page = config.workspace() / "p.html"
    page.write_text("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    try:
        registry.call("web_open", {"url": page.as_uri()})
        assert registry.call("web_table", {}) == [["A", "B"], ["1", "2"]]
    finally:
        registry.call("web_close", {})
