"""Cliente do LLM local (Ollama). Qualquer objeto com `.chat(messages, tools)` serve."""
import base64

import httpx

from . import config


class OllamaClient:
    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or config.settings()["model"]
        self.url = (url or config.OLLAMA_URL).rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = httpx.post(f"{self.url}{path}", json=payload, timeout=600)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollama não encontrado em {self.url}. Instale em https://ollama.com e rode "
                f"`ollama pull {payload.get('model')}`.") from e
        return r.json()

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.1, "num_ctx": config.NUM_CTX}}
        if tools:
            payload["tools"] = tools
        return self._post("/api/chat", payload)["message"]

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
