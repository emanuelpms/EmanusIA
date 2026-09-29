"""SAP via SAP GUI Scripting (somente Windows, SAP Logon aberto e scripting habilitado no servidor
e no cliente: RZ11 sapgui/user_scripting = TRUE). IDs de campo: use o gravador de scripts do SAP
(Alt+F12 > Gravar) para descobrir, ex.: 'wnd[0]/usr/ctxtRF02D-KUNNR'."""
import sys

from .registry import tool

_session = None


def _get_session():
    global _session
    if _session is None:
        if sys.platform != "win32":
            raise RuntimeError("SAP GUI Scripting só está disponível no Windows.")
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()  # COM precisa ser inicializado na thread do worker
        engine = win32com.client.GetObject("SAPGUI").GetScriptingEngine
        _session = engine.Children(0).Children(0)
    return _session


def _wnd(s):
    return s.findById("wnd[0]")


@tool("sap_run_transaction", "Abre uma transação SAP (ex.: VA01, MM03, FB60).",
      {"tcode": {"type": "string"}}, ("tcode",))
def sap_run_transaction(tcode: str):
    s = _get_session()
    s.findById("wnd[0]/tbar[0]/okcd").text = f"/n{tcode}"
    _wnd(s).sendVKey(0)
    return {"status": s.findById("wnd[0]/sbar").text}


@tool("sap_set_field", "Preenche um campo pelo ID do SAP GUI.",
      {"field_id": {"type": "string"}, "value": {"type": "string"}}, ("field_id", "value"),
      risky=True)
def sap_set_field(field_id: str, value: str):
    _get_session().findById(field_id).text = value
    return "ok"


@tool("sap_read_field", "Lê o texto de um campo/label pelo ID.", {"field_id": {"type": "string"}},
      ("field_id",))
def sap_read_field(field_id: str):
    return _get_session().findById(field_id).text


@tool("sap_press", "Pressiona botão/tecla. vkey: 0=Enter, 8=F8 (executar), 11=Ctrl+S (salvar), "
      "3=F3 (voltar). Ou informe button_id.",
      {"vkey": {"type": "integer"}, "button_id": {"type": "string"}}, risky=True)
def sap_press(vkey: int | None = None, button_id: str | None = None):
    s = _get_session()
    if button_id:
        s.findById(button_id).press()
    else:
        _wnd(s).sendVKey(vkey if vkey is not None else 0)
    return {"status": s.findById("wnd[0]/sbar").text}


@tool("sap_read_grid", "Lê um ALV Grid (ex.: resultado de relatório) como linhas com cabeçalho.",
      {"grid_id": {"type": "string"}, "max_rows": {"type": "integer"}}, ("grid_id",))
def sap_read_grid(grid_id: str, max_rows: int = 1000):
    g = _get_session().findById(grid_id)
    cols = list(g.ColumnOrder)
    rows = [[g.GetCellValue(r, c) for c in cols] for r in range(min(g.RowCount, max_rows))]
    return [[g.GetColumnTitleFromName(c) if hasattr(g, "GetColumnTitleFromName") else c
             for c in cols]] + rows
