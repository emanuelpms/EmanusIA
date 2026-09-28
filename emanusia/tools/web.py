"""Automação web via Playwright. Uma sessão de navegador persistente por processo.
Login: use `web_fill_secret` (lê a senha de variável de ambiente; o LLM nunca a vê)."""
import os
import re

from .. import config
from .registry import tool

_pw = _browser = _page = None


def _get_page():
    global _pw, _browser, _page
    if _page is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        kw = {"headless": config.HEADLESS}
        exe = os.environ.get("EMANUSIA_CHROMIUM")  # ex.: /opt/pw-browsers/chromium
        if exe:
            kw["executable_path"] = exe
        _browser = _pw.chromium.launch(**kw)
        _page = _browser.new_context(accept_downloads=True).new_page()
        _page.set_default_timeout(15000)
    return _page


@tool("web_open", "Abre uma URL no navegador.", {"url": {"type": "string"}}, ("url",))
def web_open(url: str):
    p = _get_page()
    p.goto(url)
    return {"url": p.url, "title": p.title()}


_DESTRUCTIVE = re.compile(r"excluir|deletar|apagar|remover|pagar|pagamento|enviar|confirmar|aprovar|"
                          r"finalizar|submit|delete|remove|pay|send|approve|confirm|checkout|purchase",
                          re.I)


@tool("web_click", "Clica em um elemento (seletor CSS ou text=...). Cliques em botões que "
      "excluem/pagam/enviam/aprovam exigem autorização.",
      {"selector": {"type": "string"}}, ("selector",),
      risky=lambda a: bool(_DESTRUCTIVE.search(a.get("selector", ""))))
def web_click(selector: str):
    _get_page().click(selector)
    return "ok"


@tool("web_fill", "Preenche um campo de formulário.",
      {"selector": {"type": "string"}, "value": {"type": "string"}}, ("selector", "value"))
def web_fill(selector: str, value: str):
    _get_page().fill(selector, value)
    return "ok"


@tool("web_fill_secret", "Preenche um campo com o valor de uma variável de ambiente (senhas).",
      {"selector": {"type": "string"}, "env_var": {"type": "string"}}, ("selector", "env_var"))
def web_fill_secret(selector: str, env_var: str):
    if env_var not in os.environ:
        raise KeyError(f"Variável de ambiente {env_var} não definida")
    _get_page().fill(selector, os.environ[env_var])
    return "ok"


@tool("web_text", "Retorna o texto visível da página (ou de um seletor).",
      {"selector": {"type": "string"}})
def web_text(selector: str = "body"):
    return _get_page().inner_text(selector)[:8000]


@tool("web_table", "Extrai uma <table> como lista de linhas (a primeira é o cabeçalho).",
      {"selector": {"type": "string"}})
def web_table(selector: str = "table"):
    return _get_page().eval_on_selector(selector, """t => [...t.rows].map(
        r => [...r.cells].map(c => c.innerText.trim()))""")


@tool("web_screenshot", "Salva screenshot da página no workspace.", {"path": {"type": "string"}})
def web_screenshot(path: str = "screenshot.png"):
    _get_page().screenshot(path=str(config.safe_path(path)), full_page=True)
    return {"path": path}


@tool("web_download", "Clica num link/botão que baixa um arquivo e salva no workspace.",
      {"selector": {"type": "string"}, "path": {"type": "string"}}, ("selector", "path"))
def web_download(selector: str, path: str):
    p = _get_page()
    with p.expect_download() as d:
        p.click(selector)
    d.value.save_as(str(config.safe_path(path)))
    return {"path": path}


@tool("web_close", "Fecha o navegador.")
def web_close():
    global _pw, _browser, _page
    if _browser:
        _browser.close()
        _pw.stop()
    _pw = _browser = _page = None
    return "ok"
