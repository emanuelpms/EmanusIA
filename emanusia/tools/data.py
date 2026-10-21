"""Análise de dados (pandas) para o dia a dia de escritório: entender uma planilha, filtrar, agrupar,
tabela dinâmica, limpar, PROCV (junção), consolidar várias planilhas, extrair tabelas de PDF/HTML e
criar colunas calculadas.

`source` pode ser um arquivo do workspace (.xlsx/.xls/.csv/.json) ou linhas vindas de outro passo
([[cabeçalho...], [linha...]] ou [{coluna: valor}]). `output` (opcional) salva o resultado em
.xlsx/.csv. O retorno traz no máximo `limit` linhas (para a tela não pesar) + o total."""
import glob
import math

import pandas as pd

from .. import config
from .registry import tool

MAX_RETURN = 200
AGGS = {"sum": "sum", "soma": "sum", "mean": "mean", "media": "mean", "média": "mean", "count": "count",
        "contagem": "count", "min": "min", "max": "max", "nunique": "nunique", "distintos": "nunique",
        "median": "median", "mediana": "median", "std": "std", "first": "first", "last": "last"}


# ---------------------------------------------------------------- utilidades
def load(source, sheet: str | None = None) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source.copy()
    if isinstance(source, dict) and isinstance(source.get("rows"), list):  # resultado de outro passo
        source = source["rows"]
    if isinstance(source, list):
        if not source:
            return pd.DataFrame()
        if isinstance(source[0], dict):
            return pd.DataFrame(source)
        return pd.DataFrame(source[1:], columns=[str(c) for c in source[0]])
    p = config.safe_path(str(source))
    ext = p.suffix.lower()
    if ext == ".csv":
        for enc in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(p, sep=None, engine="python", encoding=enc)
            except UnicodeDecodeError:
                continue
    if ext == ".json":
        return pd.read_json(p)
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p, sheet_name=sheet or 0)
    raise ValueError(f"Formato não suportado: {ext} (use xlsx, xls, csv ou json)")


def _clean_val(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        return v.item()
    return v


def rows(df: pd.DataFrame, limit: int | None = MAX_RETURN) -> list[list]:
    body = df.head(limit) if limit else df
    return [[str(c) for c in df.columns]] + [[_clean_val(v) for v in r] for r in body.itertuples(index=False)]


def save(df: pd.DataFrame, output: str | None, sheet: str = "Dados"):
    if not output:
        return None
    p = config.safe_path(output)
    if p.suffix.lower() == ".csv":
        df.to_csv(p, index=False, encoding="utf-8-sig")
    else:
        from .excel import excel_write
        excel_write(output, {sheet: rows(df, None)})
    return output


def result(df: pd.DataFrame, output: str | None, limit: int = MAX_RETURN, **extra) -> dict:
    return {"total_rows": len(df), "rows": rows(df, min(limit, MAX_RETURN)), "saved": save(df, output),
            **extra}


def _col(df, name):
    if name in df.columns:
        return name
    low = {str(c).strip().lower(): c for c in df.columns}
    if str(name).strip().lower() in low:
        return low[str(name).strip().lower()]
    raise KeyError(f"Coluna '{name}' não existe. Colunas: {', '.join(map(str, df.columns))}")


def _agg(name: str) -> str:
    if str(name).lower() not in AGGS:
        raise ValueError(f"Agregação '{name}' inválida: use {', '.join(sorted(set(AGGS)))}")
    return AGGS[str(name).lower()]


def apply_where(df: pd.DataFrame, where: list | None) -> pd.DataFrame:
    """where = [{column, op, value}], op: = != > >= < <= contains startswith endswith in not_in
    between isnull notnull."""
    for f in where or []:
        c = df[_col(df, f["column"])]
        op, v = f.get("op", "="), f.get("value")
        num = pd.to_numeric(c, errors="coerce") if isinstance(v, int | float) else c
        if op in ("=", "=="):
            m = num == v
        elif op == "!=":
            m = num != v
        elif op in (">", ">=", "<", "<="):
            vv = v
            if isinstance(v, str):  # datas em texto
                num, vv = pd.to_datetime(c, errors="coerce", dayfirst=True), pd.to_datetime(v, dayfirst=True)
            m = {">": num > vv, ">=": num >= vv, "<": num < vv, "<=": num <= vv}[op]
        elif op == "contains":
            m = c.astype(str).str.contains(str(v), case=False, na=False, regex=False)
        elif op == "startswith":
            m = c.astype(str).str.lower().str.startswith(str(v).lower())
        elif op == "endswith":
            m = c.astype(str).str.lower().str.endswith(str(v).lower())
        elif op == "in":
            m = c.isin(v if isinstance(v, list) else [v])
        elif op == "not_in":
            m = ~c.isin(v if isinstance(v, list) else [v])
        elif op == "between":
            m = (num >= v[0]) & (num <= v[1])
        elif op == "isnull":
            m = c.isna() | (c.astype(str).str.strip() == "")
        elif op == "notnull":
            m = c.notna() & (c.astype(str).str.strip() != "")
        else:
            raise ValueError(f"operador '{op}' inválido")
        df = df[m.fillna(False)]
    return df


def aggregate(df: pd.DataFrame, group_by, agg: dict) -> pd.DataFrame:
    group_by = [group_by] if isinstance(group_by, str) else list(group_by or [])
    spec = {}
    for col, fns in (agg or {}).items():
        c = _col(df, col)
        for fn in (fns if isinstance(fns, list) else [fns]):
            f = _agg(fn)
            if f in ("sum", "mean", "median", "std", "min", "max"):
                df[c] = pd.to_numeric(df[c], errors="coerce") if f not in ("min", "max") else df[c]
            multi = isinstance(fns, list) and len(fns) > 1
            spec[f"{c}_{fn}" if multi else c] = (c, f)
    if not group_by:
        return pd.DataFrame([{k: df[c].agg(f) for k, (c, f) in spec.items()}])
    keys = [_col(df, g) for g in group_by]
    if not spec:
        return df.groupby(keys, dropna=False).size().reset_index(name="quantidade")
    return df.groupby(keys, dropna=False).agg(**spec).reset_index()


# ---------------------------------------------------------------- ferramentas
@tool("data_profile", "Entende uma planilha/tabela: colunas, tipos, vazios, estatísticas e amostra. "
      "Use ANTES de analisar dados desconhecidos.",
      {"source": {"description": "arquivo do workspace ou linhas"}, "sheet": {"type": "string"}},
      ("source",))
def data_profile(source, sheet: str | None = None):
    df = load(source, sheet)
    cols = []
    for c in df.columns:
        s = df[c]
        num = pd.to_numeric(s, errors="coerce")
        info = {"column": str(c), "type": str(s.dtype), "empty": int(s.isna().sum()),
                "distinct": int(s.nunique())}
        if num.notna().mean() > 0.8 and len(s):
            info.update(min=_clean_val(num.min()), max=_clean_val(num.max()),
                        mean=round(float(num.mean()), 2), sum=round(float(num.sum()), 2))
        else:
            info["top"] = [str(x) for x in s.value_counts().head(5).index]
        cols.append(info)
    sheets = None
    if isinstance(source, str) and source.lower().endswith((".xlsx", ".xlsm", ".xls")):
        sheets = pd.ExcelFile(config.safe_path(source)).sheet_names
    return {"rows": len(df), "columns": cols, "sample": rows(df, 5), "sheets": sheets,
            "duplicates": int(df.duplicated().sum())}


@tool("data_query", "Filtra, agrupa e agrega dados (como filtro + SUBTOTAL/SOMASE). where=[{column, "
      "op, value}] (op: = != > >= < <= contains in between isnull notnull); group_by=[colunas]; "
      "agg={coluna: sum|mean|count|min|max|nunique|median}; sort_by; columns (selecionar); output.",
      {"source": {}, "sheet": {"type": "string"}, "where": {"type": "array"}, "columns": {"type": "array"},
       "group_by": {"type": "array"}, "agg": {"type": "object"}, "sort_by": {"type": "string"},
       "descending": {"type": "boolean"}, "limit": {"type": "integer"}, "output": {"type": "string"}},
      ("source",))
def data_query(source, sheet=None, where=None, columns=None, group_by=None, agg=None, sort_by=None,
               descending=True, limit: int = MAX_RETURN, output=None):
    df = apply_where(load(source, sheet), where)
    if group_by or agg:
        df = aggregate(df, group_by, agg or {})
    if columns:
        df = df[[_col(df, c) for c in columns]]
    if sort_by:
        df = df.sort_values(_col(df, sort_by), ascending=not descending)
    return result(df, output, limit)


@tool("data_pivot", "Tabela dinâmica: index (linhas), columns (colunas), values, aggfunc, com totais.",
      {"source": {}, "sheet": {"type": "string"}, "index": {"type": "array"}, "columns": {"type": "string"},
       "values": {"type": "string"}, "aggfunc": {"type": "string"}, "where": {"type": "array"},
       "output": {"type": "string"}}, ("source", "index", "values"))
def data_pivot(source, index, values, columns=None, aggfunc="sum", sheet=None, where=None, output=None):
    df = apply_where(load(source, sheet), where)
    v = _col(df, values)
    f = _agg(aggfunc)
    if f not in ("count", "nunique", "first", "last"):
        df[v] = pd.to_numeric(df[v], errors="coerce")
    idx = [_col(df, i) for i in ([index] if isinstance(index, str) else index)]
    pv = pd.pivot_table(df, index=idx, columns=_col(df, columns) if columns else None, values=v,
                        aggfunc=f, margins=True, margins_name="Total", fill_value=0)
    pv.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in pv.columns]
    return result(pv.reset_index(), output)


@tool("data_clean", "Limpa dados: remove duplicados/linhas vazias, apara espaços, renomeia colunas, "
      "converte tipos (dtypes={col: number|date|text}), preenche vazios (fill={col: valor}), mantém "
      "só `keep_columns`. Salva em output.",
      {"source": {}, "sheet": {"type": "string"}, "output": {"type": "string"},
       "drop_duplicates": {"type": "boolean"}, "drop_empty_rows": {"type": "boolean"},
       "trim": {"type": "boolean"}, "rename": {"type": "object"}, "dtypes": {"type": "object"},
       "fill": {"type": "object"}, "keep_columns": {"type": "array"}}, ("source",))
def data_clean(source, sheet=None, output=None, drop_duplicates=True, drop_empty_rows=True, trim=True,
               rename=None, dtypes=None, fill=None, keep_columns=None):
    df = load(source, sheet)
    before = len(df)
    df.columns = [str(c).strip() for c in df.columns]
    if trim:
        for c in df.columns:
            if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
                df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)
    if drop_empty_rows:
        df = df.replace("", pd.NA).dropna(how="all")
    if rename:
        df = df.rename(columns={_col(df, k): v for k, v in rename.items()})
    for c, t in (dtypes or {}).items():
        c = _col(df, c)
        if t == "number":
            s = df[c].astype(str).str.replace(r"[R$\s]", "", regex=True)
            br = s.str.contains(r",\d{1,2}$", regex=True)  # 1.234,56 -> 1234.56
            s = s.where(~br, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
            df[c] = pd.to_numeric(s, errors="coerce")
        elif t == "date":
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
        elif t == "text":
            df[c] = df[c].astype(str)
    for c, v in (fill or {}).items():
        df[_col(df, c)] = df[_col(df, c)].fillna(v)
    if drop_duplicates:
        df = df.drop_duplicates()
    if keep_columns:
        df = df[[_col(df, c) for c in keep_columns]]
    return result(df, output, 20, removed_rows=before - len(df))


@tool("data_merge", "Cruza duas tabelas (PROCV/JOIN): on=coluna em comum (ou left_on/right_on); "
      "how=left|inner|outer|right. indicator=true marca quem só existe de um lado (conciliação).",
      {"left": {}, "right": {}, "on": {"type": "string"}, "left_on": {"type": "string"},
       "right_on": {"type": "string"}, "how": {"type": "string"}, "left_sheet": {"type": "string"},
       "right_sheet": {"type": "string"}, "indicator": {"type": "boolean"}, "output": {"type": "string"}},
      ("left", "right"))
def data_merge(left, right, on=None, left_on=None, right_on=None, how="left", left_sheet=None,
               right_sheet=None, indicator=False, output=None):
    a, b = load(left, left_sheet), load(right, right_sheet)
    lo, ro = (_col(a, on), _col(b, on)) if on else (_col(a, left_on), _col(b, right_on))
    a[lo], b[ro] = a[lo].astype(str).str.strip(), b[ro].astype(str).str.strip()
    df = a.merge(b, left_on=lo, right_on=ro, how=how, indicator="_origem" if indicator else False,
                 suffixes=("", "_2"))
    extra = {}
    if indicator:
        df["_origem"] = df["_origem"].map({"both": "ambos", "left_only": "só esquerda",
                                           "right_only": "só direita"})
        extra["resumo"] = {k: int(v) for k, v in df["_origem"].value_counts().items()}
    return result(df, output, 50, **extra)


@tool("data_combine", "Consolida várias planilhas/CSVs (padrão de arquivos, ex.: 'filiais/*.xlsx') "
      "em uma só tabela, com coluna da origem.",
      {"pattern": {"type": "string"}, "sheet": {"type": "string"}, "output": {"type": "string"},
       "source_column": {"type": "string"}}, ("pattern",))
def data_combine(pattern: str, sheet=None, output=None, source_column="arquivo_origem"):
    files = sorted(p for p in glob.glob(str(config.safe_path(pattern)), recursive=True)
                   if p.lower().endswith((".xlsx", ".xls", ".xlsm", ".csv")))
    if not files:
        raise FileNotFoundError(f"Nenhuma planilha em '{pattern}'")
    frames = []
    for f in files:
        rel = config.display_path(config.safe_path(f))
        d = load(rel, sheet)
        if source_column:
            d[source_column] = rel
        frames.append(d)
    return result(pd.concat(frames, ignore_index=True), output, 20, files=len(files))


@tool("data_extract", "Extrai tabelas de PDF (relatórios, notas, extratos) ou HTML salvo. Retorna as "
      "tabelas encontradas; `index` escolhe uma e `output` salva.",
      {"path": {"type": "string"}, "index": {"type": "integer"}, "pages": {"type": "string"},
       "output": {"type": "string"}}, ("path",))
def data_extract(path: str, index: int | None = None, pages: str = "", output=None):
    p = config.safe_path(path)
    tables: list[pd.DataFrame] = []
    if p.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(p) as pdf:
            sel = pdf.pages
            if pages:
                a, _, b = pages.partition("-")
                sel = pdf.pages[int(a) - 1:int(b or a)]
            for pg in sel:
                for t in pg.extract_tables():
                    if t and len(t) > 1:
                        tables.append(pd.DataFrame(t[1:], columns=[str(c or f"col{i}") for i, c in enumerate(t[0])]))
    elif p.suffix.lower() in (".html", ".htm"):
        from io import StringIO
        tables = pd.read_html(StringIO(p.read_text(encoding="utf-8", errors="replace")))
    else:
        raise ValueError("data_extract aceita .pdf ou .html")
    if not tables:
        return {"tables": 0}
    if index is not None:
        return result(tables[index], output, 50, tables=len(tables))
    return {"tables": len(tables), "preview": [rows(t, 3) for t in tables[:10]]}


@tool("data_compute", "Cria colunas calculadas linha a linha: new_columns={nome: 'expressão'} usando os "
      "nomes das colunas (ex.: 'preco * quantidade', \"'ATRASADO' if dias > 30 else 'OK'\").",
      {"source": {}, "sheet": {"type": "string"}, "new_columns": {"type": "object"},
       "output": {"type": "string"}}, ("source", "new_columns"))
def data_compute(source, new_columns: dict, sheet=None, output=None):
    from ..engine import check_expr, safe_eval
    df = load(source, sheet)
    if len(df) > 200_000:
        raise ValueError("data_compute limitado a 200 mil linhas")
    for name, expr in new_columns.items():
        check_expr(expr)
        recs = df.to_dict("records")
        vals = []
        for r in recs:
            ctx = {str(k).strip().replace(" ", "_"): _clean_val(v) for k, v in r.items()}
            try:
                vals.append(safe_eval(expr, ctx))
            except (TypeError, ZeroDivisionError):
                vals.append(None)
        df[name] = vals
    return result(df, output, 20)
