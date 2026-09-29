"""Automação web via Playwright, pensada para sistemas web grandes (ERPs, portais, SPAs).

- Uma sessão de navegador por processo; várias abas (pop-ups entram na lista sozinhos).
- iframes: `web_frame` escolhe o frame alvo (aninhado: "iframe#a >> iframe#b").
- Esperas explícitas (`web_wait`), checagem (`web_exists`) para usar em if/while dos bots.
- Extração estruturada de listas/cartões (`web_extract`) e tabelas (`web_table`).
- Sessão persistente (`web_save_session` / `web_open session=`): faça login (inclusive MFA) uma vez,
  salve os cookies e os bots reaproveitam. Fica em data/web_sessions (fora do workspace).
- Senhas: `web_fill_secret` lê de variável de ambiente; o LLM e o YAML nunca veem a senha.
Seletores: CSS, "text=Entrar", "role=button[name='Salvar']", "xpath=//td[2]". Havendo mais de um
elemento, usa o primeiro visível na ordem do DOM: prefira seletores específicos."""
import os
import re

from .. import config
from .registry import tool

_pw = _browser = _ctx = None
_pages: list = []
_cur = 0
_frames: list[str] = []
_dialog = {"action": "accept", "text": None}
TIMEOUT_MS = 20000


def _session_file(name: str):
    if not re.fullmatch(r"[\w\-]+", name or ""):
        raise ValueError("nome de sessão inválido")
    d = config.data_dir() / "web_sessions"
    d.mkdir(exist_ok=True)
    return d / f"{name}.json"


def _on_dialog(d):
    if _dialog["action"] == "dismiss":
        d.dismiss()
    else:
        d.accept(_dialog["text"]) if _dialog["text"] is not None else d.accept()


def _track(page):
    page.set_default_timeout(TIMEOUT_MS)
    page.on("dialog", _on_dialog)
    _pages.append(page)


def _start(session: str | None = None):
    global _pw, _browser, _ctx, _cur
    from playwright.sync_api import sync_playwright
    if _pw is None:
        _pw = sync_playwright().start()
        kw = {"headless": config.HEADLESS}
        if os.environ.get("EMANUSIA_CHROMIUM"):  # ex.: /opt/pw-browsers/chromium
            kw["executable_path"] = os.environ["EMANUSIA_CHROMIUM"]
        _browser = _pw.chromium.launch(**kw)
    if _ctx is not None:
        _ctx.close()
    _pages.clear()
    _frames.clear()
    state = None
    if session:
        f = _session_file(session)
        state = str(f) if f.exists() else None
    _ctx = _browser.new_context(accept_downloads=True, storage_state=state,
                                viewport={"width": 1366, "height": 850}, locale="pt-BR")
    _ctx.on("page", lambda p: _track(p) if p not in _pages else None)  # pop-ups/novas abas
    _new_page()
    _cur = 0


def _new_page():
    p = _ctx.new_page()
    if p not in _pages:
        _track(p)
    return p


def _get_page():
    if _ctx is None or not _pages:
        _start()
    live = [p for p in _pages if not p.is_closed()]
    if len(live) != len(_pages):
        _pages[:] = live
        if not _pages:
            _new_page()
    return _pages[min(_cur, len(_pages) - 1)]


def _root():
    base = _get_page()
    for f in _frames:
        base = base.frame_locator(f)
    return base


def _loc(selector: str):
    return _root().locator(selector).first


def _ms(timeout_s) -> int:
    return int(float(timeout_s) * 1000) if timeout_s else TIMEOUT_MS


_DESTRUCTIVE = re.compile(r"excluir|deletar|apagar|remover|pagar|pagamento|enviar|confirmar|aprovar|"
                          r"finalizar|transferir|submit|delete|remove|pay|send|approve|confirm|"
                          r"checkout|purchase|transfer", re.I)


# ---------------------------------------------------------------- navegação
@tool("web_open", "Abre uma URL. session = nome de sessão salva (reaproveita login). "
      "new_tab abre em outra aba.",
      {"url": {"type": "string"}, "session": {"type": "string"}, "new_tab": {"type": "boolean"}},
      ("url",))
def web_open(url: str, session: str = "", new_tab: bool = False):
    global _cur
    if session:
        _start(session)
    if new_tab:
        _get_page()
        _cur = _pages.index(_new_page())
    p = _get_page()
    _frames.clear()
    p.goto(url, wait_until="domcontentloaded")
    return {"url": p.url, "title": p.title(), "tab": _cur}


@tool("web_save_session", "Salva cookies/login atuais com um nome, para reusar em web_open(session=).",
      {"name": {"type": "string"}}, ("name",))
def web_save_session(name: str):
    _get_page()
    _ctx.storage_state(path=str(_session_file(name)))
    return {"saved": name}


@tool("web_back", "Volta para a página anterior.")
def web_back():
    _get_page().go_back()
    return {"url": _get_page().url}


@tool("web_tabs", "Lista as abas abertas (índice, título, url).")
def web_tabs():
    _get_page()
    return [{"index": i, "title": p.title(), "url": p.url, "current": i == _cur}
            for i, p in enumerate(_pages)]


@tool("web_switch_tab", "Muda para a aba de índice N (-1 = a mais recente, ex.: pop-up).",
      {"index": {"type": "integer"}}, ("index",))
def web_switch_tab(index: int):
    global _cur
    _get_page()
    _cur = index % len(_pages)
    _frames.clear()
    _pages[_cur].bring_to_front()
    return {"tab": _cur, "url": _pages[_cur].url}


@tool("web_frame", "Define o iframe onde as próximas ações acontecem (vazio = página principal). "
      "Aninhados: 'iframe#a >> iframe#b'.", {"selector": {"type": "string"}})
def web_frame(selector: str = ""):
    _frames[:] = [s.strip() for s in selector.split(">>") if s.strip()]
    return {"frames": list(_frames)}


@tool("web_on_dialog", "Define o que fazer com alertas/confirmações do site (accept|dismiss).",
      {"action": {"type": "string", "enum": ["accept", "dismiss"]}, "prompt_text": {"type": "string"}},
      ("action",))
def web_on_dialog(action: str, prompt_text: str | None = None):
    _dialog.update(action=action, text=prompt_text)
    return dict(_dialog)


# ---------------------------------------------------------------- interação
@tool("web_click", "Clica em um elemento. new_tab=true se o clique abre outra aba/pop-up (espera e "
      "muda para ela). Cliques que excluem/pagam/enviam/aprovam exigem autorização.",
      {"selector": {"type": "string"}, "timeout_s": {"type": "number"}, "new_tab": {"type": "boolean"}},
      ("selector",), risky=lambda a: bool(_DESTRUCTIVE.search(str(a.get("selector", "")))))
def web_click(selector: str, timeout_s: float = 0, new_tab: bool = False):
    global _cur
    if not new_tab:
        _loc(selector).click(timeout=_ms(timeout_s))
        return "ok"
    _get_page()
    with _ctx.expect_page(timeout=_ms(timeout_s)) as info:
        _loc(selector).click(timeout=_ms(timeout_s))
    page = info.value
    if page not in _pages:
        _track(page)
    page.wait_for_load_state("domcontentloaded")
    _cur, _frames[:] = _pages.index(page), []
    return {"tab": _cur, "url": page.url}


@tool("web_fill", "Preenche um campo de formulário (apaga o conteúdo anterior).",
      {"selector": {"type": "string"}, "value": {"type": "string"}}, ("selector", "value"))
def web_fill(selector: str, value):
    _loc(selector).fill(str(value))
    return "ok"


@tool("web_fill_secret", "Preenche um campo com o valor de uma variável de ambiente (senhas).",
      {"selector": {"type": "string"}, "env_var": {"type": "string"}}, ("selector", "env_var"))
def web_fill_secret(selector: str, env_var: str):
    if env_var not in os.environ:
        raise KeyError(f"Variável de ambiente {env_var} não definida")
    _loc(selector).fill(os.environ[env_var])
    return "ok"


@tool("web_type", "Digita tecla a tecla (para campos com máscara/autocomplete).",
      {"selector": {"type": "string"}, "text": {"type": "string"}, "delay_ms": {"type": "integer"}},
      ("selector", "text"))
def web_type(selector: str, text, delay_ms: int = 50):
    _loc(selector).press_sequentially(str(text), delay=delay_ms)
    return "ok"


@tool("web_select", "Escolhe opção de um <select> (por valor ou texto visível).",
      {"selector": {"type": "string"}, "value": {"type": "string"}}, ("selector", "value"))
def web_select(selector: str, value):
    loc = _loc(selector)
    try:
        loc.select_option(value=str(value), timeout=3000)
    except Exception:
        loc.select_option(label=str(value))
    return "ok"


@tool("web_check", "Marca/desmarca checkbox ou radio.",
      {"selector": {"type": "string"}, "checked": {"type": "boolean"}}, ("selector",))
def web_check(selector: str, checked: bool = True):
    _loc(selector).set_checked(bool(checked))
    return "ok"


@tool("web_press", "Pressiona tecla(s): Enter, Tab, Escape, Control+A... (no campo, se informado).",
      {"key": {"type": "string"}, "selector": {"type": "string"}}, ("key",))
def web_press(key: str, selector: str = ""):
    if selector:
        _loc(selector).press(key)
    else:
        _get_page().keyboard.press(key)
    return "ok"


@tool("web_hover", "Passa o mouse sobre um elemento (abre menus suspensos).",
      {"selector": {"type": "string"}}, ("selector",))
def web_hover(selector: str):
    _loc(selector).hover()
    return "ok"


@tool("web_upload", "Envia arquivo(s) do workspace em um <input type=file>.",
      {"selector": {"type": "string"}, "paths": {"type": "array"}}, ("selector", "paths"))
def web_upload(selector: str, paths):
    files = [str(config.safe_path(p)) for p in ([paths] if isinstance(paths, str) else paths)]
    _loc(selector).set_input_files(files)
    return {"uploaded": len(files)}


@tool("web_scroll", "Rola a página (to: bottom|top) ou até um elemento; útil em listas infinitas.",
      {"to": {"type": "string"}, "selector": {"type": "string"}})
def web_scroll(to: str = "bottom", selector: str = ""):
    if selector:
        _loc(selector).scroll_into_view_if_needed()
    else:
        _get_page().evaluate("t => window.scrollTo(0, t === 'top' ? 0 : document.body.scrollHeight)", to)
    return "ok"


@tool("web_eval", "Executa JavaScript na página e retorna o resultado (uso avançado).",
      {"script": {"type": "string"}}, ("script",), risky=True)
def web_eval(script: str):
    return _get_page().evaluate(script)


# ---------------------------------------------------------------- espera e leitura
@tool("web_wait", "Espera um elemento (state: visible|hidden|attached|detached), um texto na página, "
      "ou a URL conter um trecho. Sem nada, espera a rede ficar ociosa.",
      {"selector": {"type": "string"}, "state": {"type": "string"}, "text": {"type": "string"},
       "url_contains": {"type": "string"}, "timeout_s": {"type": "number"}})
def web_wait(selector: str = "", state: str = "visible", text: str = "", url_contains: str = "",
             timeout_s: float = 0):
    p, t = _get_page(), _ms(timeout_s)
    if selector:
        _loc(selector).wait_for(state=state, timeout=t)
    if text:
        _root().get_by_text(text).first.wait_for(timeout=t)
    if url_contains:
        p.wait_for_url(re.compile(".*" + re.escape(url_contains) + ".*"), timeout=t)
    if not (selector or text or url_contains):
        p.wait_for_load_state("networkidle", timeout=t)
    return "ok"


@tool("web_exists", "Retorna true/false se o elemento existe e está visível (ideal para if/while).",
      {"selector": {"type": "string"}, "timeout_s": {"type": "number"}}, ("selector",))
def web_exists(selector: str, timeout_s: float = 0):
    loc = _loc(selector)
    if timeout_s:
        try:
            loc.wait_for(state="visible", timeout=_ms(timeout_s))
        except Exception:
            return False
    return loc.count() > 0 and loc.is_visible()


@tool("web_count", "Quantos elementos casam com o seletor.", {"selector": {"type": "string"}},
      ("selector",))
def web_count(selector: str):
    return _root().locator(selector).count()


@tool("web_text", "Texto visível da página (ou de um seletor).", {"selector": {"type": "string"}})
def web_text(selector: str = "body"):
    return _loc(selector).inner_text()[:8000]


@tool("web_attr", "Lê um atributo (href, value, src...) de um elemento.",
      {"selector": {"type": "string"}, "name": {"type": "string"}}, ("selector", "name"))
def web_attr(selector: str, name: str):
    if name == "value":
        return _loc(selector).input_value()
    return _loc(selector).get_attribute(name)


@tool("web_table", "Extrai uma <table> como lista de linhas (a primeira é o cabeçalho).",
      {"selector": {"type": "string"}})
def web_table(selector: str = "table"):
    return _loc(selector).evaluate("""t => [...t.rows].map(
        r => [...r.cells].map(c => c.innerText.trim()))""")


@tool("web_extract", "Extrai uma lista de itens repetidos (cards, linhas, resultados). "
      "fields = {campo: 'seletor dentro do item'} ou 'seletor@atributo'; '' = texto do item.",
      {"item_selector": {"type": "string"}, "fields": {"type": "object"}, "limit": {"type": "integer"}},
      ("item_selector", "fields"))
def web_extract(item_selector: str, fields: dict, limit: int = 500):
    return _root().locator(item_selector).evaluate_all("""(els, [fields, limit]) =>
      els.slice(0, limit).map(el => Object.fromEntries(Object.entries(fields).map(([k, spec]) => {
        const [sel, attr] = spec.split('@');
        const t = sel ? el.querySelector(sel) : el;
        if (!t) return [k, null];
        return [k, attr ? t.getAttribute(attr) : t.innerText.trim()];
      })))""", [fields, int(limit)])


@tool("web_screenshot", "Salva screenshot da página no workspace.", {"path": {"type": "string"}})
def web_screenshot(path: str = "screenshot.png"):
    _get_page().screenshot(path=str(config.safe_path(path)), full_page=True)
    return {"path": path}


@tool("web_download", "Clica num link/botão que baixa um arquivo e salva no workspace.",
      {"selector": {"type": "string"}, "path": {"type": "string"}}, ("selector", "path"))
def web_download(selector: str, path: str):
    p = _get_page()
    with p.expect_download() as d:
        _loc(selector).click()
    d.value.save_as(str(config.safe_path(path)))
    return {"path": path}


@tool("web_close", "Fecha o navegador.")
def web_close():
    global _pw, _browser, _ctx, _cur
    if _browser:
        _browser.close()
        _pw.stop()
    _pw = _browser = _ctx = None
    _pages.clear()
    _frames.clear()
    _cur = 0
    return "ok"
