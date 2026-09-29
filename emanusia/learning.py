"""Aprendizado a partir do uso: cada tarefa do chat que dá certo vira "experiência".

1. Memória: tarefas parecidas já resolvidas entram como dica no prompt (ajuda muito modelos 7B).
2. Mineração: quando a MESMA sequência de ferramentas se repete N vezes (padrão 3), o sistema
   propõe um bot pronto, com os valores que mudam entre execuções virando parâmetros.
3. Auto-criação (opcional, em Ajustes): a proposta vira um bot em RASCUNHO automaticamente.
   Nunca é ativado sozinho: passa pela validação e por um humano."""
import hashlib
import json
import re
import threading
import time
import unicodedata

from . import auth, config, events

_lock = threading.Lock()
STOP = set("a o e de da do das dos em no na nos nas um uma para por com que se ao aos as os me "
           "meu minha pra favor the and of to".split())


def _dir():
    p = config.data_dir() / "memory"
    p.mkdir(exist_ok=True)
    return p


def tokens(text: str) -> set[str]:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in STOP}


def record_task(user: str, task: str, calls: list[dict], success: bool):
    """calls = [{name, args, ok}]. Só as chamadas bem-sucedidas entram na receita."""
    steps = [{"tool": c["name"], "args": c["args"]} for c in calls if c.get("ok")]
    if not steps:
        return None
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user, "task": task[:500],
           "success": bool(success), "steps": steps}
    with _lock, open(_dir() / "tasks.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    if success and config.settings().get("auto_create_bots"):
        try:
            auto_create()
        except Exception:  # aprendizado nunca derruba a tarefa
            pass
    return rec


def load_tasks() -> list[dict]:
    f = _dir() / "tasks.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def similar(task: str, k: int = 2, threshold: float = 0.3) -> list[dict]:
    q = tokens(task)
    if not q:
        return []
    scored = []
    for r in load_tasks():
        if not r["success"]:
            continue
        t = tokens(r["task"])
        score = len(q & t) / len(q | t) if t else 0
        if score >= threshold:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, r in scored:
        if r["task"] not in seen:
            seen.add(r["task"])
            out.append(r)
        if len(out) == k:
            break
    return out


# ---------------------------------------------------------------- mineração de padrões
def _state() -> dict:
    f = _dir() / "suggestions_state.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _save_state(s: dict):
    (_dir() / "suggestions_state.json").write_text(json.dumps(s, indent=2), encoding="utf-8")


def _parametrize(recs: list[dict]) -> tuple[dict, list]:
    """Mesma sequência de ferramentas em várias tarefas: valores iguais ficam fixos,
    valores (texto/número) que mudam viram {{parâmetro}} com o último valor como padrão."""
    params, steps, latest = {}, [], recs[-1]
    for i, step in enumerate(latest["steps"]):
        args = {}
        for key, val in step["args"].items():
            vals = [r["steps"][i]["args"].get(key) for r in recs]
            if all(v == vals[0] for v in vals) or not all(isinstance(v, str | int | float) for v in vals):
                args[key] = val
            else:
                name = key if key not in params else f"{key}_{i + 1}"
                params[name] = val
                args[key] = "{{" + name + "}}"
        steps.append({"tool": step["tool"], "args": args})
    return params, steps


def suggestions(min_repeats: int | None = None) -> list[dict]:
    from . import bots
    min_repeats = min_repeats or int(config.settings().get("suggest_min_repeats", 3))
    state = _state()
    existing = {bots.signature(b.code) for b in bots.list_bots()}
    groups: dict[tuple, list] = {}
    for r in load_tasks():
        if r["success"]:
            groups.setdefault(tuple(s["tool"] for s in r["steps"]), []).append(r)
    out = []
    for sig, recs in groups.items():
        if len(recs) < min_repeats:
            continue
        sid = hashlib.sha1("|".join(sig).encode()).hexdigest()[:10]
        if sig in existing or state.get(sid) in ("accepted", "dismissed", "auto"):
            continue
        params, steps = _parametrize(recs)
        words: dict[str, int] = {}
        for r in recs:
            for w in tokens(r["task"]):
                words[w] = words.get(w, 0) + 1
        top = [w for w, _ in sorted(words.items(), key=lambda x: (-x[1], x[0]))[:3]]
        out.append({"id": sid, "title": recs[-1]["task"][:120], "name": "Bot " + " ".join(top).title(),
                    "count": len(recs), "users": sorted({r["user"] for r in recs}),
                    "tools": list(sig), "code": {"params": params, "steps": steps}})
    return sorted(out, key=lambda s: -s["count"])


def _find(sid: str) -> dict:
    for s in suggestions(min_repeats=1):
        if s["id"] == sid:
            return s
    raise KeyError(sid)


def accept(sid: str, user: auth.User, name: str | None = None, team: str = "Geral", origin="sugestao"):
    from . import bots
    s = _find(sid)
    b = bots.create_bot(name or s["name"], user, s["code"], team,
                        description=f"Aprendido de {s['count']} tarefas: {s['title']}", origin=origin)
    bots.validate_bot(b.id, user)
    st = _state()
    st[sid] = "accepted" if origin == "sugestao" else "auto"
    _save_state(st)
    return bots.get_bot(b.id)


def dismiss(sid: str):
    st = _state()
    st[sid] = "dismissed"
    _save_state(st)


def auto_create() -> list[str]:
    from . import bots
    created = []
    for s in suggestions():
        name, n = s["name"], 2
        while bots.get_bot(bots.slug(name)):
            name, n = f"{s['name']} {n}", n + 1
        owner = auth.get_user(s["users"][0])
        if owner is None or not owner.can_run:  # usuário removido/somente leitura: só sugere
            continue
        b = accept(s["id"], owner, name, "Aprendizado", origin="auto")
        events.emit("learned", bot=b.id, text=f"Aprendi uma rotina nova: {b.name}")
        created.append(b.id)
    return created
