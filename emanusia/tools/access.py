"""Logins usando o cofre de acessos (vault.py). O código do bot só conhece o NOME do acesso;
o usuário e a senha vêm do cofre de quem está executando."""
from .. import auth, vault
from .registry import tool


def _cred(name: str) -> dict:
    return vault.resolve(auth.CURRENT_USER.get().name, name)


@tool("list_credentials", "Lista os acessos (sistemas com login) mapeados para o usuário atual.")
def list_credentials():
    return [{"name": c["name"], "label": c["label"], "kind": c["kind"], "url": c["url"]}
            for c in vault.list_credentials(auth.CURRENT_USER.get().name)]


@tool("web_login", "Faz login num site com o acesso mapeado (usuário+senha do cofre). Abre a URL do "
      "acesso (ou `url`), preenche e envia. Seletores opcionais no cadastro do acesso.",
      {"credential": {"type": "string"}, "url": {"type": "string"}}, ("credential",))
def web_login(credential: str, url: str = ""):
    from . import web
    c = _cred(credential)
    ex = c.get("extra", {})
    target = url or c["url"]
    if target:
        web.web_open(target, session=ex.get("session", ""))
    page = web._get_page()
    pwd = web._loc(ex.get("pass_selector") or "input[type=password]")
    pwd.wait_for(state="visible", timeout=web.TIMEOUT_MS)
    user_sel = ex.get("user_selector") or ("input[type=email]:visible, input[type=text]:visible, "
                                           "input:not([type]):visible")
    if c["username"]:
        web._loc(user_sel).fill(c["username"])
    pwd.fill(c["password"])
    if ex.get("submit_selector"):
        web._loc(ex["submit_selector"]).click()
    else:
        pwd.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=web.TIMEOUT_MS)
    except Exception:
        pass
    if ex.get("success_selector"):
        web._loc(ex["success_selector"]).wait_for(state="visible", timeout=web.TIMEOUT_MS)
    if ex.get("session"):
        web.web_save_session(ex["session"])
    return {"logged_in": credential, "url": page.url}


@tool("web_fill_credential", "Preenche um campo com o usuário ou a senha de um acesso mapeado.",
      {"selector": {"type": "string"}, "credential": {"type": "string"},
       "field": {"type": "string", "enum": ["username", "password"]}}, ("selector", "credential"))
def web_fill_credential(selector: str, credential: str, field: str = "password"):
    from . import web
    c = _cred(credential)
    web._loc(selector).fill(c["password"] if field == "password" else c["username"])
    return "ok"


@tool("sap_login", "Faz logon no SAP GUI com o acesso mapeado (mandante/idioma no cadastro).",
      {"credential": {"type": "string"}}, ("credential",))
def sap_login(credential: str):
    from . import sap
    c = _cred(credential)
    s = sap._get_session()
    ex = c.get("extra", {})
    if ex.get("client"):
        s.findById("wnd[0]/usr/txtRSYST-MANDT").text = ex["client"]
    s.findById("wnd[0]/usr/txtRSYST-BNAME").text = c["username"]
    s.findById("wnd[0]/usr/pwdRSYST-BCODE").text = c["password"]
    if ex.get("language"):
        s.findById("wnd[0]/usr/txtRSYST-LANGU").text = ex["language"]
    s.findById("wnd[0]").sendVKey(0)
    return {"logged_in": credential, "status": s.findById("wnd[0]/sbar").text}


@tool("desktop_type_credential", "Digita o usuário ou a senha de um acesso mapeado num programa Windows.",
      {"window": {"type": "string"}, "credential": {"type": "string"},
       "field": {"type": "string", "enum": ["username", "password"]}, "control": {"type": "string"}},
      ("window", "credential"))
def desktop_type_credential(window: str, credential: str, field: str = "password", control: str = ""):
    from . import desktop
    c = _cred(credential)
    w = desktop._win(window)
    (w.child_window(title=control) if control else w).type_keys(
        c["password"] if field == "password" else c["username"], with_spaces=True)
    return "ok"
