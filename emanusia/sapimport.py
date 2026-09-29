"""Ensinar no SAP: converte a gravação do próprio SAP GUI (Alt+F12 → "Gravação e reprodução de
script" → arquivo .vbs) em passos do robô, sem ninguém precisar escrever código.

Exemplo de linhas gravadas pelo SAP:
    session.findById("wnd[0]/tbar[0]/okcd").text = "/nVA03"
    session.findById("wnd[0]").sendVKey 0
    session.findById("wnd[0]/usr/ctxtVBAK-VBELN").text = "12345"
    session.findById("wnd[0]/tbar[1]/btn[8]").press
    session.findById("wnd[0]/usr/chkP_TEST").selected = true
    session.findById("wnd[0]/usr/cntlGRID/shellcont/shell").pressToolbarButton "&MB_EXPORT"
"""
import re

LINE = re.compile(r'findById\("([^"]+)"\)\.(\w+)\s*(?:=\s*(.*?)|\s+(.*?))?\s*$')
IGNORE = {"maximize", "setFocus", "caretPosition", "resizeWorkingPane", "restore", "iconify",
          "firstVisibleRow", "currentCellColumn", "topNode"}
PREFIXES = ("ctxt", "txt", "pwd", "chk", "rad", "cmb", "btn", "tabp", "tabs", "cntl", "lbl", "usr", "sub")
LOGIN_FIELDS = ("RSYST-BNAME", "RSYST-BCODE", "RSYST-MANDT", "RSYST-LANGU")
MAX_SIZE = 300_000


def _value(raw: str | None):
    if raw is None:
        return None
    raw = raw.strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.startswith('"'):
        m = re.match(r'"((?:[^"]|"")*)"', raw)
        return m.group(1).replace('""', '"') if m else raw.strip('"')
    try:
        return int(raw)
    except ValueError:
        return raw


def _args(raw: str | None) -> list:
    if not raw:
        return []
    parts = re.findall(r'"(?:[^"]|"")*"|[^,]+', raw)
    return [_value(p) for p in parts if p.strip()]


def field_label(fid: str) -> str:
    """wnd[0]/usr/ctxtVBAK-VBELN -> VBAK-VBELN ; wnd[0]/tbar[1]/btn[8] -> botão 8 da barra"""
    last = fid.rstrip("/").split("/")[-1]
    m = re.match(r"btn\[(\d+)\]", last)
    if m:
        return {"8": "Executar (F8)", "11": "Gravar", "3": "Voltar", "0": "Confirmar"}.get(m.group(1), f"botão {m.group(1)}")
    for p in PREFIXES:
        if last.startswith(p) and len(last) > len(p):
            return last[len(p):]
    return last


def parse(script: str, credential: str = "") -> dict:
    """Devolve {"code": {params, steps}, "warnings": [...]}"""
    if len(script) > MAX_SIZE:
        raise ValueError("arquivo de gravação grande demais")
    steps, warnings = [], []
    skip_enter = False
    logged_in = False
    for n, line in enumerate(script.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("'") or "findById" not in line:
            continue
        m = LINE.search(line)
        if not m:
            continue
        fid, prop, assigned, call_args = m.group(1), m.group(2), m.group(3), m.group(4)
        value = _value(assigned)
        if prop in IGNORE or fid.endswith("/sbar"):
            continue
        # ---- logon
        if any(f in fid for f in LOGIN_FIELDS):
            if credential and not logged_in:
                steps.append({"tool": "sap_login", "args": {"credential": credential}})
                logged_in, skip_enter = True, True
            elif not credential and "BCODE" in fid:
                warnings.append(f"linha {n}: a senha do SAP não foi importada — escolha um acesso cadastrado")
            skip_enter = True  # o Enter que confirma o logon não vira passo
            continue
        # ---- transação
        if fid.endswith("tbar[0]/okcd") and prop == "text":
            code = str(value or "")
            m2 = re.match(r"/[no]?(\w+)", code, re.I)
            if m2:
                steps.append({"tool": "sap_run_transaction", "args": {"tcode": m2.group(1).upper()}})
                skip_enter = True
            continue
        if prop == "sendVKey":
            key = _args(call_args)[0] if call_args else 0
            if skip_enter and key == 0:
                skip_enter = False
                continue
            steps.append({"tool": "sap_press", "args": {"vkey": int(key)}})
            skip_enter = False
            continue
        skip_enter = False
        label = field_label(fid)
        if prop == "text":
            if "pwd" in fid.split("/")[-1]:
                warnings.append(f"linha {n}: campo de senha ignorado — use um acesso cadastrado")
                continue
            steps.append({"tool": "sap_set_field", "args": {"field_id": fid, "value": "" if value is None else str(value)},
                          "label": label})
        elif prop == "press":
            steps.append({"tool": "sap_press", "args": {"button_id": fid}, "label": label})
        elif prop in ("selected", "key"):
            steps.append({"tool": "sap_select", "args": {"field_id": fid, "value": value}, "label": label})
        elif prop == "select" and assigned is None:
            steps.append({"tool": "sap_select", "args": {"field_id": fid}, "label": label})
        elif assigned is None:  # métodos de grade/árvore: pressToolbarButton, setCurrentCell, doubleClick...
            steps.append({"tool": "sap_call", "args": {"field_id": fid, "method": prop, "args": _args(call_args)},
                          "label": label})
        else:
            warnings.append(f"linha {n}: propriedade “{prop}” não suportada, ignorada")
    if not steps:
        raise ValueError("Não encontrei ações do SAP nesse arquivo. Ele veio do gravador de scripts do SAP (.vbs)?")
    return {"code": {"params": {}, "steps": steps}, "warnings": warnings}
