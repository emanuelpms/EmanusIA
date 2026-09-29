"""Ensinar mostrando + ensaio.

- teach(): a pessoa usa o site normalmente; os passos gravados viram um bot (rascunho) na hora.
- rehearse(): roda o bot de verdade tirando um print a cada passo. Se um passo falhar, a tela mostra
  qual foi, com o erro em português e o print do momento.
- fix_step(): "Mostrar onde fica" — o robô refaz os passos anteriores num navegador visível, e o
  primeiro clique da pessoa ensina o novo lugar do passo que falhou."""
import os
import re
import shutil
import threading
import time

from . import auth, bots, config, events, plain
from .engine import Runner, code_hash
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

    def on_step(path, step, ok, res):
        n = len(steps) + 1
        shot = f"{n}.jpg" if _screenshot(d / f"{n}.jpg") else None
        steps.append({"path": path, "text": plain.describe_step(step), "ok": ok, "shot": shot,
                      "error": None if ok else plain.explain_error(res, step)})

    runner = Runner(user, confirm, b.id, emit, on_step=on_step)
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
  const b = document.createElement('div'); b.id = '__emanusia_banner';
  b.textContent = '🤖 Clique no lugar certo para me ensinar';
  b.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1f3a5f;color:#fff;padding:10px 18px;border-radius:10px;font:600 15px system-ui;box-shadow:0 4px 18px #0006';
  document.body.appendChild(b);
}"""


def fix_step(bot_id: str, path: str, user: auth.User, stop: threading.Event, confirm=None,
             _simulate_click: str | None = None) -> dict:
    """Refaz os passos anteriores ao `path` (só passos do nível principal) e captura o 1º clique."""
    from .tools import web
    b = bots.get_bot(bot_id)
    if b is None:
        raise KeyError(bot_id)
    if not bots.can_edit(b, user):
        raise PermissionError("sem permissão")
    if not re.fullmatch(r"\d+", str(path)):
        raise ValueError("só dá para ensinar de novo passos do nível principal")
    idx = int(path) - 1
    steps = b.code.get("steps") or []
    if not (0 <= idx < len(steps)) or "tool" not in steps[idx]:
        raise ValueError("passo inválido")
    old_headless = config.HEADLESS
    config.HEADLESS = os.environ.get("EMANUSIA_HEADLESS") == "1"  # normalmente visível: a pessoa clica
    web.web_close()
    try:
        Runner(user, confirm, b.id).run({"params": b.code.get("params") or {}, "steps": steps[:idx]})
        page = web._get_page()
        got: dict = {}
        fn = f"__emanusia_pick_{int(time.time() * 1000)}"
        page.expose_binding(fn, lambda src, e: got.update(e))
        page.evaluate(CAPTURE_JS, fn)
        if _simulate_click:  # testes automatizados
            page.click(_simulate_click)
        t0 = time.time()
        while not got and not stop.is_set() and time.time() - t0 < TEACH_TIMEOUT:
            page.wait_for_timeout(250)
        if not got:
            raise TimeoutError("Ninguém clicou: tente de novo e clique no lugar certo.")
    finally:
        config.HEADLESS = old_headless
        web.web_close()
    new_step = dict(steps[idx])
    new_step["args"] = {**(new_step.get("args") or {}), "selector": got["selector"]}
    if got.get("label"):
        new_step["label"] = got["label"]
    code = {**b.code, "steps": steps[:idx] + [new_step] + steps[idx + 1:]}
    bots.update_bot(b.id, user, code=code)
    bots.validate_bot(b.id, user)
    return {"path": path, "selector": got["selector"], "label": got.get("label"),
            "text": plain.describe_step(new_step)}
