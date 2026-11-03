from pathlib import Path

from .base_loader import BaseLoader, LoadedDocument


class DocxLoader(BaseLoader):
    def __init__(self, allow_empty=False):
        self.allow_empty=allow_empty

    def load(self, path: str | Path) -> list[LoadedDocument]:
        source = Path(path)
        if source.suffix.lower() != ".docx":
            raise ValueError("Legacy .doc is not supported; use a separately converted .docx file")
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ModuleNotFoundError as exc:
            raise ValueError("DOCX parser unavailable; install python-docx to extract paragraphs and tables") from exc
        try:
            document = Document(source)
            blocks = []
            table_count = 0
            structured_tables = []
            formula_evidence = []
            for element in document.element.body.iterchildren():
                if element.tag == qn("w:p"):
                    from app.preprocessors.structured_tables import office_paragraph
                    text, rich, formulas, flags = office_paragraph(element.xml)
                    if formulas or flags or '<sup>' in rich or '<sub>' in rich:
                        formula_evidence.append({'body_position': len(blocks)+1, 'text': text,
                                                 'original_ooxml': element.xml, 'formula_sources': formulas,
                                                 'flags': flags, 'review_status': 'source_checked_pending_human'})
                    if text.strip():
                        blocks.append(text)
                elif element.tag == qn("w:tbl"):
                    from app.preprocessors.structured_tables import office_table, to_html
                    table = office_table(element.xml, f'docx_table{table_count+1:02d}')
                    structured_tables.append(table)
                    blocks.append(to_html(table))
                    table_count += 1
            content = "\n\n".join(blocks)
        except Exception as exc:
            raise ValueError("DOCX extraction failed; no placeholder content was indexed") from exc
        if not content.strip():
            if self.allow_empty:
                return []
            raise ValueError("DOCX contains no body text or table data; embedded image OCR is not supported")
        return [LoadedDocument(content=content, source=source.name, metadata={"format": "docx", "parser": "python-docx", "table_count": table_count, "coverage": "body_paragraphs_and_tables", "structured_tables": structured_tables, "formula_evidence": formula_evidence})]
