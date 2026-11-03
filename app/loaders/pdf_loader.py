from pathlib import Path

from .base_loader import BaseLoader, LoadedDocument


class PdfLoader(BaseLoader):
    def load(self, path: str | Path) -> list[LoadedDocument]:
        source = Path(path)
        try:
            import fitz
        except ModuleNotFoundError as exc:
            raise ValueError("PDF parser unavailable; install pymupdf to extract the text layer") from exc
        documents = []
        try:
            with fitz.open(source) as pdf:
                if pdf.needs_pass:
                    raise ValueError("Encrypted PDF requires a password; ingestion is not supported")
                for index, page in enumerate(pdf, start=1):
                    content = page.get_text("text").strip()
                    has_images = bool(page.get_images())
                    has_drawings = bool(page.get_drawings())
                    if not content and (has_images or has_drawings):
                        raise ValueError(f"PDF page {index} has graphics but no extractable text; scanned/graphic page OCR is not supported")
                    metadata = {"format": "pdf", "parser": "pymupdf", "page": index, "page_count": len(pdf), "coverage": "text_layer_only", "contains_visual_content": has_images or has_drawings, "visual_content_processed": False}
                    if not content:
                        metadata.update({"blank_page": True, "warning": "Blank page has no text, images or drawings"})
                    documents.append(LoadedDocument(content=content, source=source.name, metadata=metadata))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF text extraction failed; no placeholder content was indexed") from exc
        if not any(document.content for document in documents):
            raise ValueError("PDF contains no readable text pages")
        return documents
