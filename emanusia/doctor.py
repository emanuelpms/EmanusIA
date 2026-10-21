"""Diagnóstico: confere tudo o que o EmanusIA precisa neste computador e diz como consertar.
`emanusia doctor` (ou diagnostico.bat) — tire um print do resultado para o suporte."""
import importlib
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import config

log = logging.getLogger("emanusia")


def setup_logging():
    """Erros completos (com o rastro) em data/logs/emanusia.log — 3 arquivos de 1 MB."""
    d = config.data_dir() / "logs"
    target = str(d / "emanusia.log")
    for old in [h for h in log.handlers if isinstance(h, RotatingFileHandler)]:
        if old.baseFilename == target:
            return
        log.removeHandler(old)  # a pasta de dados mudou: o registro acompanha
        old.close()
    d.mkdir(exist_ok=True)
    h = RotatingFileHandler(target, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def _item(name, ok, detail="", fix="", level=None):
    return {"name": name, "ok": ok, "level": level or ("ok" if ok else "erro"), "detail": detail, "fix": fix}


def check_python():
    v = sys.version_info
    ok = v >= (3, 10)
    return _item("Python", ok, f"{v.major}.{v.minor}.{v.micro}",
                 "" if ok else "Instale o Python 3.11 (python.org) e rode instalar.bat de novo.")


def check_folders():
    try:
        for d in (config.data_dir(), config.workspace()):
            p = d / ".teste_escrita"
            p.write_text("ok", encoding="utf-8")
            p.unlink()
        return _item("Pastas de trabalho", True, f"{config.data_dir()} · {config.workspace()}")
    except OSError as e:
        return _item("Pastas de trabalho", False, str(e), "Mova a pasta do EmanusIA para Documentos ou rode como seu usuário.")


def check_packages():
    missing = []
    for m in ("fastapi", "uvicorn", "playwright", "openpyxl", "pandas", "plotly", "cryptography", "pdfplumber", "yaml"):
        try:
            importlib.import_module(m)
        except ImportError:
            missing.append(m)
    return _item("Pacotes do sistema", not missing, "todos instalados" if not missing else "faltando: " + ", ".join(missing),
                 "" if not missing else "Rode instalar.bat de novo.")


def check_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            kw = {"headless": True}
            if os.environ.get("EMANUSIA_CHROMIUM"):
                kw["executable_path"] = os.environ["EMANUSIA_CHROMIUM"]
            b = p.chromium.launch(**kw)
            b.close()
        return _item("Navegador dos robôs", True, "Chromium abre normalmente")
    except Exception as e:
        return _item("Navegador dos robôs", False, str(e).split("\n")[0][:200],
                     "Rode: .venv\\Scripts\\python -m playwright install chromium")


def check_vault():
    try:
        from . import vault
        f = vault._key()
        ok = f.decrypt(f.encrypt(b"teste")) == b"teste"
        return _item("Cofre de acessos", ok, "abre e fecha normalmente")
    except Exception as e:
        return _item("Cofre de acessos", False, str(e)[:200],
                     "A chave do cofre é presa ao usuário do Windows que o criou: use o mesmo usuário ou recadastre os acessos.")


def check_windows() -> list[dict]:
    if sys.platform != "win32":
        return [_item("Integrações do Windows", True, "não é Windows (SAP/Outlook/programas indisponíveis aqui)", level="aviso")]
    out = []
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
        out.append(_item("pywin32 (SAP, Outlook, Excel)", True, "instalado"))
    except ImportError:
        return [_item("pywin32 (SAP, Outlook, Excel)", False, "não instalado", 'Rode: .venv\\Scripts\\pip install pywin32')]
    try:
        import win32com.client
        win32com.client.GetObject("SAPGUI").GetScriptingEngine
        out.append(_item("SAP GUI Scripting", True, "SAP Logon aberto e scripting liberado"))
    except Exception:
        out.append(_item("SAP GUI Scripting", False, "SAP Logon fechado ou scripting desativado",
                         "Só importa se for usar SAP: abra o SAP Logon e ative o scripting (veja docs/ARQUITETURA.md).",
                         level="aviso"))
    return out


def recent_errors(n: int = 5) -> list[str]:
    f = config.data_dir() / "logs" / "emanusia.log"
    if not f.exists():
        return []
    lines = [l for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if " ERROR " in l]
    return lines[-n:]


def run(full: bool = True) -> dict:
    items = [check_python(), check_folders(), check_packages(), check_browser(),
             check_vault(), *check_windows()]
    return {"ok": all(i["ok"] or i["level"] == "aviso" for i in items), "items": items, "errors": recent_errors(),
            "log": str(config.data_dir() / "logs" / "emanusia.log")}


def print_report(rep: dict):
    icon = {"ok": "✅", "aviso": "⚠️ ", "erro": "❌"}
    print("\n  Diagnóstico do EmanusIA\n  " + "-" * 60)
    for i in rep["items"]:
        print(f"  {icon[i['level']]} {i['name']}: {i['detail']}")
        if i["fix"] and i["level"] != "ok":
            print(f"      → {i['fix']}")
    if rep["errors"]:
        print("\n  Últimos erros registrados:")
        for e in rep["errors"]:
            print("   ", e[:200])
    print(f"\n  Registro completo: {rep['log']}")
    print("  " + ("Tudo certo!" if rep["ok"] else "Há itens para corrigir (veja as setas →).") + "\n")
