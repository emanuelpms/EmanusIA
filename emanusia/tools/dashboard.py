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


# ============================================================================ dashboard a partir de dados
DATA_CSS = """
:root{--bg:#f4f6fb;--card:#fff;--fg:#0f172a;--mut:#64748b;--line:#e2e8f0;--acc:#2f6db5}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#131c2e;--fg:#e6ecf5;--mut:#8a97ad;--line:#22304a;--acc:#4f8cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Segoe UI",sans-serif}
header{padding:20px 24px 8px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:end}
h1{margin:0;font-size:24px}.sub{color:var(--mut)}main{padding:8px 24px 24px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:end;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:14px}
.bar label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:3px}
select,input,button{font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
button{cursor:pointer;background:var(--acc);color:#fff;border:0}button.sec{background:var(--line);color:var(--fg)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.kpi{border-top:4px solid var(--c)}.kpi .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.kpi .v{font-size:26px;font-weight:700}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr));gap:12px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{cursor:pointer;color:var(--mut);position:sticky;top:0;background:var(--card)}.tw{max-height:420px;overflow:auto}
.num{text-align:right;font-variant-numeric:tabular-nums}
"""

DATA_JS = r"""
const D=__DATA__, C=__CFG__, PAL=__PAL__;
const $=s=>document.querySelector(s), fmtN=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:2});
const FMT={money:v=>v.toLocaleString('pt-BR',{style:'currency',currency:'BRL'}),percent:v=>(v*100).toFixed(1)+'%',int:v=>Math.round(v).toLocaleString('pt-BR')};
const fmt=(v,f)=>v==null||isNaN(v)?'—':(FMT[f]||fmtN.format)(v);
const num=v=>typeof v==='number'?v:parseFloat(String(v).replace(/[R$\s.]/g,'').replace(',','.'));
function agg(vals,fn){const n=vals.map(num).filter(x=>!isNaN(x));
  switch(fn){case'count':return vals.length;case'nunique':return new Set(vals).size;case'mean':return n.length?n.reduce((a,b)=>a+b,0)/n.length:NaN;
  case'max':return Math.max(...n);case'min':return Math.min(...n);default:return n.reduce((a,b)=>a+b,0);}}
function filtered(){return D.rows.filter(r=>{
  for(const f of C.filters){const v=$('#f_'+f.i).value;if(v&&String(r[f.i])!==v)return false;}
  if(C.date!=null){const d=String(r[C.date]).slice(0,10),a=$('#d_from').value,b=$('#d_to').value;if(a&&d<a)return false;if(b&&d>b)return false;}
  const q=$('#q').value.toLowerCase();if(q&&!r.some(v=>String(v).toLowerCase().includes(q)))return false;return true;});}
const dark=matchMedia('(prefers-color-scheme:dark)').matches, axis={gridcolor:dark?'#22304a':'#e2e8f0',color:dark?'#8a97ad':'#64748b'};
let sortCol=null,sortDesc=true;
function render(){
  const rows=filtered();
  C.kpis.forEach((k,i)=>{$('#k'+i).textContent=fmt(agg(rows.map(r=>r[k.i]),k.agg),k.format);});
  C.charts.forEach((c,i)=>{
    const g=new Map();for(const r of rows){let key=r[c.x];if(c.period==='month')key=String(key).slice(0,7);if(!g.has(key))g.set(key,[]);g.get(key).push(r[c.y]);}
    let e=[...g.entries()].map(([k,v])=>[k,agg(v,c.agg)]);
    if(c.type==='line'||c.type==='area'||c.period)e.sort((a,b)=>String(a[0]).localeCompare(String(b[0])));else e.sort((a,b)=>b[1]-a[1]);
    e=e.slice(0,c.top||15);const x=e.map(p=>p[0]),y=e.map(p=>p[1]);
    const t=c.type==='pie'?[{type:'pie',labels:x,values:y,hole:.55,marker:{colors:PAL}}]
      :c.type==='line'||c.type==='area'?[{type:'scatter',mode:'lines+markers',x,y,fill:c.type==='area'?'tozeroy':undefined,line:{color:PAL[i%PAL.length],width:3}}]
      :[{type:'bar',x:c.horizontal?y:x,y:c.horizontal?x:y,orientation:c.horizontal?'h':'v',marker:{color:PAL[i%PAL.length]}}];
    Plotly.react('c'+i,t,{title:{text:c.title,font:{size:14}},height:340,margin:{l:50,r:16,t:44,b:60},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
      font:{color:axis.color},xaxis:{...axis,type:c.horizontal?'-':'category',automargin:true},yaxis:{...axis,type:c.horizontal?'category':'-',autorange:c.horizontal?'reversed':true,automargin:true},
      showlegend:c.type==='pie'},{responsive:true,displaylogo:false});});
  let tr=rows.slice();if(sortCol!=null)tr.sort((a,b)=>{const x=num(a[sortCol]),y=num(b[sortCol]);const r=!isNaN(x)&&!isNaN(y)?x-y:String(a[sortCol]).localeCompare(String(b[sortCol]));return sortDesc?-r:r;});
  $('#tb').innerHTML=tr.slice(0,200).map(r=>'<tr>'+r.map(v=>`<td class="${typeof v==='number'?'num':''}">${typeof v==='number'?fmtN.format(v):String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</td>`).join('')+'</tr>').join('');
  $('#cnt').textContent=`${rows.length.toLocaleString('pt-BR')} de ${D.rows.length.toLocaleString('pt-BR')} registros`+(rows.length>200?' (tabela mostra 200)':'');
}
function exportCsv(){const rows=filtered(),esc=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const csv='﻿'+[D.cols,...rows].map(r=>r.map(esc).join(';')).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='dados_filtrados.csv';a.click();}
document.querySelectorAll('select,input').forEach(e=>e.addEventListener('input',render));
document.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{sortDesc=sortCol===i?!sortDesc:true;sortCol=i;render();});
$('#clear').onclick=()=>{document.querySelectorAll('.bar select,.bar input').forEach(e=>e.value='');render();};
$('#csv').onclick=exportCsv;render();
"""


@tool("dashboard_data",
      "Dashboard HTML COMPLETO e interativo a partir de uma base (planilha/CSV/linhas): filtros por coluna, "
      "período, busca, KPIs e gráficos que recalculam ao filtrar, tabela ordenável e exportação CSV. "
      "kpis=[{label, column, agg: sum|mean|count|max|min|nunique, format: money|int|percent}], "
      "charts=[{type: bar|line|pie|area, x, y, agg, title, top, period: month, horizontal}], "
      "filters=[colunas], date_column.",
      {"path": {"type": "string"}, "source": {}, "sheet": {"type": "string"}, "title": {"type": "string"},
       "subtitle": {"type": "string"}, "kpis": {"type": "array"}, "charts": {"type": "array"},
       "filters": {"type": "array"}, "date_column": {"type": "string"}}, ("path", "source", "title"))
def dashboard_data(path: str, source, title: str, sheet: str | None = None, subtitle: str = "",
                   kpis: list | None = None, charts: list | None = None, filters: list | None = None,
                   date_column: str = ""):
    import pandas as pd
    from .data import _agg, _clean_val, _col, load
    df = load(source, sheet)
    if len(df) > 100_000:
        raise ValueError("Base grande demais para dashboard HTML (máx. 100 mil linhas): agregue antes com data_query")
    if date_column:
        dc = _col(df, date_column)
        df[dc] = pd.to_datetime(df[dc], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
    cols = [str(c) for c in df.columns]
    idx = {c: i for i, c in enumerate(cols)}
    I = lambda c: idx[str(_col(df, c))]
    cfg = {"kpis": [{"i": I(k["column"]), "agg": _agg(k.get("agg", "sum")), "format": k.get("format", ""),
                     "label": k.get("label", k["column"])} for k in kpis or []],
           "charts": [{"x": I(c["x"]), "y": I(c.get("y") or c["x"]), "agg": _agg(c.get("agg", "sum" if c.get("y") else "count")),
                       "type": c.get("type", "bar"), "title": c.get("title") or f"{c.get('y') or 'Quantidade'} por {c['x']}",
                       "top": int(c.get("top", 15)), "period": c.get("period"), "horizontal": bool(c.get("horizontal"))}
                      for c in charts or []],
           "filters": [{"i": I(f), "name": str(_col(df, f))} for f in filters or []],
           "date": I(date_column) if date_column else None}
    data = {"cols": cols, "rows": [[_clean_val(v) for v in r] for r in df.itertuples(index=False)]}
    e = html.escape
    opts = lambda f: "".join(f'<option value="{e(str(v))}">{e(str(v))}</option>'
                             for v in sorted(df[f["name"]].dropna().astype(str).unique())[:500])
    bar = "".join(f'<label>{e(f["name"])}<select id="f_{f["i"]}"><option value="">Todos</option>{opts(f)}</select></label>'
                  for f in cfg["filters"])
    if date_column:
        bar += '<label>De<input type="date" id="d_from"></label><label>Até<input type="date" id="d_to"></label>'
    bar += ('<label>Buscar<input id="q" placeholder="texto..."></label><button class="sec" id="clear">Limpar</button>'
            '<button id="csv">Exportar CSV</button><span class="sub" id="cnt"></span>')
    kp = "".join(f'<div class="card kpi" style="--c:{PALETTE[i % len(PALETTE)]}"><div class="l">{e(k["label"])}</div>'
                 f'<div class="v" id="k{i}">—</div></div>' for i, k in enumerate(cfg["kpis"]))
    ch = "".join(f'<div class="card"><div id="c{i}"></div></div>' for i in range(len(cfg["charts"])))
    js = (DATA_JS.replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
          .replace("__CFG__", json.dumps(cfg, ensure_ascii=False)).replace("__PAL__", json.dumps(PALETTE)))
    js = js.replace("</", "<\\/")
    doc = (f'<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{e(title)}</title><style>{DATA_CSS}</style><body><header><div><h1>{e(title)}</h1>'
           f'<div class="sub">{e(subtitle or f"{len(df)} registros")}</div></div></header><main>'
           f'<div class="bar">{bar}</div><div class="kpis">{kp}</div><div class="charts">{ch}</div>'
           f'<div class="card"><div class="tw"><table><thead><tr>{"".join(f"<th>{e(c)}</th>" for c in cols)}</tr></thead>'
           f'<tbody id="tb"></tbody></table></div></div></main>'
           f'<script>{get_plotlyjs()}</script><script>{js}</script></body></html>')
    config.safe_path(path).write_text(doc, encoding="utf-8")
    return {"path": path, "rows": len(df), "kpis": len(cfg["kpis"]), "charts": len(cfg["charts"]),
            "filters": len(cfg["filters"])}
