"""RPA de desktop para qualquer software Windows (Excel, ERPs, apps legados) via pywinauto/pyautogui.
Prefira UI Automation (desktop_click por nome de controle); use coordenadas só como último recurso."""
import sys

from .registry import tool


def _need_windows():
    if sys.platform != "win32":
        raise RuntimeError("RPA de desktop (pywinauto) só está disponível no Windows.")


def _win(title_re: str):
    _need_windows()
    from pywinauto import Desktop
    return Desktop(backend="uia").window(title_re=title_re)


@tool("desktop_start_app", "Inicia um programa (caminho do .exe ou comando).",
      {"command": {"type": "string"}}, ("command",), risky=True)
def desktop_start_app(command: str):
    _need_windows()
    from pywinauto import Application
    Application(backend="uia").start(command)
    return "ok"


@tool("desktop_windows", "Lista os títulos das janelas abertas.")
def desktop_windows():
    _need_windows()
    from pywinauto import Desktop
    return [w.window_text() for w in Desktop(backend="uia").windows() if w.window_text()]


@tool("desktop_inspect", "Lista controles de uma janela (nome/tipo) para descobrir o que clicar.",
      {"window": {"type": "string", "description": "regex do título"}}, ("window",))
def desktop_inspect(window: str):
    w = _win(window)
    return [f"{c.element_info.control_type}: {c.window_text()}"
            for c in w.descendants() if c.window_text()][:150]


@tool("desktop_click", "Clica num controle pelo texto, dentro de uma janela.",
      {"window": {"type": "string"}, "control": {"type": "string"}}, ("window", "control"),
      risky=True)
def desktop_click(window: str, control: str):
    _win(window).child_window(title=control).click_input()
    return "ok"


@tool("desktop_type", "Digita texto num controle (ou na janela ativa se control vazio).",
      {"window": {"type": "string"}, "text": {"type": "string"}, "control": {"type": "string"}},
      ("window", "text"), risky=True)
def desktop_type(window: str, text: str, control: str = ""):
    w = _win(window)
    (w.child_window(title=control) if control else w).type_keys(text, with_spaces=True)
    return "ok"


@tool("desktop_read", "Lê o texto visível de uma janela.", {"window": {"type": "string"}},
      ("window",))
def desktop_read(window: str):
    return "\n".join(t for t in _win(window).texts() if t)[:8000]


@tool("desktop_click_xy", "Clica em coordenadas da tela (x, y em pixels).",
      {"x": {"type": "integer"}, "y": {"type": "integer"}}, ("x", "y"), risky=True)
def desktop_click_xy(x: int, y: int):
    import pyautogui
    pyautogui.click(x, y)
    return "ok"


@tool("desktop_hotkey", "Atalho de teclado, ex.: ['ctrl','s'].", {"keys": {"type": "array"}},
      ("keys",), risky=True)
def desktop_hotkey(keys: list):
    import pyautogui
    pyautogui.hotkey(*keys)
    return "ok"
