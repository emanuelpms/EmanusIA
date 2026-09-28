"""Abre a GUI: servidor local + janela nativa (pywebview) ou, na falta dela, o navegador padrão."""
import socket
import threading
import time
import webbrowser


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        return preferred if s.connect_ex(("127.0.0.1", preferred)) != 0 else 0


def launch(port: int = 8765):
    import uvicorn
    port = _free_port(port) or 8766
    server = uvicorn.Server(uvicorn.Config("emanusia.api:app", host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # pip install pywebview
        webview.create_window("EmanusIA", url, width=1280, height=820)
        webview.start()
    except ImportError:
        print(f"EmanusIA rodando em {url} (Ctrl+C para sair). Dica: pip install pywebview p/ janela nativa.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
