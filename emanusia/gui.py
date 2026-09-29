"""Abre a GUI: servidor local + janela nativa (pywebview) ou, na falta dela, o navegador padrão.
Se o EmanusIA já estiver aberto, só mostra a tela de novo (não sobe um segundo servidor)."""
import socket
import threading
import time
import webbrowser

import httpx


def _already_running(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/me", timeout=1.5)
        return r.status_code == 200 and "auth_enabled" in r.json()
    except Exception:
        return False


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _show(url: str, wait_forever: bool):
    try:
        import webview  # pip install pywebview (janela própria)
        webview.create_window("EmanusIA", url, width=1280, height=820)
        webview.start()
        return
    except ImportError:
        pass
    except Exception as e:  # ex.: WebView2 ausente no Windows -> usa o navegador
        print(f"Janela própria indisponível ({e}). Abrindo no navegador...")
    print(f"EmanusIA em {url}  (feche esta janela ou Ctrl+C para sair)")
    webbrowser.open(url)
    if wait_forever:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def launch(port: int = 8765):
    import uvicorn
    if _already_running(port):
        print("O EmanusIA já está aberto: mostrando a tela.")
        return _show(f"http://127.0.0.1:{port}/", wait_forever=False)
    if not _port_free(port):
        port = next((p for p in range(port + 1, port + 20) if _port_free(p)), 0)
        if not port:
            raise SystemExit("Nenhuma porta livre entre 8765 e 8785. Feche outros programas e tente de novo.")
    server = uvicorn.Server(uvicorn.Config("emanusia.api:app", host="127.0.0.1", port=port, log_level="warning"))
    errors = []

    def serve():
        try:
            server.run()
        except BaseException as e:  # noqa: BLE001 — mostra o motivo em vez de travar
            errors.append(e)
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    t0 = time.time()
    while not server.started:
        if errors or not t.is_alive() or time.time() - t0 > 60:
            raise SystemExit(f"O servidor do EmanusIA não iniciou: {errors[0] if errors else 'tempo esgotado'}. "
                             f"Rode diagnostico.bat e veja data/logs/emanusia.log.")
        time.sleep(0.1)
    _show(f"http://127.0.0.1:{port}/", wait_forever=True)
    server.should_exit = True
