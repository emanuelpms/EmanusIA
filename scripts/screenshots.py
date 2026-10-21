"""Gera as imagens do README (docs/images/) com o sistema funcionando de verdade.

Uso:  python scripts/screenshots.py
Cria uma empresa de exemplo numa pasta temporária, sobe o servidor, abre as telas num navegador
sem janela e fotografa. Não mexe nos seus dados reais."""
import io
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
TMP = Path(tempfile.mkdtemp(prefix="emanusia_shots_"))
PORT = 8931
SITE_PORT = 8932  # "site da empresa" de exemplo (portal do fornecedor)
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM = os.environ.get("EMANUSIA_CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)

os.environ.update(EMANUSIA_WORKSPACE=str(TMP / "ws"), EMANUSIA_DATA=str(TMP / "data"),
                  EMANUSIA_WORKFLOWS=str(TMP / "wf"), EMANUSIA_HEADLESS="1", EMANUSIA_NO_SCHEDULER="1")
if CHROMIUM:
    os.environ["EMANUSIA_CHROMIUM"] = CHROMIUM
sys.path.insert(0, str(ROOT))

from emanusia import auth, bots, config, vault  # noqa: E402
from emanusia.tools import load_all, registry  # noqa: E402

PORTAL = """<!doctype html><meta charset=utf-8><title>Portal do Fornecedor</title>
<style>body{font:15px system-ui;margin:0;background:#f4f6fb}header{background:#1f3a5f;color:#fff;padding:14px 22px;font-weight:600}
main{padding:22px}label{display:block;margin:10px 0 4px}input,select{padding:8px;border:1px solid #ccd;border-radius:6px;width:260px}
button{margin-top:14px;padding:9px 18px;background:#2f6db5;color:#fff;border:0;border-radius:6px}table{margin-top:16px;border-collapse:collapse;background:#fff}
td,th{border:1px solid #dde;padding:6px 12px}</style><header>Portal do Fornecedor ACME</header><main>
<label for=u>E-mail</label><input id=u><label for=f>Filial</label><select id=f><option>SP</option><option>RJ</option></select><br>
<button id=BTN onclick="document.getElementById('r').innerHTML='<table><tr><th>Nota</th><th>Emissão</th><th>Valor</th></tr><tr><td>101</td><td>02/09</td><td>R$ 2.500,00</td></tr><tr><td>102</td><td>05/09</td><td>R$ 980,00</td></tr></table>'">Buscar notas</button><div id=r></div></main>"""


def seed():
    load_all()
    from emanusia.demo import seed as demo
    demo()
    adm = auth.LOCAL_ADMIN
    ws = config.workspace()
    (ws / "portal.html").write_text(PORTAL.replace("BTN", "buscar"), encoding="utf-8")
    (ws / "portal2.html").write_text(PORTAL.replace("BTN", "pesquisar").replace("Buscar notas", "Pesquisar"), encoding="utf-8")

    def web_bot(name, page, team, desc):
        code = {"params": {"filial": "RJ"}, "steps": [
            {"tool": "web_open", "args": {"url": f"http://localhost:{SITE_PORT}/{page}"}},
            {"tool": "web_fill", "args": {"selector": "#u", "value": "compras@empresa.com"}, "label": "E-mail"},
            {"tool": "web_select", "args": {"selector": "#f", "value": "{{filial}}"}, "label": "Filial"},
            {"tool": "web_click", "args": {"selector": "#buscar"}, "label": "Buscar notas"},
            {"tool": "web_table", "args": {"selector": "#r table"}, "save_as": "notas"},
            {"tool": "excel_write", "args": {"path": "notas.xlsx", "sheets": {"Notas": "{{notas}}"}}}]}
        b = bots.create_bot(name, adm, code, team, description=desc, origin="gravacao")
        bots.validate_bot(b.id, adm)
        return b.id

    web_bot("Baixar notas do fornecedor", "portal.html", "Compras", "Entra no portal e baixa as notas do dia.")
    web_bot("Consultar pedidos", "portal2.html", "Compras", "Consulta pedidos no portal (o site mudou!).")
    slow = bots.create_bot("Relatório diário", adm, {"params": {}, "steps": [
        {"log": "juntando planilhas"}, {"sleep": 4}, {"log": "montando o painel"}, {"sleep": 4}]},
        "Vendas", description="Consolida as vendas do dia.")
    bots.validate_bot(slow.id, adm)
    bots.set_active(slow.id, adm, True)
    mail = bots.create_bot("Enviar fechamento", adm, {"params": {}, "steps": [
        {"tool": "excel_write", "args": {"path": "fechamento.xlsx", "sheets": {"Resumo": [["Mês", "Total"], ["Set", 415000]]}}},
        {"tool": "email_send", "args": {"to": "diretoria@empresa.com", "subject": "Fechamento de setembro",
                                        "body": "Segue o fechamento.", "attachments": ["fechamento.xlsx"]}}]},
        "Financeiro", description="Envia o fechamento para a diretoria.")
    bots.validate_bot(mail.id, adm)
    bots.set_active(mail.id, adm, True)
    vault.set_credential("local", "portal-acme", "compras@empresa.com", "x", label="Portal do fornecedor ACME",
                         url="https://portal.acme.com.br/login")
    vault.set_credential("local", "sap-producao", "MARIA.SILVA", "x", label="SAP produção (mandante 300)", kind="sap",
                         extra={"client": "300", "language": "PT"})
    vault.set_credential(vault.SHARED, "banco-extrato", "empresa.fin", "x", label="Internet banking (conta de serviço)",
                         url="https://banco.com.br")
    # documentos de exemplo para o construtor: boletos em PDF e notas fiscais em XML
    sys.path.insert(0, str(ROOT / "tests"))
    from test_builder import NFE, make_pdf
    for nome, valor, venc, linha in (("energia", "R$ 1.320,45", "12/10/2026", "23790.12345 60000.123456 78901.234567 1 98760000132045"),
                                     ("agua", "R$ 289,90", "15/10/2026", "23790.11111 60000.222222 78901.333333 2 98760000028990"),
                                     ("internet", "R$ 449,00", "20/10/2026", "23790.44444 60000.555555 78901.666666 3 98760000044900")):
        make_pdf(f"boletos/{nome}.pdf", [[f"Conta de {nome}", f"Vencimento: {venc}", f"Total a pagar {valor}", linha]])
    for n, total in ((1, "1250.00"), (2, "980.40"), (3, "4310.75")):
        config.safe_path(f"notas/nfe{n}.xml").write_text(NFE.format(n=n, total=total), encoding="utf-8")
    random.seed(7)
    rows = [["Data", "Região", "Vendedor", "Produto", "Valor", "Qtd"]]
    for _ in range(900):
        rows.append([f"{random.randint(1, 28):02d}/{random.randint(1, 9):02d}/2026", random.choice(["Sul", "Sudeste", "Norte", "Nordeste", "Centro-Oeste"]),
                     random.choice(["Ana", "Rui", "Lia", "Caio", "Bia", "Téo"]), random.choice(["Notebook", "Monitor", "Teclado", "Cadeira", "Headset"]),
                     round(random.uniform(80, 4000), 2), random.randint(1, 6)])
    registry.call("excel_write", {"path": "vendas.xlsx", "sheets": {"Vendas": rows}})
    registry.call("dashboard_data", {"path": "painel.html", "source": "vendas.xlsx", "title": "Painel Comercial 2026",
        "subtitle": "Gerado pelo robô a partir de uma planilha de vendas",
        "kpis": [{"label": "Faturamento", "column": "Valor", "agg": "sum", "format": "money"},
                 {"label": "Pedidos", "column": "Valor", "agg": "count", "format": "int"},
                 {"label": "Ticket médio", "column": "Valor", "agg": "mean", "format": "money"},
                 {"label": "Itens", "column": "Qtd", "agg": "sum", "format": "int"}],
        "charts": [{"type": "area", "x": "Data", "y": "Valor", "period": "month", "title": "Faturamento mensal"},
                   {"type": "bar", "x": "Vendedor", "y": "Valor", "title": "Faturamento por vendedor"},
                   {"type": "pie", "x": "Região", "y": "Valor", "title": "Participação por região"},
                   {"type": "bar", "x": "Produto", "y": "Qtd", "horizontal": True, "title": "Itens por produto"}],
        "filters": ["Região", "Vendedor", "Produto"], "date_column": "Data"})


def wait_job(pg, jid, want=("done", "error"), timeout=90):
    t = time.time()
    while time.time() - t < timeout:
        s = pg.evaluate(f"fetch('/api/jobs/{jid}').then(r => r.json())")
        if s["status"] in want:
            return s
        pg.wait_for_timeout(300)
    raise TimeoutError(jid)


def post(pg, url, body=None):
    return pg.evaluate("([u, b]) => fetch(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                       "body: JSON.stringify(b || {})}).then(r => r.json())", [url, body])


def shot(pg, name, **kw):
    OUT.mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=str(OUT / name), **kw)
    print("  ✓", name)


def main():
    from playwright.sync_api import sync_playwright
    seed()
    site = subprocess.Popen([sys.executable, "-m", "http.server", str(SITE_PORT), "--bind", "127.0.0.1"],
                            cwd=config.workspace(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "emanusia.api:app", "--port", str(PORT), "--log-level", "warning"],
                           cwd=ROOT, env=os.environ.copy())
    try:
        time.sleep(3)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROMIUM, headless=True)
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            pg = ctx.new_page()
            pg.on("dialog", lambda d: d.dismiss())
            pg.goto(BASE + "/")
            pg.wait_for_timeout(800)
            # ensaios (um passa, outro falha porque o "site mudou")
            for bid in ("baixar-notas-do-fornecedor", "consultar-pedidos"):
                wait_job(pg, post(pg, f"/api/bots/{bid}/rehearse")["id"])
            post(pg, "/api/bots/baixar-notas-do-fornecedor/active", {"active": True})

            # ---------- escritório (PNG + GIF)
            off = ctx.new_page()
            off.goto(BASE + "/office")
            off.wait_for_timeout(2500)
            off.evaluate("nightLevel = () => 0")
            # o chefe delega (colegas levantam, entregam e voltam) e, em seguida, um bot fica trabalhando
            post(off, "/api/bots/chefe-financeiro/run")
            off.wait_for_timeout(400)
            post(off, "/api/bots/relatorio-diario/run")
            off.evaluate("select('relatorio-diario')")
            frames = []
            for i in range(70):
                off.wait_for_timeout(100)
                frames.append(off.screenshot(clip={"x": 0, "y": 0, "width": 1440, "height": 900}))
                if i == 14:
                    shot(off, "escritorio.png")
            from PIL import Image
            imgs = [Image.open(io.BytesIO(f)).convert("RGB").resize((960, 600), Image.LANCZOS) for f in frames]
            pal = imgs[len(imgs) // 2].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
            gif = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
            gif[0].save(OUT / "escritorio.gif", save_all=True, append_images=gif[1:], duration=110, loop=0, optimize=True)
            print("  ✓ escritorio.gif")
            off.close()

            # ---------- telas do sistema
            pg.reload()
            pg.wait_for_timeout(800)
            pg.evaluate("go('bots')")
            pg.wait_for_timeout(900)
            shot(pg, "bots.png")
            pg.evaluate("openBot('baixar-notas-do-fornecedor')")
            pg.wait_for_timeout(900)
            shot(pg, "robo-passos.png")
            pg.evaluate("openBot('consultar-pedidos')")
            pg.wait_for_timeout(900)
            pg.evaluate("document.querySelector('.st.bad').scrollIntoView({block: 'center'})")
            pg.wait_for_timeout(300)
            shot(pg, "ensaio-falhou.png")
            pg.evaluate("openAddStep()")
            pg.wait_for_timeout(600)
            pg.click("text=Enviar um e-mail")
            pg.wait_for_timeout(300)
            shot(pg, "adicionar-passo.png")
            pg.evaluate("closeModal(); go('creds')")
            pg.wait_for_timeout(700)
            shot(pg, "acessos.png")
            # ---------- construtor sem código
            bd = ctx.new_page()
            bd.on("dialog", lambda d: d.accept())
            bd.goto(BASE + "/builder")
            bd.wait_for_selector(".tpl")
            bd.wait_for_timeout(500)
            shot(bd, "construtor-modelos.png")
            bd.click("text=Boletos em PDF")
            bd.fill("#bn", "Boletos do mês")
            bd.fill("#bt", "Financeiro")
            bd.wait_for_timeout(800)
            bd.locator(".kids .step .hd").nth(1).click()
            bd.wait_for_timeout(700)
            shot(bd, "construtor.png")
            bd.click("#testb")
            bd.wait_for_selector(".banner.ok, .banner.bad", timeout=60000)
            bd.wait_for_timeout(1200)
            bd.locator(".step .hd").last.click()
            bd.wait_for_timeout(600)
            shot(bd, "construtor-teste.png")
            bd.close()
            # autorização em linguagem comum
            pg.evaluate("go('bots')")
            pg.wait_for_timeout(600)
            pg.evaluate("runBot('enviar-fechamento')")
            pg.wait_for_timeout(600)
            pg.click("#mbody button:has-text('Começar')")
            pg.wait_for_selector("text=precisa da sua permissão", timeout=20000)
            pg.wait_for_timeout(1500)
            shot(pg, "autorizacao.png")
            pg.click("text=✋ Não")
            pg.wait_for_timeout(500)
            # painel de dados gerado por um robô
            dash = ctx.new_page()
            dash.goto((config.workspace() / "painel.html").as_uri())
            dash.wait_for_timeout(1200)
            shot(dash, "painel-dados.png")
            dash.close()

            # ---------- revisão pelo padrinho (ativa login)
            auth.add_user("admin", "senha-forte-0", "admin")
            auth.add_user("maria", "senha-forte-1", "operator")
            auth.add_user("joao", "senha-forte-2", "operator", reviewer=True)

            def login(u, pw):
                c = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
                c.on("dialog", lambda d: d.accept("Primeiro robô do setor de limpeza!"))
                c.goto(BASE + "/")
                c.wait_for_timeout(500)
                c.fill("#lu", u)
                c.fill("#lp", pw)
                c.press("#lp", "Enter")
                c.wait_for_timeout(900)
                return c
            maria = login("maria", "senha-forte-1")
            post(maria, "/api/bots", {"name": "Checklist da limpeza", "team": "Facilities",
                                      "description": "Registra o checklist diário e avisa a gerência.",
                                      "yaml": "steps:\n  - tool: excel_write\n    args: {path: checklist.xlsx, sheets: {Limpeza: [[Sala, Feito], [Recepção, sim]]}}\n"
                                              "  - tool: email_send\n    args: {to: gerencia@empresa.com, subject: Checklist da limpeza, body: Segue., attachments: [checklist.xlsx]}\n"})
            post(maria, "/api/bots/checklist-da-limpeza/review/submit", {"note": "Primeiro robô do setor de limpeza!"})
            joao = login("joao", "senha-forte-2")
            joao.evaluate("go('bots')")
            joao.wait_for_timeout(800)
            joao.click("button:has-text('Revisar')")
            joao.wait_for_timeout(900)
            shot(joao, "revisao.png")
            b.close()
    finally:
        srv.terminate()
        site.terminate()
    print(f"Imagens em {OUT}")


if __name__ == "__main__":
    main()
