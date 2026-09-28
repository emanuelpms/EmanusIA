"""Dashboards HTML modernos (KPIs + gráficos Plotly), 100% offline."""
import html
import json

import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from .. import config
from .registry import tool

PALETTE = ["#4f8cff", "#22c7a9", "#ffb020", "#ff6b6b", "#a78bfa", "#38bdf8"]

CSS = """
:root{--bg:#0b1220;--card:#131c2e;--fg:#e6ecf5;--mut:#8a97ad;--line:#22304a}
@media(prefers-color-scheme:light){:root{--bg:#f4f6fb;--card:#fff;--fg:#0f172a;--mut:#64748b;--line:#e2e8f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,Segoe UI,sans-serif;padding:24px}
h1{margin:0 0 4px;font-size:26px}.sub{color:var(--mut);margin-bottom:22px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.kpi .l{color:var(--mut);font-size:13px}.kpi .v{font-size:30px;font-weight:700}
.kpi .d{font-size:13px;color:#22c7a9}.kpi .d.neg{color:#ff6b6b}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px}
@media(max-width:520px){.charts{grid-template-columns:1fr}}
"""


def _figure(spec: dict) -> go.Figure:
    kind = spec.get("type", "bar")
    x, series = spec["x"], spec["series"]  # series: {"Nome": [valores]}
    fig = go.Figure()
    for i, (name, ys) in enumerate(series.items()):
        color = PALETTE[i % len(PALETTE)]
        if kind == "pie":
            fig.add_trace(go.Pie(labels=x, values=ys, hole=.55, marker=dict(colors=PALETTE)))
            break
        if kind == "line":
            fig.add_trace(go.Scatter(x=x, y=ys, name=name, mode="lines+markers",
                                     line=dict(color=color, width=3)))
        elif kind == "area":
            fig.add_trace(go.Scatter(x=x, y=ys, name=name, fill="tozeroy", line=dict(color=color)))
        else:
            fig.add_trace(go.Bar(x=x, y=ys, name=name, marker_color=color))
    fig.update_layout(title=spec.get("title", ""), template=None, height=360,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#8a97ad"), margin=dict(l=40, r=20, t=50, b=40),
                      legend=dict(orientation="h", y=-0.2))
    fig.update_xaxes(gridcolor="rgba(138,151,173,.15)")
    fig.update_yaxes(gridcolor="rgba(138,151,173,.15)")
    return fig


@tool("dashboard_build",
      "Gera dashboard HTML. kpis=[{label,value,delta?}]. charts=[{type: bar|line|area|pie, "
      "title, x:[...], series:{\"Nome\":[...]}}].",
      {"path": {"type": "string"}, "title": {"type": "string"}, "subtitle": {"type": "string"},
       "kpis": {"type": "array"}, "charts": {"type": "array"}}, ("path", "title"))
def dashboard_build(path: str, title: str, subtitle: str = "", kpis: list | None = None,
                    charts: list | None = None):
    e = html.escape
    kpi_html = "".join(
        f'<div class="card kpi"><div class="l">{e(str(k["label"]))}</div>'
        f'<div class="v">{e(str(k["value"]))}</div>'
        + (f'<div class="d {"neg" if str(k["delta"]).startswith("-") else ""}">{e(str(k["delta"]))}</div>'
           if k.get("delta") else "") + "</div>" for k in kpis or [])
    divs, scripts = [], []
    for i, c in enumerate(charts or []):
        fig = _figure(c)
        divs.append(f'<div class="card"><div id="c{i}"></div></div>')
        payload = json.dumps(fig.to_json()).replace("</", "<\\/")
        scripts.append(f'(function(){{var f=JSON.parse({payload});'
                       f'Plotly.newPlot("c{i}",f.data,f.layout,{{responsive:true,displaylogo:false}});}})();')
    doc = (f'<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{e(title)}</title><style>{CSS}</style><body><h1>{e(title)}</h1>'
           f'<div class="sub">{e(subtitle)}</div><div class="kpis">{kpi_html}</div>'
           f'<div class="charts">{"".join(divs)}</div>'
           f'<script>{get_plotlyjs()}</script><script>{"".join(scripts)}</script></body></html>')
    config.safe_path(path).write_text(doc, encoding="utf-8")
    return {"path": path, "charts": len(charts or []), "kpis": len(kpis or [])}
