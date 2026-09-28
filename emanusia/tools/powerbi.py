"""Power BI: exporta dataset (CSV) + script Power Query (M) pronto para 'Obter dados', e push opcional via REST.
Arquivos .pbix não podem ser gerados por código; o fluxo é: robô prepara os dados -> Power BI Desktop
importa a pasta/CSV e o refresh agendado mantém tudo atualizado."""
import csv
import os

import httpx

from .. import config
from .registry import tool


@tool("powerbi_export",
      "Exporta linhas ([[cabeçalho],[linha]...]) como CSV UTF-8 para o Power BI e gera o script M "
      "de importação em powerbi/<nome>.pq.",
      {"name": {"type": "string"}, "rows": {"type": "array"}}, ("name", "rows"))
def powerbi_export(name: str, rows: list):
    csv_rel, pq_rel = f"powerbi/{name}.csv", f"powerbi/{name}.pq"
    csv_path = config.safe_path(csv_rel)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    m = (f'let\n    Fonte = Csv.Document(File.Contents("{csv_path}"), '
         f'[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
         f'    Cabecalho = Table.PromoteHeaders(Fonte, [PromoteAllScalars=true])\nin\n    Cabecalho\n')
    config.safe_path(pq_rel).write_text(m, encoding="utf-8")
    return {"csv": csv_rel, "power_query": pq_rel, "rows": len(rows) - 1}


@tool("powerbi_push_rows",
      "Envia linhas a um dataset 'push' do Power BI Service (token em POWERBI_TOKEN).",
      {"dataset_id": {"type": "string"}, "table": {"type": "string"},
       "rows": {"type": "array", "description": "lista de objetos {coluna: valor}"}},
      ("dataset_id", "table", "rows"), risky=True)
def powerbi_push_rows(dataset_id: str, table: str, rows: list):
    r = httpx.post(f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/tables/{table}/rows",
                   headers={"Authorization": f"Bearer {os.environ['POWERBI_TOKEN']}"},
                   json={"rows": rows}, timeout=60)
    r.raise_for_status()
    return {"pushed": len(rows)}
