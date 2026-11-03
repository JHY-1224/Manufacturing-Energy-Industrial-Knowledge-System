from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from .base_loader import BaseLoader, LoadedDocument, decode_text


def _rows_to_markdown(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if not width:
        return ""
    def cell_text(cell: object) -> str:
        value = "" if cell is None else str(cell)
        return value.replace("&", "&amp;").replace("|", "&#124;").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    normalized = [[cell_text(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


class CsvExcelLoader(BaseLoader):
    def load(self, path: str | Path) -> list[LoadedDocument]:
        source = Path(path)
        if source.suffix.lower() == ".csv":
            try:
                rows = list(csv.reader(StringIO(decode_text(source), newline=""), strict=True))
            except csv.Error as exc:
                raise ValueError("CSV contains invalid quoted fields") from exc
            if not rows or not any(any(str(cell).strip() for cell in row) for row in rows):
                raise ValueError("CSV contains no table data")
            return [LoadedDocument(content=_rows_to_markdown(rows), source=source.name, metadata={"format": "csv", "parser": "csv", "category": "Table", "row_count": len(rows)})]
        if source.suffix.lower() != ".xlsx":
            raise ValueError("Only .csv and .xlsx are supported; legacy .xls requires conversion to a separate .xlsx file")
        try:
            from openpyxl import load_workbook
        except ModuleNotFoundError as exc:
            raise ValueError("Excel parser unavailable; install openpyxl to read .xlsx worksheets") from exc
        try:
            workbook = load_workbook(source, read_only=True, data_only=False, keep_links=False)
            try:
                documents = []
                for index, sheet in enumerate(workbook.worksheets, start=1):
                    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
                    if not any(any(cell is not None and str(cell).strip() for cell in row) for row in rows):
                        continue
                    documents.append(LoadedDocument(content=_rows_to_markdown(rows), source=source.name, metadata={"format": "xlsx", "parser": "openpyxl", "category": "Table", "sheet": sheet.title, "sheet_index": index, "section": sheet.title, "row_count": len(rows), "formula_values": "source_formulas_not_recalculated"}))
            finally:
                workbook.close()
        except Exception as exc:
            raise ValueError("Excel worksheet extraction failed; no placeholder content was indexed") from exc
        if not documents:
            raise ValueError("Excel workbook contains no table data")
        return documents
