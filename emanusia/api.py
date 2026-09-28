"""API local + UI mínima. Só escuta em 127.0.0.1. Uma thread por vez (Playwright é thread-bound)."""
from concurrent.futures import ThreadPoolExecutor

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .agent import Agent
from .llm import OllamaClient
from .tools import load_all
from .tools.workflow import _path, list_workflows, run_steps

app = FastAPI(title="EmanusIA")
_pool = ThreadPoolExecutor(max_workers=1)


class Task(BaseModel):
    task: str
    approve_risky: bool = False  # o usuário libera ações de risco explicitamente


class WfRun(BaseModel):
    params: dict = {}
    approve_risky: bool = False


@app.post("/run")
def run(t: Task):
    events: list[dict] = []
    agent = Agent(OllamaClient(), confirm=lambda n, a: t.approve_risky,
                  on_event=lambda k, d: events.append({"kind": k, **d}))
    answer = _pool.submit(agent.run, t.task).result()
    return {"answer": answer, "events": events}


@app.get("/workflows")
def workflows():
    load_all()
    return list_workflows()


@app.post("/workflows/{name}/run")
def run_workflow(name: str, body: WfRun):
    load_all()
    wf = yaml.safe_load(_path(name).read_text(encoding="utf-8"))
    ok = (lambda n, a: True) if body.approve_risky else (lambda n, a: False)
    return _pool.submit(run_steps, wf, body.params, ok).result()


PAGE = """<!doctype html><meta charset=utf-8><title>EmanusIA</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font:16px system-ui;max-width:760px;margin:30px auto;padding:0 16px;background:#0b1220;color:#e6ecf5}
textarea{width:100%;height:110px;background:#131c2e;color:inherit;border:1px solid #22304a;border-radius:10px;padding:10px}
button{background:#4f8cff;border:0;color:#fff;padding:10px 18px;border-radius:10px;cursor:pointer;margin-top:8px}
pre{background:#131c2e;padding:12px;border-radius:10px;white-space:pre-wrap}label{margin-left:12px;color:#8a97ad}</style>
<h1>🤖 EmanusIA</h1><textarea id=t placeholder="Ex.: Abra o site X, baixe a tabela de vendas e gere um dashboard"></textarea><br>
<button onclick=go()>Executar</button><label><input type=checkbox id=ap> autorizar ações de risco</label>
<pre id=o></pre><script>
async function go(){o.textContent='Trabalhando...';
const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({task:t.value,approve_risky:ap.checked})});const j=await r.json();
o.textContent=j.events.filter(e=>e.kind=='tool_call').map(e=>'→ '+e.name).join('\\n')+'\\n\\n'+j.answer}
</script>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
