import os

import pytest
import yaml

from emanusia import config
from emanusia.tools import registry
from emanusia.tools.workflow import render, run_steps


def test_path_traversal_blocked():
    with pytest.raises(PermissionError):
        config.safe_path("../fora.txt")


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


def test_web_table_extraction_real_browser(headless):
    page = config.workspace() / "p.html"
    page.write_text("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    try:
        registry.call("web_open", {"url": page.as_uri()})
        assert registry.call("web_table", {}) == [["A", "B"], ["1", "2"]]
    finally:
        registry.call("web_close", {})
