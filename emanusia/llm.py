"""Cliente do LLM local (Ollama). Qualquer objeto com `.chat(messages, tools)` serve.
Erros do Ollama viram LLMError com a explicação em português e o que fazer."""
import base64
from contextvars import ContextVar

import httpx

from . import config

# LLM da tarefa atual (ferramentas como create_bot usam o mesmo modelo do chat)
CURRENT: ContextVar = ContextVar("current_llm", default=None)


class LLMError(RuntimeError):
    """Problema com a IA local, já explicado em português."""


def has_model(name: str, models: list[str]) -> bool:
    return name in models or (":" not in name and f"{name}:latest" in models)


class OllamaClient:
    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or config.settings()["model"]
        self.url = (url or config.OLLAMA_URL).rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        model = payload.get("model")
        try:
            r = httpx.post(f"{self.url}{path}", json=payload, timeout=httpx.Timeout(600, connect=5))
        except httpx.ConnectError as e:
            raise LLMError("O Ollama não está aberto neste computador. Abra o aplicativo Ollama (ícone da lhama "
                           "perto do relógio) ou rode `ollama serve` e tente de novo.") from e
        except httpx.TimeoutException as e:
            raise LLMError("A IA demorou demais para responder. O computador pode estar sobrecarregado: feche "
                           "outros programas e tente de novo.") from e
        try:
            data = r.json()
        except ValueError:
            data = {"error": r.text[:300]}
        err = data.get("error") if isinstance(data, dict) else None
        if r.status_code >= 400 or err:
            low = str(err or r.text).lower()
            if "not found" in low and ("model" in low or "pull" in low):
                raise LLMError(f"O modelo de IA “{model}” não está instalado. Abra o Prompt de Comando e rode: "
                               f"ollama pull {model}")
            if "does not support tools" in low:
                raise LLMError(f"O modelo “{model}” não sabe usar ferramentas. Em Ajustes, escolha qwen2.5:7b "
                               f"(ou outro modelo com suporte a ferramentas).")
            if "memory" in low or "cuda" in low or "out of" in low:
                raise LLMError(f"Falta memória para o modelo “{model}”. Feche jogos/programas pesados ou escolha um "
                               f"modelo menor em Ajustes (ex.: qwen2.5:3b). Detalhe: {err}")
            raise LLMError(f"O Ollama respondeu com erro: {err or r.status_code}")
        return data

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.1, "num_ctx": config.NUM_CTX}}
        if tools:
            payload["tools"] = tools
        msg = self._post("/api/chat", payload).get("message")
        if not isinstance(msg, dict):
            raise LLMError("O Ollama devolveu uma resposta inesperada (sem mensagem). Atualize o Ollama.")
        return msg

    def vision(self, prompt: str, images: list[bytes], model: str | None = None) -> str:
        """Pergunta sobre imagens (screenshots) a um modelo multimodal."""
        msg = {"role": "user", "content": prompt,
               "images": [base64.b64encode(i).decode() for i in images]}
        payload = {"model": model or config.settings()["vision_model"], "messages": [msg],
                   "stream": False, "options": {"temperature": 0, "num_ctx": config.NUM_CTX}}
        return self._post("/api/chat", payload)["message"]["content"]

    def models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=5)
            return sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return []

    def health(self) -> dict:
        """Tudo pronto para o chat? Devolve o que falta em português."""
        vision = config.settings()["vision_model"]
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3)
            models = sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return {"ok": False, "running": False, "models": [], "model": self.model,
                    "message": "O Ollama não está aberto. Abra o aplicativo Ollama e recarregue esta página."}
        if not has_model(self.model, models):
            return {"ok": False, "running": True, "models": models, "model": self.model,
                    "message": f"O modelo “{self.model}” não está instalado. No Prompt de Comando rode: ollama pull {self.model}"}
        warn = None if has_model(vision, models) else \
            f"(Opcional) Para o robô enxergar a tela, rode: ollama pull {vision}"
        return {"ok": True, "running": True, "models": models, "model": self.model, "message": warn}
