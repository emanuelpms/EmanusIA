"""Gravador web: você usa o site normalmente e o robô transforma em workflow YAML.
Senhas NUNCA são gravadas: campos password viram web_fill_secret com uma variável de ambiente."""
import re
import threading

import yaml

from . import config
from .tools.workflow import _path

SELECTOR_JS = r"""
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
  function label(el) {   // nome que a pessoa vê: texto do botão, rótulo do campo, placeholder...
    const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    if (['A', 'BUTTON'].includes(el.tagName) || el.getAttribute('role') === 'button') { const t = clean(el.innerText); if (t) return t; }
    if (el.type === 'submit' || el.type === 'button') return clean(el.value);
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); }
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    return clean(el.placeholder || el.name || el.title || el.innerText || el.tagName.toLowerCase());
  }
"""

JS = r"""
(() => {
  if (window.__emanusiaRec) return; window.__emanusiaRec = true;
""" + SELECTOR_JS + r"""
  function frameChain() {   // em qual quadro interno (iframe) a ação aconteceu
    const chain = []; let w = window;
    while (w !== w.top) {
      let fe = null; try { fe = w.frameElement; } catch (err) {}
      chain.unshift(!fe ? 'iframe' : fe.id ? '#' + CSS.escape(fe.id)
        : fe.name ? 'iframe[name="' + q(fe.name) + '"]' : 'iframe');
      w = w.parent;
    }
    return chain.join(' >> ');
  }
  const send = e => window.__emanusia_rec({...e, frame: frameChain()});
  document.addEventListener('click', ev => {
    const el = ev.target.closest('a,button,input,select,label,[role=button]') || ev.target;
    if (el.tagName === 'INPUT' && !['checkbox', 'radio', 'submit', 'button'].includes(el.type)) return;
    if (el.tagName === 'SELECT') return;
    send({kind: 'click', selector: sel(el), label: label(el)});
  }, true);
  document.addEventListener('input', ev => {
    const el = ev.target;
    if (!['INPUT', 'TEXTAREA'].includes(el.tagName)) return;
    if (['checkbox', 'radio', 'submit', 'button'].includes(el.type)) return;
    send({kind: el.type === 'password' ? 'secret' : 'fill', selector: sel(el), value: el.value,
          name: el.name || el.id || 'CAMPO', label: label(el)});
  }, true);
  document.addEventListener('change', ev => {
    if (ev.target.tagName === 'SELECT') send({kind: 'select', selector: sel(ev.target), value: ev.target.value, label: label(ev.target)});
  }, true);
})();
"""


class Recorder:
    def __init__(self, start_url: str, credential: str = "", username: str = ""):
        self.start_url, self.credential, self.username = start_url, credential, username
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def attach(self, context, pages=()):
        """Grava no contexto (páginas novas) e também nas páginas já abertas (`pages`)."""
        context.expose_binding("__emanusia_rec", lambda src, e: self._on(e))
        context.add_init_script(JS)
        for pg in pages:
            for fr in pg.frames:
                try:
                    fr.evaluate(JS)
                except Exception:
                    pass

    def _on(self, e: dict):
        with self._lock:
            last = self.events[-1] if self.events else None
            # digitação vira 1 passo só (mantém o valor final)
            if last and e["kind"] in ("fill", "secret", "select") and last["kind"] == e["kind"] \
                    and last["selector"] == e["selector"] and last.get("frame") == e.get("frame"):
                self.events[-1] = e
            else:
                self.events.append(e)

    def steps(self, with_open: bool = True) -> list[dict]:
        steps = [{"tool": "web_open", "args": {"url": self.start_url}}] if with_open else []
        frame = ""
        for e in self.events:
            k = e["kind"]
            if e.get("frame", "") != frame:   # entrou/saiu de um quadro interno
                frame = e.get("frame", "")
                steps.append({"tool": "web_frame", "args": {"selector": frame}})
            lab = {"label": e["label"]} if e.get("label") else {}
            if k == "click":
                steps.append({"tool": "web_click", "args": {"selector": e["selector"]}, **lab})
            elif k == "fill" and self.credential and self.username and e["value"] == self.username:
                steps.append({"tool": "web_fill_credential", "args": {"selector": e["selector"],
                              "credential": self.credential, "field": "username"}, **lab})
            elif k == "fill":
                steps.append({"tool": "web_fill", "args": {"selector": e["selector"], "value": e["value"]}, **lab})
            elif k == "select":
                steps.append({"tool": "web_select", "args": {"selector": e["selector"], "value": e["value"]}, **lab})
            elif k == "secret":
                if self.credential:  # modo "ensinar": usa o acesso cadastrado de quem roda o bot
                    steps.append({"tool": "web_fill_credential", "args": {"selector": e["selector"],
                                  "credential": self.credential, "field": "password"}, **lab})
                else:
                    var = "SENHA_" + re.sub(r"\W", "_", e["name"]).upper()
                    steps.append({"tool": "web_fill_secret",
                                  "args": {"selector": e["selector"], "env_var": var}, **lab})
        return steps

    def save(self, name: str, description: str = "") -> dict:
        wf = {"name": name, "description": description or f"Gravado de {self.start_url}",
              "params": {}, "steps": self.steps()}
        _path(name).write_text(yaml.safe_dump(wf, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return wf


def record(url: str, name: str | None, stop: threading.Event, credential: str = "",
           username: str = "") -> dict:
    """Abre um navegador VISÍVEL; grava até `stop` ser acionado ou a janela ser fechada.
    name=None: não salva workflow, só devolve os passos (modo "ensinar", que vira bot direto)."""
    from playwright.sync_api import sync_playwright
    rec = Recorder(url, credential, username)
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
    return rec.save(name) if name else {"params": {}, "steps": rec.steps()}
