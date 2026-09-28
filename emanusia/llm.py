"""Cliente do LLM local (Ollama). Qualquer objeto com `.chat(messages, tools)` serve."""
import httpx

from . import config


class OllamaClient:
    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or config.MODEL
        self.url = (url or config.OLLAMA_URL).rstrip("/")

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.1}}
        if tools:
            payload["tools"] = tools
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=600)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollama não encontrado em {self.url}. Instale em https://ollama.com e rode "
                f"`ollama pull {self.model}`.") from e
        return r.json()["message"]
