from pathlib import Path

from .base_loader import BaseLoader
from .chatml_loader import ChatMLLoader
from .csv_excel_loader import CsvExcelLoader
from .docx_loader import DocxLoader
from .html_loader import HtmlLoader
from .markdown_loader import MarkdownLoader, TextLoader
from .pdf_loader import PdfLoader


def get_loader(path: str | Path) -> BaseLoader:
    suffix = Path(path).suffix.lower()
    if suffix in {'.png','.jpg','.jpeg','.webp','.tif','.tiff','.bmp','.gif','.avif'}:
        raise ValueError('Image requires auto/OCR processing')
    if suffix in {".md", ".markdown"}:
        return MarkdownLoader()
    if suffix == ".pdf":
        return PdfLoader()
    if suffix in {".docx", ".doc"}:
        return DocxLoader()
    if suffix in {".html", ".htm"}:
        return HtmlLoader()
    if suffix in {".csv", ".xlsx", ".xls"}:
        return CsvExcelLoader()
    if suffix in {".jsonl", ".chatml"}:
        return ChatMLLoader()
    return TextLoader()
