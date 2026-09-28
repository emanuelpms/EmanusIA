"""Gravador web: você usa o site normalmente e o robô transforma em workflow YAML.
Senhas NUNCA são gravadas: campos password viram web_fill_secret com uma variável de ambiente."""
import re
import threading

import yaml

from . import config
from .tools.workflow import _path

JS = r"""
(() => {
  if (window.__emanusiaRec) return; window.__emanusiaRec = true;
  const q = s => s.replace(/"/g, '\\"');
  function sel(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    for (const a of ['data-testid', 'name', 'aria-label', 'placeholder']) {
      const v = el.getAttribute(a);
      if (v) return el.tagName.toLowerCase() + '[' + a + '="' + q(v) + '"]';
    }
    const t = (el.innerText || '').trim();
    if (['A', 'BUTTON'].includes(el.tagName) && t && t.length < 40 && !t.includes('\n')) return 'text=' + t;
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.body; e = e.parentElement) {
      const sibs = [...e.parentElement.children].filter(c => c.tagName === e.tagName);
      parts.unshift(e.tagName.toLowerCase() + (sibs.length > 1 ? ':nth-of-type(' + (sibs.indexOf(e) + 1) + ')' : ''));
    }
    return 'body > ' + parts.join(' > ');
  }
  const send = e => window.__emanusia_rec(e);
  document.addEventListener('click', ev => {
    const el = ev.target.closest('a,button,input,select,label,[role=button]') || ev.target;
    if (el.tagName === 'INPUT' && !['checkbox', 'radio', 'submit', 'button'].includes(el.type)) return;
    if (el.tagName === 'SELECT') return;
    send({kind: 'click', selector: sel(el)});
  }, true);
  document.addEventListener('input', ev => {
    const el = ev.target;
    if (!['INPUT', 'TEXTAREA'].includes(el.tagName)) return;
    if (['checkbox', 'radio', 'submit', 'button'].includes(el.type)) return;
    send({kind: el.type === 'password' ? 'secret' : 'fill', selector: sel(el), value: el.value,
          name: el.name || el.id || 'CAMPO'});
  }, true);
  document.addEventListener('change', ev => {
    if (ev.target.tagName === 'SELECT') send({kind: 'select', selector: sel(ev.target), value: ev.target.value});
  }, true);
})();
"""


class Recorder:
    def __init__(self, start_url: str):
        self.start_url = start_url
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def attach(self, context):
        context.expose_binding("__emanusia_rec", lambda src, e: self._on(e))
        context.add_init_script(JS)

    def _on(self, e: dict):
        with self._lock:
            last = self.events[-1] if self.events else None
            # digitação vira 1 passo só (mantém o valor final)
            if last and e["kind"] in ("fill", "secret", "select") and last["kind"] == e["kind"] \
                    and last["selector"] == e["selector"]:
                self.events[-1] = e
            else:
                self.events.append(e)

    def steps(self) -> list[dict]:
        steps = [{"tool": "web_open", "args": {"url": self.start_url}}]
        for e in self.events:
            k = e["kind"]
            if k == "click":
                steps.append({"tool": "web_click", "args": {"selector": e["selector"]}})
            elif k in ("fill", "select"):
                steps.append({"tool": "web_fill", "args": {"selector": e["selector"], "value": e["value"]}})
            elif k == "secret":
                var = "SENHA_" + re.sub(r"\W", "_", e["name"]).upper()
                steps.append({"tool": "web_fill_secret",
                              "args": {"selector": e["selector"], "env_var": var}})
        return steps

    def save(self, name: str, description: str = "") -> dict:
        wf = {"name": name, "description": description or f"Gravado de {self.start_url}",
              "params": {}, "steps": self.steps()}
        _path(name).write_text(yaml.safe_dump(wf, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return wf


def record(url: str, name: str, stop: threading.Event) -> dict:
    """Abre um navegador VISÍVEL; grava até `stop` ser acionado ou a janela ser fechada."""
    from playwright.sync_api import sync_playwright
    rec = Recorder(url)
    with sync_playwright() as pw:
        import os
        kw = {"headless": False}
        if os.environ.get("EMANUSIA_CHROMIUM"):
            kw["executable_path"] = os.environ["EMANUSIA_CHROMIUM"]
        browser = pw.chromium.launch(**kw)
        ctx = browser.new_context()
        rec.attach(ctx)
        page = ctx.new_page()
        page.goto(url)
        while not stop.is_set() and browser.is_connected() and page.context.pages:
            try:
                page.wait_for_timeout(300)
            except Exception:
                break
        if browser.is_connected():
            browser.close()
    return rec.save(name)
