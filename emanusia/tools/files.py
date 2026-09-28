import json

from .. import config
from .registry import tool


@tool("file_list", "Lista arquivos do workspace.", {"subdir": {"type": "string"}})
def file_list(subdir: str = "."):
    base = config.safe_path(subdir)
    return sorted(str(p.relative_to(config.workspace())) for p in base.rglob("*") if p.is_file())


@tool("file_read", "Lê um arquivo de texto do workspace.", {"path": {"type": "string"}},
      required=("path",))
def file_read(path: str):
    return config.safe_path(path).read_text(encoding="utf-8")[:20000]


@tool("file_write", "Escreve texto (ou JSON) em um arquivo do workspace.",
      {"path": {"type": "string"}, "content": {"type": "string"}}, required=("path", "content"))
def file_write(path: str, content):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    config.safe_path(path).write_text(content, encoding="utf-8")
    return {"path": path, "bytes": len(content.encode())}
