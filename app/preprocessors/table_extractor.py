from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4


@dataclass
class ExtractedTable:
    table_id: str
    content: str
    section: str
    kind: str = "table"
    source_table_id: str | None = None


class TableExtractor:
    markdown_pattern = re.compile(r"(?:^|\n)(\|[^\n]+\|\n\|(?:\s*:?-+:?\s*\|)+\n(?:\|[^\n]+\|\n?)+)", re.MULTILINE)
    html_pattern = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
    figure_pattern = re.compile(r"<figure\b[^>]*>.*?</figure>|<img\b[^>]*>", re.IGNORECASE | re.DOTALL)

    def extract(self, text: str, section: str = "未分节") -> tuple[str, list[ExtractedTable]]:
        tables: list[ExtractedTable] = []

        def replace(match: re.Match[str]) -> str:
            table_id = f"tbl_{uuid4().hex[:10]}"
            content = match.group(0).strip()
            kind = "figure" if re.match(r'<(?:figure|img)\b',content,re.I) else "table"
            source = re.search(r'\bdata-source-table="([\w:.-]+)"', content)
            tables.append(ExtractedTable(table_id=table_id, content=content, section=section, kind=kind,
                                         source_table_id=source.group(1) if source else None))
            return f"\n[{table_id}]\n"

        text = self.html_pattern.sub(replace, text)
        text = self.markdown_pattern.sub(replace, text)
        text = self.figure_pattern.sub(replace, text)
        return text, tables
