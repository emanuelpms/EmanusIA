"""Empresa de demonstração: bots que rodam offline (Excel + dashboard + subordinados), para ver o
escritório funcionando antes de criar os seus. `emanusia demo`"""
from . import auth, bots

MESES = [["Mês", "Receita", "Custo"], ["Jan", 120, 70], ["Fev", 135, 80], ["Mar", 160, 85]]

DEMO = [
    # (nome, time, cargo, descrição, código)
    ("Coletor de Vendas", "Financeiro", "worker", "Junta os números de vendas do mês.",
     {"params": {}, "steps": [
         {"log": "coletando vendas"},
         {"set": {"linhas": MESES}},
         {"tool": "excel_write", "args": {"path": "demo/vendas.xlsx", "sheets": {"Vendas": "{{linhas}}"},
                                          "chart": {"sheet": "Vendas", "type": "bar", "value_cols": [2, 3]}}},
         {"set": {"meses": "=len(linhas) - 1"}},
         {"log": "{{meses}} meses coletados"}]}),
    ("Analista de Custos", "Financeiro", "worker", "Monta o dashboard de receita x custo.",
     {"params": {}, "steps": [
         {"tool": "dashboard_build", "args": {
             "path": "demo/dashboard.html", "title": "Financeiro — 1º trimestre",
             "kpis": [{"label": "Receita", "value": "R$ 415 mil", "delta": "+18%"},
                      {"label": "Custo", "value": "R$ 235 mil", "delta": "+6%"}],
             "charts": [{"type": "bar", "title": "Receita x Custo", "x": ["Jan", "Fev", "Mar"],
                         "series": {"Receita": [120, 135, 160], "Custo": [70, 80, 85]}}]}}]}),
    ("Dados Power BI", "Financeiro", "worker", "Atualiza o dataset do Power BI.",
     {"params": {}, "steps": [{"tool": "powerbi_export", "args": {"name": "financeiro", "rows": MESES}}]}),
    ("Chefe Financeiro", "Financeiro", "boss", "Coordena o fechamento: delega para a equipe.",
     {"params": {}, "steps": [
         {"log": "iniciando fechamento"},
         {"call_bot": "coletor-de-vendas"},
         {"call_bot": "analista-de-custos"},
         {"call_bot": "dados-power-bi"},
         {"log": "fechamento concluído"}]}),
    ("Relatório Comercial", "Vendas", "worker", "Planilha semanal do time comercial.",
     {"params": {"semana": "38"}, "steps": [
         {"tool": "excel_write", "args": {"path": "demo/comercial_{{semana}}.xlsx",
                                          "sheets": {"Semana": [["Vendedor", "Pedidos"], ["Ana", 12], ["Rui", 9]]}}}]}),
    ("Painel de Metas", "Vendas", "worker", "Dashboard de metas do time.",
     {"params": {}, "steps": [{"tool": "dashboard_build", "args": {
         "path": "demo/metas.html", "title": "Metas de vendas",
         "kpis": [{"label": "Meta", "value": "87%", "delta": "+4%"}],
         "charts": [{"type": "pie", "title": "Pedidos", "x": ["Ana", "Rui"], "series": {"Pedidos": [12, 9]}}]}}]}),
    ("Chefe Comercial", "Vendas", "boss", "Revisa os relatórios do time.",
     {"params": {}, "steps": [{"call_bot": "relatorio-comercial"}, {"call_bot": "painel-de-metas"}]}),
    ("Conciliador SAP", "Operações", "worker", "Rascunho: vai conciliar notas no SAP (em treinamento).",
     {"params": {}, "steps": [{"tool": "sap_run_transaction", "args": {"tcode": "FB03"}}]}),
]


def seed(user: auth.User | None = None) -> list[str]:
    user = user or auth.LOCAL_ADMIN
    created = []
    for name, team, role, desc, code in DEMO:
        bid = bots.slug(name)
        if bots.get_bot(bid):
            continue
        bots.create_bot(name, user, code, team, role, desc)
        created.append(bid)
    # validar subordinados antes dos chefes (call_bot exige que existam)
    for name, *_ in DEMO:
        bid = bots.slug(name)
        if bid in created and not name.startswith("Conciliador"):  # o conciliador fica em rascunho
            if bots.validate_bot(bid, user)["ok"]:
                bots.set_active(bid, user, True)
    return created
