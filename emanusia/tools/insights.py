"""Análise automática "de analista": lê uma base, calcula os números que importam e escreve, em
português, o que foi identificado e as sugestões de ação — pronto para o corpo de um e-mail.
Tudo por regras transparentes (sem IA): variação entre períodos, melhores/piores, concentração
(Pareto), valores fora do padrão, dados faltando/duplicados e comparação com a meta."""
import html

import pandas as pd

from .data import _col, load
from .registry import tool

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _fmt(v, kind: str) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if kind == "money":
        t = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{'-' if v < 0 else ''}R$ {t}"
    if kind == "int":
        return f"{v:,.0f}".replace(",", ".")
    if kind == "percent":
        return f"{v:.1%}".replace(".", ",")
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(a, b):
    return None if not b else (a - b) / abs(b)


def _p(x) -> str:
    return f"{x:+.1%}".replace(".", ",")


def _numeric(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    from ..engine import _to_number
    return s.map(lambda v: float(_to_number(v)) if str(v).strip() not in ("", "nan", "None") else None).astype(float)


@tool("data_insights", "Analisa uma base como um analista: totais, variação entre períodos, melhores e piores "
      "por dimensão, concentração, valores fora do padrão, qualidade dos dados e meta. Devolve "
      "`findings` (o que foi identificado), `suggestions` (ações sugeridas), `text` e `html` prontos para "
      "o corpo do e-mail.",
      {"source": {}, "value": {"type": "string"}, "date_column": {"type": "string"}, "dimensions": {"type": "array"},
       "title": {"type": "string"}, "format": {"type": "string", "enum": ["money", "int", "decimal"]},
       "goal": {"type": "number"}, "top": {"type": "integer"}, "sheet": {"type": "string"}},
      ("source", "value"))
def data_insights(source, value: str, date_column: str = "", dimensions: list | None = None, title: str = "",
                  format: str = "money", goal: float | None = None, top: int = 3, sheet: str | None = None):
    df = load(source, sheet)
    if df.empty:
        return {"findings": ["A base veio vazia: nenhum dado para analisar."],
                "suggestions": ["Conferir se a extração/download trouxe os dados do período."],
                "summary": {}, "text": "A base veio vazia.", "html": "<p>A base veio vazia.</p>"}
    vc = _col(df, value)
    if isinstance(dimensions, str):
        dimensions = [d.strip() for d in dimensions.split(",") if d.strip()]
    dims = [_col(df, d) for d in (dimensions or [])]
    n_rows = len(df)
    dup = int(df.duplicated().sum())
    df["_v"] = _numeric(df[vc])
    missing = int(df["_v"].isna().sum())
    F, S = [], []  # findings, suggestions
    f = lambda v: _fmt(v, format)  # noqa: E731

    total, mean = df["_v"].sum(), df["_v"].mean()
    F.append(f"Total de {vc}: {f(total)} em {_fmt(n_rows, 'int')} registro(s) (média de {f(mean)} por registro).")
    summary = {"total": round(float(total), 2), "registros": n_rows, "media": round(float(mean), 2) if pd.notna(mean) else None}

    # ------------------------------------------------ qualidade dos dados
    if missing:
        F.append(f"{missing} registro(s) sem {vc} válido ({missing / n_rows:.0%} da base).")
        if missing / n_rows > 0.05:
            S.append(f"Melhorar o preenchimento de “{vc}” na origem: {missing / n_rows:.0%} dos registros vieram vazios ou inválidos.")
    if dup:
        F.append(f"{dup} linha(s) duplicada(s) na base.")
        S.append("Eliminar duplicidades na origem (o robô já as tirou da análise tratada).")
    neg = int((df["_v"] < 0).sum())
    if neg:
        F.append(f"{neg} registro(s) com valor negativo (devoluções/estornos?), somando {f(df.loc[df['_v'] < 0, '_v'].sum())}.")
        S.append("Conferir os lançamentos negativos e o motivo das devoluções/estornos.")

    # ------------------------------------------------ fora do padrão (calculado antes: muda a leitura do período)
    v = df["_v"].dropna()
    out, lim = df.iloc[0:0], None
    if len(v) >= 8:
        q1, q3 = v.quantile(0.25), v.quantile(0.75)
        lim = q3 + 3 * (q3 - q1)
        if q3 > q1:
            out = df[df["_v"] > lim].sort_values("_v", ascending=False)

    # ------------------------------------------------ meta
    if goal:
        g = float(goal)
        atingido = total / g if g else 0
        summary["meta_atingida"] = round(atingido, 4)
        if atingido >= 1:
            F.append(f"Meta de {f(g)} atingida: {_fmt(atingido, 'percent')} ({f(total - g)} acima).")
            sem = total - out["_v"].sum()
            if len(out) and sem < g:
                F.append(f"Atenção: sem os valores fora do padrão a meta ficaria em {_fmt(sem / g, 'percent')}.")
                S.append("Confirmar os valores fora do padrão antes de comemorar a meta: sem eles, ela não seria atingida.")
        else:
            F.append(f"Meta de {f(g)} NÃO atingida: {_fmt(atingido, 'percent')} — faltam {f(g - total)}.")
            S.append(f"Plano de ação para fechar a diferença de {f(g - total)} até a meta.")

    # ------------------------------------------------ período
    last_p = prev_p = None
    if date_column:
        dc = _col(df, date_column)
        dt = pd.to_datetime(df[dc], errors="coerce", dayfirst=not pd.api.types.is_datetime64_any_dtype(df[dc]))
        df["_p"] = dt.dt.to_period("M")
        per = df.dropna(subset=["_p"]).groupby("_p")["_v"].sum().sort_index()
        lbl = lambda p: f"{MESES[p.month - 1]}/{p.year}"  # noqa: E731
        if len(per) >= 2:
            last_p, prev_p = per.index[-1], per.index[-2]
            var = _pct(per.iloc[-1], per.iloc[-2])
            summary["variacao_ultimo_periodo"] = None if var is None else round(var, 4)
            if var is not None:
                F.append(f"{lbl(last_p)}: {f(per.iloc[-1])}, {_p(var)} em relação a {lbl(prev_p)} ({f(per.iloc[-2])}).")
                if len(out) and df.loc[out.index, "_p"].isin([last_p, prev_p]).any():
                    clean = df.drop(out.index).dropna(subset=["_p"]).groupby("_p")["_v"].sum()
                    adj = _pct(clean.get(last_p, 0), clean.get(prev_p, 0))
                    if adj is not None:
                        F.append(f"Sem os valores fora do padrão, a variação de {lbl(last_p)} seria {_p(adj)}.")
                        summary["variacao_sem_fora_do_padrao"] = round(adj, 4)
                        var = adj  # a decisão usa o número "limpo"
                if var <= -0.10:
                    S.append(f"Investigar a queda de {_p(var)} em {lbl(last_p)}" + (" — as maiores quedas estão nos pontos identificados acima." if dims else "."))
                elif var >= 0.10:
                    S.append(f"Entender o que puxou a alta de {_p(var)} em {lbl(last_p)} para repetir nos próximos períodos.")
            best, worst = per.idxmax(), per.idxmin()
            F.append(f"Melhor mês: {lbl(best)} ({f(per.max())}); pior mês: {lbl(worst)} ({f(per.min())}).")
            tr = df.drop(out.index).dropna(subset=["_p"]).groupby("_p")["_v"].sum().sort_index() if len(out) else per
            if len(tr) >= 3 and tr.iloc[-1] < tr.iloc[-2] < tr.iloc[-3]:
                F.append("Tendência de queda nos últimos 3 meses seguidos.")
                S.append("Revisar preços, metas e ações comerciais: são 3 meses seguidos de queda.")
            elif len(tr) >= 3 and tr.iloc[-1] > tr.iloc[-2] > tr.iloc[-3]:
                F.append("Tendência de alta nos últimos 3 meses seguidos.")
        elif len(per) == 1:
            F.append(f"Os dados cobrem só {lbl(per.index[0])}.")

    # ------------------------------------------------ dimensões
    for d in dims:
        g = df.groupby(d, dropna=False)["_v"].sum().sort_values(ascending=False)
        g = g[g.index.notna()] if len(g) > 1 else g
        if g.empty or total == 0:
            continue
        tops = ", ".join(f"{k} ({f(v)}; {v / total:.0%})" for k, v in g.head(top).items())
        F.append(f"Por {d} — maiores: {tops}.")
        if len(g) > top:
            low = g.tail(min(top, len(g) - top))
            F.append(f"Por {d} — menores: " + ", ".join(f"{k} ({f(v)})" for k, v in low.items()) + ".")
        n_top = max(1, int(round(len(g) * 0.2)))
        share = g.head(n_top).sum() / total if total else 0
        if len(g) >= 5 and share >= 0.6:
            F.append(f"Concentração: {n_top} de {len(g)} {d}(s) ({n_top / len(g):.0%}) respondem por {share:.0%} do total.")
            S.append(f"Reduzir a dependência de poucos {d}(s): {share:.0%} do resultado está em {n_top} deles.")
        if last_p is not None:
            cur = df[df["_p"] == last_p].groupby(d)["_v"].sum()
            old = df[df["_p"] == prev_p].groupby(d)["_v"].sum()
            delta = (cur.reindex(old.index.union(cur.index)).fillna(0) - old.reindex(old.index.union(cur.index)).fillna(0)).sort_values()
            if len(delta) and delta.iloc[0] < 0:
                k = delta.index[0]
                F.append(f"Maior queda por {d} no último mês: {k} ({f(delta.iloc[0])}).")
                S.append(f"Entender a queda de {k} ({d}): foi a maior do último mês ({f(delta.iloc[0])}).")
            if len(delta) and delta.iloc[-1] > 0:
                F.append(f"Maior crescimento por {d} no último mês: {delta.index[-1]} (+{f(delta.iloc[-1])}).")
        if len(g) > top:
            weakest = g.index[-1]
            if g.iloc[-1] / total < 0.02:
                S.append(f"Avaliar o desempenho de {weakest} ({d}): menos de 2% do total.")

    if len(out):
        ex = ", ".join(f(x) for x in out["_v"].head(3))
        F.append(f"{len(out)} registro(s) muito acima do normal (acima de {f(lim)}), ex.: {ex}.")
        S.append("Conferir os registros fora do padrão: podem ser erro de digitação ou oportunidades a entender.")

    if not S:
        S.append("Nenhum ponto de atenção relevante: manter o acompanhamento e repetir a análise no próximo período.")

    head = title or f"Análise de {vc}"
    text = f"{head}\n\nO que foi identificado:\n" + "\n".join(f"• {x}" for x in F) + \
           "\n\nSugestões de ação e melhorias:\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(S, 1))
    e = html.escape
    body = (f"<h3 style=\"margin:0 0 8px;font-family:Segoe UI,Arial;color:#1f3a5f\">{e(head)}</h3>"
            "<p style=\"margin:12px 0 4px;font-family:Segoe UI,Arial\"><b>O que foi identificado</b></p>"
            "<ul style=\"margin:0;font-family:Segoe UI,Arial\">" + "".join(f"<li>{e(x)}</li>" for x in F) + "</ul>"
            "<p style=\"margin:12px 0 4px;font-family:Segoe UI,Arial\"><b>Sugestões de ação e melhorias</b></p>"
            "<ol style=\"margin:0;font-family:Segoe UI,Arial\">" + "".join(f"<li>{e(x)}</li>" for x in S) + "</ol>")
    return {"findings": F, "suggestions": S, "summary": summary, "text": text, "html": body}
