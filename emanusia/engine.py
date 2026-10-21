"""Motor de automação (a "linguagem" dos bots). YAML declarativo, validado antes de rodar.

Tipos de passo:
  - tool: web_click                 # chama uma ferramenta
    args: {selector: "#ok"}
    save_as: resultado              # guarda o retorno em uma variável
    if: "len(linhas) > 1"           # (opcional) só executa se a condição for verdadeira
    retry: 3                        # (opcional) tenta de novo em caso de erro
    retry_delay: 2                  # segundos entre tentativas
    on_error: continue              # stop (padrão) | continue  -> save_as recebe {error: ...}
  - if: "total > 1000"              # condicional
    then: [...]
    else: [...]
  - for_each: "pedidos[1:]"         # laço (as: item; loop_index disponível)
    as: pedido
    steps: [...]
  - while: "pagina < 10 and tem_proxima"
    max: 50
    steps: [...]
  - try: [...]                      # tratamento de erro
    catch: [...]                    # variável `erro` com a mensagem
  - set: {total: "=len(linhas) - 1", arquivo: "rel_{{data}}.xlsx"}   # "=" avalia expressão (só no set)
  - call: outro-workflow            # subfluxo (params, save_as)
  - call_bot: bot-financeiro        # passa o trabalho para outro bot (params, save_as)
  - log: "Processando {{pedido.0}}"
  - sleep: 2
  - fail: "Mensagem de erro"

Expressões: aritmética, comparações, and/or/not, in, índices/fatias e as funções
len int float str round sum min max abs bool lower upper strip contains range title replace split
join startswith endswith zfill regex_find to_number money today now date_add format_date
days_between column records get first last unique sorted.
Não há acesso a atributos, imports nem código Python arbitrário.
"""
import ast
import hashlib
import re
import time

import yaml

from . import auth, config, events
from .tools import registry

MAX_DEPTH = 8
MAX_LOOP = 10_000
_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_SECRET_ARG = re.compile(r"pass|senha|secret|token|api_?key", re.I)
STEP_KEYS = {"tool", "if", "for_each", "while", "try", "set", "call", "call_bot", "log", "sleep", "fail"}
KNOWN_KEYS = STEP_KEYS | {"args", "save_as", "retry", "retry_delay", "on_error", "then", "else",
                          "as", "steps", "max", "catch", "params", "name", "comment", "label"}


class WorkflowError(Exception):
    pass


class StopBefore(Exception):
    """Execução interrompida de propósito logo antes de um passo (consertar/inserir mostrando)."""


# ------------------------------------------------------------------ caminhos de passos
# "3" = 3º passo; "3.2" = 2º passo dentro do 3º (então/repetir/tentar); "3.else.1", "3.catch.1".
def _children_key(step: dict, seg: str | None) -> str | None:
    if seg in ("else", "catch"):
        return seg
    for k in ("then", "steps", "try"):
        if k in step:
            return k
    return None


def _locate(code: dict, path: str):
    """Devolve (lista_que_contém_o_passo, índice)."""
    parts = str(path).split(".")
    lst, i, seg = code.get("steps") or [], None, None
    for n, part in enumerate(parts):
        if part in ("else", "catch"):
            seg = part
            continue
        if i is not None:
            key = _children_key(lst[i], seg)
            if key is None:
                raise KeyError(path)
            lst = lst[i].setdefault(key, [])
            seg = None
        i = int(part) - 1
        if not (0 <= i < len(lst)) and not (n == len(parts) - 1):
            raise KeyError(path)
    return lst, i


def get_step(code: dict, path: str) -> dict:
    lst, i = _locate(code, path)
    if not (0 <= i < len(lst)):
        raise KeyError(path)
    return lst[i]


def replace_step(code: dict, path: str, new: dict) -> dict:
    import copy
    code = copy.deepcopy(code)
    lst, i = _locate(code, path)
    if not (0 <= i < len(lst)):
        raise KeyError(path)
    lst[i] = new
    return code


def insert_steps(code: dict, after: int, new: list) -> dict:
    """Insere passos no nível principal depois do passo `after` (0 = no começo)."""
    import copy
    code = copy.deepcopy(code)
    steps = code.setdefault("steps", [])
    after = max(0, min(int(after), len(steps)))
    steps[after:after] = copy.deepcopy(new)
    return code


# ------------------------------------------------------------------ expressões seguras
def _rng(*a):
    r = range(*a)
    if len(r) > MAX_LOOP:
        raise ValueError("range grande demais")
    return r


def _date(v):
    from datetime import date, datetime
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M",
                "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    raise ValueError(f"data inválida: {v}")


def _fmt_date(v, fmt="%d/%m/%Y"):
    return _date(v).strftime(str(fmt))


def _today(fmt="%Y-%m-%d"):
    import datetime as dt
    return dt.datetime.now().strftime(str(fmt))


def _date_add(v, days=0, months=0, fmt="%Y-%m-%d"):
    import calendar
    import datetime as dt
    d = _date(v) + dt.timedelta(days=float(days))
    if months:
        m = d.month - 1 + int(months)
        y, m = d.year + m // 12, m % 12 + 1
        d = d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))
    return d.strftime(str(fmt))


def _to_number(v):
    """'R$ 1.234,56' -> 1234.56; '12%' -> 12.0; números passam direto; vazio -> 0."""
    if isinstance(v, bool | int | float):
        return v
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in "-.,":
        return 0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    n = float(s)
    return int(n) if n.is_integer() and "." not in s else n


def _money(v, symbol="R$ "):
    n = float(_to_number(v))
    txt = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if n < 0 else ''}{symbol}{txt}"


def _regex_find(text, pattern, group=0):
    m = re.search(str(pattern), str(text), re.I)
    return (m.group(int(group)) if m else "")


def _full(table):
    """Tabela completa de um resultado de dados (que na tela mostra só uma amostra)."""
    df = getattr(table, "df", None)
    if df is not None:
        from .tools.data import rows
        return rows(df, None)
    return table


def _column(table, name):
    """Valores de uma coluna de uma tabela ([[cabeçalho], [linha]...] ou [{coluna: valor}])."""
    table = _full(table)
    if isinstance(table, dict) and "rows" in table:
        table = table["rows"]
    if not table:
        return []
    if isinstance(table[0], dict):
        return [r.get(name) for r in table]
    idx = [str(c) for c in table[0]].index(str(name))
    return [r[idx] if idx < len(r) else None for r in table[1:]]


def _records(table):
    """Tabela -> lista de {coluna: valor} (para repetir linha a linha usando os nomes das colunas)."""
    table = _full(table)
    if isinstance(table, dict) and "rows" in table:
        table = table["rows"]
    if not table or isinstance(table[0], dict):
        return list(table or [])
    head = [str(c) for c in table[0]]
    return [dict(zip(head, r)) for r in table[1:]]


def _get(obj, key, default=None):
    try:
        return obj[key] if not isinstance(obj, list) else obj[int(key)]
    except (KeyError, IndexError, TypeError, ValueError):
        return default


FUNCS = {"len": len, "int": int, "float": float, "str": str, "round": round, "sum": sum,
         "min": min, "max": max, "abs": abs, "bool": bool, "range": _rng,
         "lower": lambda s: str(s).lower(), "upper": lambda s: str(s).upper(),
         "strip": lambda s: str(s).strip(), "contains": lambda a, b: b in a,
         "title": lambda s: str(s).title(), "replace": lambda s, a, b: str(s).replace(str(a), str(b)),
         "split": lambda s, sep=None: str(s).split(sep), "join": lambda lst, sep=", ": str(sep).join(map(str, lst)),
         "startswith": lambda s, p: str(s).startswith(str(p)), "endswith": lambda s, p: str(s).endswith(str(p)),
         "zfill": lambda s, n: str(s).zfill(int(n)), "regex_find": _regex_find,
         "to_number": _to_number, "money": _money, "today": _today, "date_add": _date_add,
         "format_date": _fmt_date, "days_between": lambda a, b: (_date(b) - _date(a)).days,
         "column": _column, "records": _records, "get": _get, "first": lambda l: l[0] if l else None,
         "last": lambda l: l[-1] if l else None, "unique": lambda l: list(dict.fromkeys(l)),
         "sorted": lambda l: sorted(l), "now": lambda: _today("%Y-%m-%d %H:%M")}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b, ast.FloorDiv: lambda a, b: a // b}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_ALLOWED = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BoolOp, ast.And, ast.Or,
            ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp, ast.Compare, ast.Subscript,
            ast.Slice, ast.List, ast.Tuple, ast.Dict, ast.Call, ast.IfExp, *_BIN, *_CMP)


def check_expr(expr: str) -> ast.Expression:
    """Analisa e valida a expressão (sem executar). Lança WorkflowError se não for permitida."""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError as e:
        raise WorkflowError(f"Expressão inválida '{expr}': {e.msg}")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise WorkflowError(f"Expressão '{expr}': '{type(node).__name__}' não é permitido")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                                           or node.func.id not in FUNCS or node.keywords):
            raise WorkflowError(f"Expressão '{expr}': só as funções {', '.join(FUNCS)}")
    return tree


def safe_eval(expr, ctx: dict):
    if isinstance(expr, bool | int | float):
        return expr
    return _ev(check_expr(expr).body, ctx)


def _ev(n, ctx):
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id in ctx:
            return ctx[n.id]
        raise WorkflowError(f"Variável '{n.id}' não definida")
    if isinstance(n, ast.BoolOp):
        vals = (_ev(v, ctx) for v in n.values)
        return all(vals) if isinstance(n.op, ast.And) else any(vals)
    if isinstance(n, ast.UnaryOp):
        v = _ev(n.operand, ctx)
        return (not v) if isinstance(n.op, ast.Not) else (-v if isinstance(n.op, ast.USub) else +v)
    if isinstance(n, ast.BinOp):
        a, b = _ev(n.left, ctx), _ev(n.right, ctx)
        if isinstance(n.op, ast.Mult) and any(isinstance(x, str | list) for x in (a, b)) \
                and any(isinstance(x, int) and x > MAX_LOOP for x in (a, b)):
            raise WorkflowError("Multiplicação grande demais")
        return _BIN[type(n.op)](a, b)
    if isinstance(n, ast.Compare):
        left = _ev(n.left, ctx)
        for op, comp in zip(n.ops, n.comparators):
            right = _ev(comp, ctx)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(n, ast.Subscript):
        v = _ev(n.value, ctx)
        s = n.slice
        if isinstance(s, ast.Slice):
            return v[slice(*(None if x is None else _ev(x, ctx) for x in (s.lower, s.upper, s.step)))]
        return v[_ev(s, ctx)]
    if isinstance(n, ast.List | ast.Tuple):
        return [_ev(e, ctx) for e in n.elts]
    if isinstance(n, ast.Dict):
        return {_ev(k, ctx): _ev(v, ctx) for k, v in zip(n.keys, n.values)}
    if isinstance(n, ast.Call):
        return FUNCS[n.func.id](*(_ev(a, ctx) for a in n.args))
    if isinstance(n, ast.IfExp):
        return _ev(n.body, ctx) if _ev(n.test, ctx) else _ev(n.orelse, ctx)
    raise WorkflowError(f"Nó não suportado: {type(n).__name__}")


# ------------------------------------------------------------------ templates
def _lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        try:
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise WorkflowError(f"Variável '{path}' não encontrada")
    return cur


def render(value, ctx: dict, exprs: bool = False):
    """Substitui {{var}}; se a string for só um placeholder, preserva o tipo original.
    Com exprs=True (só no passo `set`), strings que começam com '=' são avaliadas como expressão —
    nos args das ferramentas '=' é texto normal (ex.: fórmulas do Excel)."""
    if isinstance(value, str):
        if exprs and value.startswith("="):
            return safe_eval(value[1:], ctx)
        m = _VAR.fullmatch(value.strip())
        if m:
            return _lookup(ctx, m.group(1))
        return _VAR.sub(lambda m: str(_lookup(ctx, m.group(1))), value)
    if isinstance(value, list):
        return [render(v, ctx, exprs) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx, exprs) for k, v in value.items()}
    return value


# ------------------------------------------------------------------ validação estática
def code_hash(wf: dict) -> str:
    return hashlib.sha256(yaml.safe_dump(wf, sort_keys=True, allow_unicode=True).encode()).hexdigest()[:16]


def validate(wf, user: auth.User | None = None, exists_workflow=None, exists_bot=None) -> dict:
    """Checa estrutura, ferramentas, argumentos, permissões, expressões e segredos no código.
    Retorna {ok, errors, warnings, risky_tools, tools, calls, bots}."""
    user = user or auth.LOCAL_ADMIN
    rep = {"errors": [], "warnings": [], "risky_tools": set(), "tools": set(), "calls": set(),
           "bots": set()}
    err, warn = rep["errors"].append, rep["warnings"].append
    if exists_workflow is None:
        def exists_workflow(n):
            from .tools.workflow import _path
            try:
                return _path(n).exists()
            except ValueError:
                return False
    if exists_bot is None:
        def exists_bot(n):
            from . import bots
            return bots.get_bot(n) is not None

    def expr(e, where):
        if isinstance(e, bool | int | float):
            return
        try:
            check_expr(e)
        except WorkflowError as x:
            err(f"{where}: {x}")

    def templates(v, where):
        if isinstance(v, str):
            if v.startswith("="):
                expr(v[1:], where)
        elif isinstance(v, list):
            for x in v:
                templates(x, where)
        elif isinstance(v, dict):
            for x in v.values():
                templates(x, where)

    def steps(lst, path, depth):
        if depth > MAX_DEPTH:
            err(f"{path}: aninhamento excessivo (máx {MAX_DEPTH})")
            return
        if not isinstance(lst, list):
            err(f"{path}: deveria ser uma lista de passos")
            return
        for i, s in enumerate(lst, 1):
            where = f"{path}[{i}]"
            if not isinstance(s, dict):
                err(f"{where}: passo deve ser um objeto")
                continue
            kinds = [k for k in s if k in STEP_KEYS and k != "if"] or (["if"] if "if" in s else [])
            if len(kinds) != 1:
                err(f"{where}: defina exatamente um tipo de passo ({', '.join(sorted(STEP_KEYS))})")
                continue
            for k in s:
                if k not in KNOWN_KEYS:
                    warn(f"{where}: chave desconhecida '{k}' será ignorada")
            kind = kinds[0]
            if "if" in s:
                expr(s["if"], f"{where}.if")
            if s.get("on_error", "stop") not in ("stop", "continue"):
                err(f"{where}: on_error deve ser 'stop' ou 'continue'")
            if kind == "tool":
                _check_tool(s, where, user, rep)
            elif kind == "if":
                steps(s.get("then", []), f"{where}.then", depth + 1)
                steps(s.get("else", []), f"{where}.else", depth + 1)
            elif kind in ("for_each", "while"):
                expr(s[kind], f"{where}.{kind}")
                steps(s.get("steps"), f"{where}.steps", depth + 1)
            elif kind == "try":
                steps(s["try"], f"{where}.try", depth + 1)
                steps(s.get("catch", []), f"{where}.catch", depth + 1)
            elif kind == "set":
                if not isinstance(s["set"], dict):
                    err(f"{where}: set deve ser um objeto {{variavel: valor}}")
                else:
                    templates(s["set"], where)
            elif kind == "call":
                rep["calls"].add(s["call"])
                if not exists_workflow(s["call"]):
                    err(f"{where}: workflow '{s['call']}' não existe")
                elif not user.may_run_workflow(s["call"]):
                    err(f"{where}: sem permissão no workflow '{s['call']}'")
            elif kind == "call_bot":
                rep["bots"].add(s["call_bot"])
                if not exists_bot(s["call_bot"]):
                    err(f"{where}: bot '{s['call_bot']}' não existe")
            elif kind == "sleep" and not isinstance(s["sleep"], int | float):
                err(f"{where}: sleep deve ser número de segundos")

    if not isinstance(wf, dict):
        err("O código do bot deve ser um objeto YAML com 'steps:'")
    else:
        if not isinstance(wf.get("params", {}), dict):
            err("params deve ser um objeto")
        steps(wf.get("steps"), "steps", 0)
    for k in ("risky_tools", "tools", "calls", "bots"):
        rep[k] = sorted(rep[k])
    rep["ok"] = not rep["errors"]
    return rep


def _check_tool(s, where, user, rep):
    try:
        t = registry.get(s["tool"])
    except KeyError:
        rep["errors"].append(f"{where}: ferramenta '{s['tool']}' não existe")
        return
    rep["tools"].add(t.name)
    if not user.may_use_tool(t.name):
        rep["errors"].append(f"{where}: sem permissão para '{t.name}'")
    args = s.get("args", {}) or {}
    if not isinstance(args, dict):
        rep["errors"].append(f"{where}: args deve ser um objeto")
        return
    for a in args:
        if a not in t.parameters:
            rep["errors"].append(f"{where}: '{t.name}' não aceita o argumento '{a}' "
                                 f"(aceita: {', '.join(t.parameters) or 'nenhum'})")
    for a in t.required:
        if a not in args:
            rep["errors"].append(f"{where}: '{t.name}' exige o argumento '{a}'")
    for a, v in args.items():
        if _SECRET_ARG.search(a) and isinstance(v, str) and v and "{{" not in v:
            rep["errors"].append(f"{where}: segredo escrito no código ('{a}'). Use variável de "
                                 f"ambiente (ex.: web_fill_secret)")
    if t.name == "web_fill" and re.search(r"pass|senha", str(args.get("selector", "")), re.I):
        rep["errors"].append(f"{where}: campo de senha preenchido com texto fixo; use web_fill_secret")
    cred = args.get("credential")
    if isinstance(cred, str) and "{{" not in cred:
        from . import vault
        if not vault.exists(user.name, cred):
            rep["warnings"].append(f"{where}: acesso '{cred}' não está mapeado para '{user.name}' "
                                   f"(cada usuário que rodar o bot precisa cadastrá-lo em Acessos)")
    risky = t.is_risky(args) if not any("{{" in str(v) for v in args.values()) else bool(t.risky)
    if risky:
        rep["risky_tools"].add(t.name)


# ------------------------------------------------------------------ execução
class Runner:
    def __init__(self, user: auth.User, confirm=None, bot: str | None = None, emit=None, depth=0,
                 on_step=None, stop_before: str | None = None):
        self.user, self.confirm, self.bot, self.depth = user, confirm, bot, depth
        self._emit = emit
        self.log: list[dict] = []
        # on_step(caminho, passo, ok, resultado|erro): usado pelo ensaio (print + status de cada passo)
        self.on_step = on_step
        self._path: list[int] = []
        self.failed_at: str | None = None  # caminho do passo que interrompeu a execução ("3", "5.2")
        self.stop_before = stop_before     # para logo antes deste passo (o navegador fica onde está)

    @property
    def path(self) -> str:
        return ".".join(map(str, self._path))

    def emit(self, kind: str, **data):
        ev = events.emit(kind, bot=self.bot, **data)
        if self._emit:
            self._emit(kind, ev)

    def run(self, wf: dict, params: dict | None = None) -> dict:
        if self.depth > MAX_DEPTH:
            raise WorkflowError("Chamadas aninhadas demais (possível recursão entre bots/workflows)")
        ctx = {**(wf.get("params") or {}), **(params or {})}
        # as ferramentas leem o usuário do contexto (ex.: cofre de acessos usa o acesso de quem roda)
        tu, tc = auth.CURRENT_USER.set(self.user), auth.CURRENT_CONFIRM.set(self.confirm)
        try:
            self._steps(wf.get("steps") or [], ctx, wf.get("name", ""))
        finally:
            auth.CURRENT_USER.reset(tu)
            auth.CURRENT_CONFIRM.reset(tc)
        base = set(wf.get("params") or {}) | set(params or {})
        return {"steps": self.log, "vars": [k for k in ctx if k not in base and not k.startswith("_")],
                "outputs": {k: v for k, v in ctx.items() if k not in base and k not in ("erro", "loop_index")
                            and isinstance(v, str | int | float | bool)}}

    def _steps(self, steps: list, ctx: dict, wf_name: str, seg: str | None = None):
        if seg:
            self._path.append(seg)
        try:
            for i, s in enumerate(steps, 1):
                self._path.append(i)
                try:
                    if self.stop_before and self.path == self.stop_before:
                        raise StopBefore(self.path)
                    if "tool" in s and "if" in s and not safe_eval(s["if"], ctx):
                        continue
                    self._step(s, ctx, wf_name)
                except StopBefore:
                    raise
                except Exception:
                    if self.failed_at is None:
                        self.failed_at = self.path
                    raise
                finally:
                    self._path.pop()
        finally:
            if seg:
                self._path.pop()

    def _step(self, s: dict, ctx: dict, wf_name: str):
        if "tool" in s:
            return self._tool(s, ctx, wf_name)
        if "if" in s:
            if safe_eval(s["if"], ctx):
                return self._steps(s.get("then") or [], ctx, wf_name)
            return self._steps(s.get("else") or [], ctx, wf_name, seg="else")
        if "for_each" in s:
            items = safe_eval(s["for_each"], ctx) if isinstance(s["for_each"], str) else s["for_each"]
            for i, item in enumerate(list(items)[:MAX_LOOP]):
                ctx[s.get("as", "item")], ctx["loop_index"] = item, i
                self._steps(s.get("steps") or [], ctx, wf_name)
            return
        if "while" in s:
            limit = min(int(s.get("max", 100)), MAX_LOOP)
            n = 0
            while safe_eval(s["while"], ctx):
                if n >= limit:
                    raise WorkflowError(f"while excedeu {limit} repetições")
                ctx["loop_index"] = n
                self._steps(s.get("steps") or [], ctx, wf_name)
                n += 1
            return
        if "try" in s:
            try:
                self._steps(s["try"], ctx, wf_name)
            except StopBefore:
                raise
            except Exception as e:
                self.failed_at = None  # erro tratado pelo catch: não interrompe
                ctx["erro"] = f"{type(e).__name__}: {e}"
                self.emit("log", text=f"erro tratado: {e}"[:120])
                self._steps(s.get("catch") or [], ctx, wf_name, seg="catch")
            return
        if "set" in s:
            for k, v in s["set"].items():
                ctx[k] = render(v, ctx, exprs=True)
            return
        if "call" in s:
            from .tools.workflow import load_workflow
            sub = Runner(self.user, self.confirm, self.bot, self._emit, self.depth + 1)
            if not self.user.may_run_workflow(s["call"]):
                raise PermissionError(f"sem permissão no workflow '{s['call']}'")
            res = sub.run(load_workflow(s["call"]), render(s.get("params", {}), ctx))
            self.log += sub.log
            if s.get("save_as"):
                ctx[s["save_as"]] = res["outputs"]
            return
        if "call_bot" in s:
            from . import bots
            target = bots.get_bot(s["call_bot"])
            if target is None:
                raise WorkflowError(f"bot '{s['call_bot']}' não existe")
            bots.ensure_runnable(target, self.user)
            self.emit("handoff", target=target.id, text=f"→ {target.name}")
            sub = Runner(self.user, self.confirm, target.id, self._emit, self.depth + 1)
            sub.emit("job_start", source=self.bot)
            try:
                res = sub.run(target.code, render(s.get("params", {}), ctx))
            except Exception as e:
                bots.record_run(target.id, False)
                sub.emit("job_done", ok=False, text=str(e)[:120])
                raise
            bots.record_run(target.id, True)
            sub.emit("job_done", ok=True)
            self.log += sub.log
            if s.get("save_as"):
                ctx[s["save_as"]] = res["outputs"]
            return
        if "log" in s:
            self.emit("log", text=str(render(s["log"], ctx))[:200])
            return
        if "sleep" in s:
            time.sleep(min(float(s["sleep"]), 300))
            return
        if "fail" in s:
            raise WorkflowError(str(render(s["fail"], ctx)))
        raise WorkflowError(f"Passo desconhecido: {list(s)}")

    def _tool(self, s: dict, ctx: dict, wf_name: str):
        t = registry.get(s["tool"])
        if not self.user.may_use_tool(t.name):
            raise PermissionError(f"usuário sem permissão para {t.name}")
        args = render(s.get("args") or {}, ctx)
        if t.is_risky(args) and not (self.user.can_approve and self.confirm and self.confirm(t.name, args)):
            auth.audit(self.user.name, "tool_denied", tool=t.name, workflow=wf_name, bot=self.bot)
            raise PermissionError(f"{t.name}: ação de risco não autorizada")
        attempts = 1 + max(0, min(int(s.get("retry", 0)), 10))
        delay = min(float(s.get("retry_delay", 1)), 60)
        self.emit("step", tool=t.name, text=t.name)
        ok, result = True, None
        for n in range(attempts):
            try:
                result = t.fn(**args)
                break
            except Exception as e:
                if n + 1 < attempts:
                    self.emit("log", text=f"{t.name} falhou, tentando de novo ({n + 2}/{attempts})")
                    time.sleep(delay)
                    continue
                ok = False
                auth.audit(self.user.name, "tool", tool=t.name, args=args, workflow=wf_name,
                           bot=self.bot, ok=False, error=str(e))
                self.log.append({"tool": t.name, "ok": False, "error": str(e)[:300]})
                if self.on_step:
                    self.on_step(self.path, s, False, f"{type(e).__name__}: {e}")
                if s.get("on_error") != "continue":
                    raise
                result = {"error": f"{type(e).__name__}: {e}"}
        if ok:
            self.log.append({"tool": t.name, "ok": True})
            if self.on_step:
                self.on_step(self.path, s, True, result)
            auth.audit(self.user.name, "tool", tool=t.name, args=args, workflow=wf_name,
                       bot=self.bot, ok=True)
        if s.get("save_as"):
            ctx[s["save_as"]] = result
        return result
