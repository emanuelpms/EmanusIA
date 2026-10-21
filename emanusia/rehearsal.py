"""Ensinar mostrando + ensaio.

- teach(): a pessoa usa o site normalmente; os passos gravados viram um bot (rascunho) na hora.
- rehearse(): roda o bot de verdade tirando um print a cada passo. Se um passo falhar, a tela mostra
  qual foi, com o erro em português e o print do momento.
- fix_step(): "Mostrar onde fica" — o robô refaz o que vem antes do passo (em qualquer nível: dentro
  de "se", "repetir", "tentar") num navegador visível, e o primeiro clique da pessoa ensina o lugar novo.
- record_insert(): "Adicionar passos mostrando" — o robô refaz os passos até um ponto e grava o que a
  pessoa fizer a partir dali, inserindo no meio do robô.
- add_simple_step(): ações prontas por formulário (e-mail, esperar, abrir site, tabela → planilha...)."""
import os
import shutil
import threading
import time

from . import auth, bots, config, events, plain
from .engine import Runner, StopBefore, code_hash, get_step, insert_steps, replace_step
from .recorder import SELECTOR_JS

TEACH_TIMEOUT = 180


def shots_dir(bot_id: str):
    d = config.data_dir() / "rehearsals" / bots.slug(bot_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _screenshot(path) -> bool:
    from .tools import web
    if not web._pages:
        return False
    try:
        web._get_page().screenshot(path=str(path), type="jpeg", quality=60)
        return True
    except Exception:
        return False


def teach(url: str, name: str, user: auth.User, stop: threading.Event, team: str = "Geral",
          description: str = "", credential: str = "") -> bots.Bot:
    from . import vault
    from .recorder import record
    username = ""
    if credential:
        username = vault.resolve(user.name, credential)["username"]
    code = record(url, None, stop, credential, username)
    if len(code["steps"]) <= 1:
        raise ValueError("Não gravei nenhuma ação: clique e preencha o site antes de terminar.")
    b = bots.create_bot(name, user, code, team, description=description or f"Aprendido mostrando em {url}",
                        origin="gravacao")
    bots.validate_bot(b.id, user)
    return bots.get_bot(b.id)


def rehearse(bot_id: str, user: auth.User, confirm=None, params: dict | None = None,
             emit=None) -> dict:
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not bots.can_edit(b, user):
        raise PermissionError("só quem cuida do bot faz o ensaio")
    d = shots_dir(b.id)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    code = b.code

    started = {"t": time.time()}

    def track(kind, ev):  # marca o início de cada passo (para medir quanto tempo levou)
        if kind == "step":
            started["t"] = time.time()
        if emit:
            emit(kind, ev)

    def on_step(path, step, ok, res):
        n = len(steps) + 1
        shot = f"{n}.jpg" if _screenshot(d / f"{n}.jpg") else None
        item = {"path": path, "text": plain.describe_step(step), "ok": ok, "shot": shot,
                "error": None if ok else plain.explain_error(res, step),
                "secs": round(time.time() - started["t"], 2),
                "preview": plain.preview(res) if ok else None}
        steps.append(item)
        if emit:
            emit("step_done", {"kind": "step_done", **item})

    runner = Runner(user, confirm, b.id, track, on_step=on_step)
    events.emit("job_start", bot=b.id, text="ensaio")
    ok, err = True, None
    try:
        runner.run({**code, "name": ""}, params)
    except Exception as e:
        ok, err = False, f"{type(e).__name__}: {e}"
        failed = runner.failed_at
        if not steps or steps[-1]["ok"] or steps[-1]["path"] != failed:  # falhou fora de uma ferramenta
            step = plain._step_at(code, failed) or {}
            shot = "fim.jpg" if _screenshot(d / "fim.jpg") else None
            steps.append({"path": failed, "text": plain.describe_step(step) if step else "Robô",
                          "ok": False, "shot": shot, "error": plain.explain_error(err, step)})
    finally:
        from .tools import web
        web.web_close()
    rep = {"ok": ok, "hash": code_hash(code), "at": time.strftime("%Y-%m-%d %H:%M"), "by": user.name,
           "steps": steps, "failed_at": None if ok else runner.failed_at,
           "error": None if ok else plain.explain_error(err, plain._step_at(code, runner.failed_at))}
    # o código pode ter mudado durante o ensaio: só grava se ainda é o mesmo
    if code_hash(bots.get_bot(b.id).code) == rep["hash"]:
        bots.record_rehearsal(b.id, rep)
    events.emit("job_done", bot=b.id, ok=ok, text="" if ok else rep["error"][:120])
    return rep


CAPTURE_JS = r"""(name) => {
""" + SELECTOR_JS + r"""
  const done = e => { document.removeEventListener('click', h, true); window[name](e); };
  const h = ev => { ev.preventDefault(); ev.stopPropagation();
    const el = ev.target.closest('a,button,input,select,textarea,label,[role=button],[onclick]') || ev.target;
    done({selector: sel(el), label: label(el)}); };
  document.addEventListener('click', h, true);
  if (window === window.top) {
    const b = document.createElement('div'); b.id = '__emanusia_banner';
    b.textContent = '🤖 Clique no lugar certo para me ensinar';
    b.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1f3a5f;color:#fff;padding:10px 18px;border-radius:10px;font:600 15px system-ui;box-shadow:0 4px 18px #0006';
    document.body.appendChild(b);
  }
}"""


def _replay_until(b: bots.Bot, user: auth.User, confirm, stop_before: str | None, prefix: int | None = None):
    """Abre um navegador (visível, para a pessoa agir) e refaz o robô até logo antes de
    `stop_before` (em qualquer nível) ou só os `prefix` primeiros passos. O navegador fica aberto."""
    from .tools import web
    web.web_close()
    config.HEADLESS = os.environ.get("EMANUSIA_HEADLESS") == "1"
    code = b.code if prefix is None else {"params": b.code.get("params") or {},
                                          "steps": (b.code.get("steps") or [])[:prefix]}
    try:
        Runner(user, confirm, b.id, stop_before=stop_before).run({**code, "name": ""})
    except StopBefore:
        return web._get_page()
    if stop_before:
        raise ValueError("O robô terminou sem chegar nesse passo (ele pode estar dentro de um “se” que "
                         "não aconteceu desta vez).")
    return web._get_page()


def fix_step(bot_id: str, path: str, user: auth.User, stop: threading.Event, confirm=None,
             _simulate_click: str | None = None) -> dict:
    """Refaz o robô até o passo `path` e aprende o lugar novo com o 1º clique da pessoa."""
    from .tools import web
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not bots.can_edit(b, user):
        raise PermissionError("sem permissão")
    try:
        step = get_step(b.code, str(path))
    except (KeyError, ValueError, IndexError):
        raise ValueError("passo inválido")
    if "tool" not in step:
        raise ValueError("esse passo não é um clique ou campo da tela")
    old_headless = config.HEADLESS
    try:
        page = _replay_until(b, user, confirm, str(path))
        got: dict = {}
        fn = f"__emanusia_pick_{int(time.time() * 1000)}"
        page.expose_binding(fn, lambda src, e: got.update(e))
        for fr in page.frames:  # o passo pode estar dentro de um quadro interno (iframe)
            try:
                fr.evaluate(CAPTURE_JS, fn)
            except Exception:
                pass
        if _simulate_click:  # testes automatizados
            web._loc(_simulate_click).click()
        t0 = time.time()
        while not got and not stop.is_set() and time.time() - t0 < TEACH_TIMEOUT:
            page.wait_for_timeout(250)
        if not got:
            raise TimeoutError("Ninguém clicou: tente de novo e clique no lugar certo.")
    finally:
        config.HEADLESS = old_headless
        web.web_close()
    new_step = {**step, "args": {**(step.get("args") or {}), "selector": got["selector"]}}
    if got.get("label"):
        new_step["label"] = got["label"]
    bots.update_bot(b.id, user, code=replace_step(b.code, str(path), new_step))
    bots.validate_bot(b.id, user)
    return {"path": path, "selector": got["selector"], "label": got.get("label"),
            "text": plain.describe_step(new_step)}


def record_insert(bot_id: str, after: int, user: auth.User, stop: threading.Event, url: str = "",
                  confirm=None, _simulate=None) -> dict:
    """"Adicionar passos mostrando": refaz os `after` primeiros passos, deixa a pessoa continuar a
    tarefa no navegador e insere o que ela fizer depois do passo `after`."""
    from .recorder import Recorder
    from .tools import web
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not bots.can_edit(b, user):
        raise PermissionError("sem permissão")
    after = max(0, min(int(after), len(b.code.get("steps") or [])))
    old_headless = config.HEADLESS
    try:
        page = _replay_until(b, user, confirm, None, prefix=after)
        opened = False
        if url and (page.url in ("", "about:blank") or after == 0):
            page.goto(url)
            opened = True
        if page.url in ("", "about:blank"):
            raise ValueError("Informe em qual site os novos passos acontecem.")
        in_frame = list(web._frames)
        rec = Recorder(page.url)
        rec.attach(web._ctx, [page])
        if _simulate:  # testes automatizados
            _simulate(page)
            page.wait_for_timeout(200)
            stop.set()
        t0 = time.time()
        while not stop.is_set() and time.time() - t0 < 30 * 60:
            try:
                page.wait_for_timeout(300)
            except Exception:  # a pessoa fechou a janela: vale como "terminei"
                break
        new = rec.steps(with_open=opened)
    finally:
        config.HEADLESS = old_headless
        web.web_close()
    if not new:
        raise ValueError("Não gravei nenhuma ação nova.")
    if in_frame and new[0]["tool"] != "web_frame":
        new.insert(0, {"tool": "web_frame", "args": {"selector": ""}})  # a gravação começa na página principal
    if any(s["tool"] == "web_frame" and s["args"]["selector"] for s in new) and \
            not (new[-1]["tool"] == "web_frame" and not new[-1]["args"]["selector"]):
        new.append({"tool": "web_frame", "args": {"selector": ""}})  # os passos seguintes voltam à página principal
    bots.update_bot(b.id, user, code=insert_steps(b.code, after, new))
    bots.validate_bot(b.id, user)
    return {"added": len(new), "after": after, "steps": [plain.describe_step(s) for s in new]}


SIMPLE_STEPS = {
    "email": "Enviar um e-mail", "open": "Abrir um site", "wait": "Esperar alguns segundos",
    "note": "Anotar um recado no histórico", "table_to_excel": "Salvar a tabela da página numa planilha",
    "dashboard": "Montar um painel a partir de uma planilha",
}


def add_simple_step(bot_id: str, user: auth.User, kind: str, after: int, fields: dict) -> dict:
    """Ações prontas para quem não sabe programar (formulário → passo)."""
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not bots.can_edit(b, user):
        raise PermissionError("sem permissão")
    f = {k: str(v).strip() for k, v in (fields or {}).items() if v is not None}
    n = len(b.code.get("steps") or []) + 1
    if kind == "email":
        new = [{"tool": "email_send", "args": {"to": f.get("to", ""), "subject": f.get("subject", ""),
                "body": f.get("body", ""), **({"attachments": [f["attachment"]]} if f.get("attachment") else {})}}]
    elif kind == "wait":
        try:
            secs = int(float(f.get("seconds") or 5))
        except ValueError:
            raise ValueError("informe os segundos em número")
        new = [{"sleep": max(1, min(secs, 300))}]
    elif kind == "open":
        url = f.get("url", "")
        new = [{"tool": "web_open", "args": {"url": url if "://" in url else "https://" + url}}]
    elif kind == "note":
        new = [{"log": f.get("text", "")}]
    elif kind == "table_to_excel":
        var = f"tabela_{n}"
        new = [{"tool": "web_table", "args": {"selector": f.get("selector") or "table"}, "save_as": var},
               {"tool": "excel_write", "args": {"path": f.get("path") or f"tabela_{n}.xlsx",
                                                "sheets": {"Dados": "{{" + var + "}}"}}}]
    elif kind == "dashboard":
        new = [{"tool": "dashboard_data", "args": {"path": f.get("path") or "painel.html",
                "source": f.get("source", ""), "title": f.get("title") or "Painel"}}]
    else:
        raise ValueError("tipo de passo desconhecido")
    bots.update_bot(b.id, user, code=insert_steps(b.code, after, new))
    rep = bots.validate_bot(b.id, user)
    return {"added": len(new), "ok": rep["ok"],
            "plain_errors": plain.explain_validation(rep, bots.get_bot(b.id).code)}
