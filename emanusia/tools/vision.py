"""Visão: o robô 'enxerga' a tela/página com um modelo multimodal local (útil p/ apps sem API).
Modelos pequenos erram coordenadas: trate vision_locate como experimental e confirme antes de clicar."""
import json
import re

from .registry import tool


def _screenshot(source: str) -> bytes:
    if source == "web":
        from .web import _get_page
        return _get_page().screenshot()
    import io
    from PIL import ImageGrab  # pip install pillow (Windows/macOS)
    buf = io.BytesIO()
    ImageGrab.grab().save(buf, format="PNG")
    return buf.getvalue()


@tool("vision_ask", "Tira um print (source: web = página aberta, screen = tela toda) e responde uma "
      "pergunta sobre ele com o modelo de visão.",
      {"question": {"type": "string"}, "source": {"type": "string", "enum": ["web", "screen"]}},
      ("question",))
def vision_ask(question: str, source: str = "screen"):
    from ..llm import OllamaClient
    return OllamaClient().vision(question, [_screenshot(source)])


@tool("vision_locate", "Localiza um elemento visual e devolve o ponto {x,y} em pixels da imagem.",
      {"description": {"type": "string"}, "source": {"type": "string", "enum": ["web", "screen"]}},
      ("description",))
def vision_locate(description: str, source: str = "screen"):
    from ..llm import OllamaClient
    img = _screenshot(source)
    ans = OllamaClient().vision(
        f'Localize "{description}" na imagem. Responda SOMENTE JSON: {{"x": <int>, "y": <int>}} '
        f'com o centro do elemento em pixels, ou {{"x": null, "y": null}} se não achar.', [img])
    m = re.search(r"\{[^{}]*\}", ans)
    if not m:
        raise ValueError(f"Modelo não devolveu coordenadas: {ans[:200]}")
    pt = json.loads(m.group(0))
    if pt.get("x") is None:
        raise LookupError(f"Elemento não encontrado: {description}")
    return {"x": int(pt["x"]), "y": int(pt["y"])}
