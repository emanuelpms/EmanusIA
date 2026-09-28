"""Relatórios Excel formatados (openpyxl)."""
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import config
from .registry import tool

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")


def _fill_sheet(ws, rows: list[list]):
    for r in rows:
        ws.append(list(r))
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(ws.columns, 1):
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 50)


@tool("excel_write",
      "Cria um .xlsx formatado. sheets = {\"Aba\": [[cabeçalho...], [linha...], ...]}. "
      "chart opcional: {sheet, type: bar|line, category_col, value_cols:[..]} (colunas 1-based).",
      {"path": {"type": "string"}, "sheets": {"type": "object"}, "chart": {"type": "object"}},
      ("path", "sheets"))
def excel_write(path: str, sheets: dict, chart: dict | None = None):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        _fill_sheet(wb.create_sheet(name[:31]), rows)
    if chart:
        ws = wb[chart["sheet"][:31]]
        ch = BarChart() if chart.get("type", "bar") == "bar" else LineChart()
        ch.title = chart.get("title", ws.title)
        for col in chart["value_cols"]:
            ch.add_data(Reference(ws, min_col=col, min_row=1, max_row=ws.max_row),
                        titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=chart.get("category_col", 1),
                                    min_row=2, max_row=ws.max_row))
        ch.width, ch.height = 22, 11
        ws.add_chart(ch, f"{get_column_letter(ws.max_column + 2)}2")
    wb.save(config.safe_path(path))
    return {"path": path, "sheets": list(sheets)}


@tool("excel_read", "Lê uma planilha .xlsx/.csv do workspace e retorna as linhas.",
      {"path": {"type": "string"}, "sheet": {"type": "string"}, "max_rows": {"type": "integer"}},
      ("path",))
def excel_read(path: str, sheet: str | None = None, max_rows: int = 500):
    p = config.safe_path(path)
    if p.suffix.lower() == ".csv":
        import csv
        with open(p, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))[:max_rows]
    ws = load_workbook(p, data_only=True)[sheet] if sheet else load_workbook(p, data_only=True).active
    return [list(r) for r in ws.iter_rows(values_only=True, max_row=max_rows)]
