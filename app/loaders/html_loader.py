from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re

from .base_loader import BaseLoader, LoadedDocument, decode_text
from app.preprocessors.html_notation import citation_link


class _TextExtractor(HTMLParser):
    """Keep readable text and safe table markup for the shared TableExtractor."""

    ignored_tags = {"script", "style", "template", "noscript"}
    table_tags = {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col"}
    block_tags = {"p", "div", "section", "article", "header", "footer", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.table_depth = 0
        self.links = []
        self.scripts = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.ignored_tags:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag=='a':
            values=dict(attrs);reference=citation_link(values.get('href',''),values.get('role',''));self.links.append(reference)
            if reference:self.parts.append('〔参考文献编号：')
        if tag in {'sup','sub'}:
            reference=any(self.links);self.scripts.append((tag,reference,self.table_depth>0))
            if not reference:self.parts.append('<'+tag+'>' if self.table_depth else ('^(' if tag=='sup' else '_('))
            return
        if tag == 'img':
            safe = ''.join(f' {key}="{escape(value or "", quote=True)}"' for key,value in attrs if key in {'src','alt','title'})
            self.parts.append(f'<img{safe}>')
            return
        if tag == "table":
            if self.table_depth:
                raise ValueError("Nested HTML tables require separate extraction; ingestion is not supported")
            self.table_depth += 1
            self.parts.append("\n\n")
        if self.table_depth and tag in self.table_tags:
            safe_attrs = "".join(f' {key}="{value}"' for key, value in attrs if key in {"rowspan", "colspan"} and value and value.isascii() and value.isdecimal() and 1 <= int(value) <= 1000)
            self.parts.append(f"<{tag}{safe_attrs}>")
        elif tag == "br":
            self.parts.append("<br>" if self.table_depth else "\n")
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "col", "img", "hr", "input", "meta", "link"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.ignored_depth:
            if tag in self.ignored_tags:
                self.ignored_depth -= 1
            return
        if tag=='a' and self.links:
            if self.links.pop():self.parts.append('〕')
        if tag in {'sup','sub'} and self.scripts:
            opened,reference,in_table=self.scripts.pop()
            if not reference:self.parts.append('</'+opened+'>' if in_table else ')')
            return
        if self.table_depth and tag in self.table_tags:
            self.parts.append(f"</{tag}>")
            if tag == "table":
                self.table_depth -= 1
                self.parts.append("\n\n")
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(escape(data, quote=False) if self.table_depth else data)


class HtmlLoader(BaseLoader):
    def load(self, path: str | Path) -> list[LoadedDocument]:
        source = Path(path)
        parser = _TextExtractor()
        parser.feed(decode_text(source))
        parser.close()
        if parser.table_depth:
            raise ValueError("HTML table is incomplete; closing table tag is required")
        content = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()
        if not content:
            raise ValueError("HTML contains no readable text or tables")
        return [LoadedDocument(content=content, source=source.name, metadata={"format": "html", "parser": "html.parser", "coverage": "text_and_tables"})]
