"""Servidor que imita a API do Ollama (/api/chat e /api/tags), com a mesma rigidez:
modelo não instalado -> 404, modelo sem ferramentas -> 400, formato de mensagens/ferramentas checado,
e registro de pedidos que passariam do contexto (o Ollama real corta o começo em silêncio)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLES = {"system", "user", "assistant", "tool"}


class FakeOllama:
    def __init__(self, reply, models=("qwen2.5:7b", "qwen2.5vl:3b"), tools_supported=True):
        self.reply = reply                  # função(pedido) -> mensagem do assistente
        self.models = list(models)
        self.tools_supported = tools_supported
        self.vram = 100                     # 100 = modelo inteiro na GPU; 0 = rodando no processador
        self.requests: list[dict] = []
        self.problems: list[str] = []       # violações de formato / contexto estourado
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": m} for m in fake.models]})
                if self.path == "/api/ps":
                    return self._send(200, {"models": [{"name": m, "size": 100, "size_vram": fake.vram} for m in fake.models[:1]]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(req)
                model = req.get("model")
                if model not in fake.models:
                    return self._send(404, {"error": f'model "{model}" not found, try pulling it first'})
                if req.get("tools") and not fake.tools_supported:
                    return self._send(400, {"error": f"registry.ollama.ai/library/{model} does not support tools"})
                fake.check(req)
                try:
                    msg = fake.reply(req)
                except Exception as e:  # erro no roteiro do teste
                    return self._send(500, {"error": f"roteiro: {e}"})
                if isinstance(msg, tuple):  # roteiro pediu um erro HTTP: (código, mensagem)
                    return self._send(msg[0], {"error": msg[1]})
                self._send(200, {"model": model, "message": {"role": "assistant", "content": "", **msg}, "done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def check(self, req):
        for m in req.get("messages", []):
            if m.get("role") not in ROLES:
                self.problems.append(f"papel inválido: {m.get('role')}")
            if not isinstance(m.get("content", ""), str):
                self.problems.append("content não é texto")
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc.get("function", {}).get("arguments"), dict):
                    self.problems.append("tool_calls.arguments deveria ser objeto")
        for t in req.get("tools") or []:
            f = t.get("function", {})
            if t.get("type") != "function" or not f.get("name") or f.get("parameters", {}).get("type") != "object":
                self.problems.append(f"ferramenta mal formada: {f.get('name')}")
        size = len(json.dumps(req.get("messages", []), ensure_ascii=False)) + len(json.dumps(req.get("tools") or [], ensure_ascii=False))
        ctx = req.get("options", {}).get("num_ctx", 2048)
        if size / 3.0 > ctx:  # ~3 caracteres por token: acima disso o Ollama cortaria o começo
            self.problems.append(f"contexto estourado: ~{int(size / 3)} tokens > num_ctx {ctx}")

    def close(self):
        self.server.shutdown()
