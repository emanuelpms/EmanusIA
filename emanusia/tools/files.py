"""Arquivos e pastas: listar, ler/gravar texto e JSON, copiar, mover, renomear, apagar, compactar.
Tudo preso às pastas liberadas (pasta de trabalho + as que o admin liberar em Ajustes)."""
import glob
import json
import shutil
import time
import zipfile
from pathlib import Path

from .. import config
from .registry import tool


def expand_files(files, exts: tuple = ()) -> list[Path]:
    """Lista de arquivos a partir de: um caminho, um padrão ('notas/*.pdf', '**/*.xml') ou uma lista."""
    items = files if isinstance(files, list) else [files]
    out: list[Path] = []
    for it in items:
        it = str(it).strip()
        if not it:
            continue
        if any(c in it for c in "*?["):
            base = config.safe_path(it, mkdir=False)  # valida a pasta antes de procurar
            found = [Path(p) for p in sorted(glob.glob(str(base), recursive=True))]
        else:
            found = [config.safe_path(it, mkdir=False)]
        for p in found:
            p = config.safe_path(str(p), mkdir=False)
            if p.is_file() and (not exts or p.suffix.lower() in exts) and p not in out:
                out.append(p)
    return out


def _info(p: Path) -> dict:
    st = p.stat()
    return {"path": config.display_path(p), "name": p.name, "ext": p.suffix.lower().lstrip("."),
            "size_kb": round(st.st_size / 1024, 1), "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}


@tool("file_list", "Lista arquivos de uma pasta. pattern filtra (ex.: '*.pdf'); details=true traz tamanho e data.",
      {"subdir": {"type": "string"}, "pattern": {"type": "string"}, "recursive": {"type": "boolean"},
       "details": {"type": "boolean"}})
def file_list(subdir: str = ".", pattern: str = "*", recursive: bool = True, details: bool = False):
    base = config.safe_path(subdir or ".", mkdir=False)
    if not base.exists():
        return []
    it = base.rglob(pattern or "*") if recursive else base.glob(pattern or "*")
    files = sorted((p for p in it if p.is_file()), key=lambda p: config.display_path(p))
    return [_info(p) for p in files] if details else [config.display_path(p) for p in files]


@tool("file_read", "Lê um arquivo de texto.", {"path": {"type": "string"}}, required=("path",))
def file_read(path: str):
    p = config.safe_path(path, mkdir=False)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return p.read_text(encoding=enc)[:200_000]
        except UnicodeDecodeError:
            continue


@tool("file_write", "Escreve texto (ou JSON) em um arquivo. append=true acrescenta no fim.",
      {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
      required=("path", "content"))
def file_write(path: str, content, append: bool = False):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2, default=str)
    p = config.safe_path(path)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content + ("\n" if append and not content.endswith("\n") else ""))
    return {"path": path, "bytes": len(content.encode())}


@tool("json_read", "Lê um arquivo JSON (lista ou objeto).", {"path": {"type": "string"}}, ("path",))
def json_read(path: str):
    return json.loads(config.safe_path(path, mkdir=False).read_text(encoding="utf-8-sig"))


@tool("json_write", "Grava dados (lista/objeto de outro passo) em um arquivo JSON.",
      {"path": {"type": "string"}, "data": {}}, ("path", "data"))
def json_write(path: str, data):
    config.safe_path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"path": path}


@tool("file_exists", "Verifica se um arquivo ou pasta existe (sim/não).", {"path": {"type": "string"}}, ("path",))
def file_exists(path: str):
    return config.safe_path(path, mkdir=False).exists()


@tool("folder_create", "Cria uma pasta (e as pastas acima, se faltarem).", {"path": {"type": "string"}}, ("path",))
def folder_create(path: str):
    p = config.safe_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return config.display_path(p)


def _dest(src: Path, destination: str) -> Path:
    d = config.safe_path(destination)
    if d.is_dir() or str(destination).endswith(("/", "\\")):
        d.mkdir(parents=True, exist_ok=True)
        d = d / src.name
    return d


@tool("file_copy", "Copia arquivo(s) para uma pasta ou novo nome. files aceita padrão ('entrada/*.pdf').",
      {"files": {}, "destination": {"type": "string"}}, ("files", "destination"))
def file_copy(files, destination: str):
    srcs = expand_files(files)
    if not srcs:
        raise FileNotFoundError(f"nenhum arquivo em {files}")
    out = []
    for s in srcs:
        d = _dest(s, destination if len(srcs) == 1 else destination.rstrip("/\\") + "/")
        shutil.copy2(s, d)
        out.append(config.display_path(d))
    return out


@tool("file_move", "Move arquivo(s) para outra pasta (ex.: mover os PDFs processados para 'feitos/').",
      {"files": {}, "destination": {"type": "string"}}, ("files", "destination"))
def file_move(files, destination: str):
    srcs = expand_files(files)
    if not srcs:
        raise FileNotFoundError(f"nenhum arquivo em {files}")
    out = []
    for s in srcs:
        d = _dest(s, destination if len(srcs) == 1 else destination.rstrip("/\\") + "/")
        shutil.move(str(s), str(d))
        out.append(config.display_path(d))
    return out


@tool("file_rename", "Renomeia um arquivo (fica na mesma pasta).",
      {"path": {"type": "string"}, "new_name": {"type": "string"}}, ("path", "new_name"))
def file_rename(path: str, new_name: str):
    src = config.safe_path(path, mkdir=False)
    if any(c in new_name for c in "/\\") or new_name in ("", ".", ".."):
        raise ValueError("o novo nome não pode ter pastas")
    dst = config.safe_path(str(src.parent / new_name))
    if dst.exists():
        raise FileExistsError(f"já existe {new_name}")
    src.rename(dst)
    return config.display_path(dst)


@tool("file_delete", "Apaga arquivo(s). Pede autorização.", {"files": {}}, ("files",), risky=True)
def file_delete(files):
    srcs = expand_files(files)
    for s in srcs:
        s.unlink()
    return {"deleted": len(srcs)}


@tool("file_zip", "Compacta arquivo(s) em um .zip.", {"files": {}, "output": {"type": "string"}}, ("files", "output"))
def file_zip(files, output: str):
    srcs = expand_files(files)
    if not srcs:
        raise FileNotFoundError(f"nenhum arquivo em {files}")
    out = config.safe_path(output)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for s in srcs:
            z.write(s, s.name)
    return {"output": config.display_path(out), "files": len(srcs)}


@tool("file_unzip", "Descompacta um .zip numa pasta e lista os arquivos extraídos.",
      {"path": {"type": "string"}, "folder": {"type": "string"}}, ("path",))
def file_unzip(path: str, folder: str = ""):
    src = config.safe_path(path, mkdir=False)
    dest = config.safe_path(folder or str(src.with_suffix("")))
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(src) as z:
        for m in z.infolist():
            if m.is_dir():
                continue
            target = config.safe_path(str(dest / m.filename))  # bloqueia "../" dentro do zip
            if dest not in target.parents:
                raise PermissionError(f"arquivo suspeito dentro do zip: {m.filename}")
            with z.open(m) as fi, open(target, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            out.append(config.display_path(target))
    return out
