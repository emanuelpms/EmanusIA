"""PDF: ler o texto, achar informações (CNPJ, valores, datas, o que vem depois de um rótulo),
juntar e separar arquivos. Tabelas de PDF ficam em `data_extract`."""
import re
from pathlib import Path

from .. import config
from .files import expand_files
from .registry import tool

MAX_TEXT = 200_000

# padrões prontos para quem não sabe expressão regular
PRESETS = {
    "cnpj": r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
    "cpf": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
    "valor": r"R\$\s*-?\d{1,3}(?:\.\d{3})*(?:,\d{2})|R\$\s*-?\d+(?:,\d{2})?",
    "data": r"\b\d{2}/\d{2}/\d{4}\b",
    "email": r"[\w.+\-]+@[\w\-]+\.[\w.\-]+",
    "telefone": r"\(?\d{2}\)?\s?9?\d{4}-?\d{4}",
    "cep": r"\b\d{5}-?\d{3}\b",
    "chave_nfe": r"(?:\d{4}\s?){10}\d{4}",
    "linha_digitavel": r"\d{5}\.\d{5}\s\d{5}\.\d{6}\s\d{5}\.\d{6}\s\d\s\d{14}",
    "numero": r"-?\d{1,3}(?:\.\d{3})*,\d+|-?\d+(?:,\d+)?",
}


def _pages(spec: str, n: int) -> list[int]:
    """'1-3,5' -> [0,1,2,4] (vazio = todas)."""
    if not str(spec or "").strip():
        return list(range(n))
    out = []
    for part in str(spec).split(","):
        a, _, b = part.strip().partition("-")
        lo, hi = int(a), int(b or a)
        out += [i - 1 for i in range(lo, hi + 1) if 1 <= i <= n]
    return out


def read_text(path: str, pages: str = "") -> tuple[str, int]:
    import pdfplumber
    with pdfplumber.open(config.safe_path(path, mkdir=False)) as pdf:
        n = len(pdf.pages)
        text = "\n".join((pdf.pages[i].extract_text() or "") for i in _pages(pages, n))
    return text, n


def _text_of(path, text) -> str:
    if text not in (None, ""):
        return str(text)
    if not path:
        raise ValueError("informe o arquivo PDF (path) ou o texto (text)")
    return read_text(path)[0]


@tool("pdf_text", "Lê o texto de um PDF (todas as páginas ou '1-3,5'). Se o PDF for uma imagem "
      "escaneada, o texto vem vazio.",
      {"path": {"type": "string"}, "pages": {"type": "string"}}, ("path",))
def pdf_text(path: str, pages: str = ""):
    text, n = read_text(path, pages)
    return {"pages": n, "text": text[:MAX_TEXT], "chars": len(text)}


@tool("pdf_find", "Acha informações no texto de um PDF: find = cnpj, cpf, valor, data, email, telefone, cep, "
      "chave_nfe, linha_digitavel, numero ou uma expressão regular própria em `pattern`. Retorna a lista "
      "do que achou (ou só o primeiro com first=true).",
      {"path": {"type": "string"}, "text": {"type": "string"}, "find": {"type": "string", "enum": list(PRESETS)},
       "pattern": {"type": "string"}, "first": {"type": "boolean"}})
def pdf_find(path: str = "", text: str = "", find: str = "", pattern: str = "", first: bool = False):
    rx = pattern or PRESETS.get(find)
    if not rx:
        raise ValueError(f"diga o que procurar: find ({', '.join(PRESETS)}) ou pattern")
    found = []
    for m in re.finditer(rx, _text_of(path, text), re.I):
        v = m.group(1) if m.groups() else m.group(0)
        v = re.sub(r"\s+", " ", v).strip()
        if find == "chave_nfe":
            v = v.replace(" ", "")
        if v not in found:
            found.append(v)
    if first:
        return found[0] if found else ""
    return found


@tool("pdf_fields", "Lê campos de um documento pelo rótulo: fields = {nome: 'rótulo que aparece antes'}. "
      "Ex.: {total: 'Valor total', vencimento: 'Vencimento'} pega o que vem depois de cada rótulo na "
      "mesma linha (ou na linha de baixo).",
      {"path": {"type": "string"}, "text": {"type": "string"}, "fields": {"type": "object"}}, ("fields",))
def pdf_fields(fields: dict, path: str = "", text: str = ""):
    lines = [l.strip() for l in _text_of(path, text).splitlines()]
    out = {}
    for name, label in (fields or {}).items():
        lab = re.escape(str(label).strip()).replace(r"\ ", r"\s+")
        val = ""
        for i, line in enumerate(lines):
            m = re.search(lab + r"\s*[:\-–]?\s*(.*)", line, re.I)
            if m:
                val = m.group(1).strip()
                if not val and i + 1 < len(lines):
                    val = lines[i + 1]
                break
        out[name] = val
    return out


@tool("pdf_merge", "Junta vários PDFs em um só, na ordem dada (lista de arquivos ou padrão como 'notas/*.pdf').",
      {"files": {}, "output": {"type": "string"}}, ("files", "output"))
def pdf_merge(files, output: str):
    from pypdf import PdfWriter
    paths = expand_files(files, (".pdf",))
    if not paths:
        raise FileNotFoundError("nenhum PDF para juntar")
    w = PdfWriter()
    for p in paths:
        w.append(str(p))
    out = config.safe_path(output)
    with open(out, "wb") as f:
        w.write(f)
    return {"output": config.display_path(out), "files": len(paths)}


@tool("pdf_split", "Separa um PDF: uma página por arquivo (padrão) ou os intervalos dados em `ranges` "
      "('1-2,3-5'). Os arquivos vão para `folder`.",
      {"path": {"type": "string"}, "folder": {"type": "string"}, "ranges": {"type": "string"}}, ("path",))
def pdf_split(path: str, folder: str = "", ranges: str = ""):
    from pypdf import PdfReader, PdfWriter
    src = config.safe_path(path, mkdir=False)
    r = PdfReader(str(src))
    n = len(r.pages)
    groups = []
    if ranges.strip():
        for part in ranges.split(","):
            groups.append(_pages(part, n))
    else:
        groups = [[i] for i in range(n)]
    base = folder or src.stem
    out = []
    for g in groups:
        if not g:
            continue
        w = PdfWriter()
        for i in g:
            w.add_page(r.pages[i])
        name = f"{src.stem}_p{g[0] + 1}" + (f"-{g[-1] + 1}" if len(g) > 1 else "") + ".pdf"
        dst = config.safe_path(str(Path(base) / name))
        with open(dst, "wb") as f:
            w.write(f)
        out.append(config.display_path(dst))
    return out


@tool("pdf_info", "Informações do PDF: número de páginas, título, autor e se tem texto (ou é imagem escaneada).",
      {"path": {"type": "string"}}, ("path",))
def pdf_info(path: str):
    from pypdf import PdfReader
    r = PdfReader(str(config.safe_path(path, mkdir=False)))
    meta = r.metadata or {}
    first = (r.pages[0].extract_text() or "").strip() if r.pages else ""
    return {"pages": len(r.pages), "title": str(meta.get("/Title", "") or ""),
            "author": str(meta.get("/Author", "") or ""), "has_text": bool(first)}
