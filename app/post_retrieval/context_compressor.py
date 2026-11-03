import re

from app.embeddings.embedding_factory import tokenize


class ContextCompressor:
    def compress(self, query: str, content: str, max_sentences: int = 4, mode: str = "auto", preserve_structure: bool = False) -> str:
        # Explicitly protected evidence includes table headers, units, conditions
        # and footnotes. Sentence/character clipping can change their meaning.
        if preserve_structure:
            return content
        is_table=bool(re.search(r'(?m)^\s*\|?\s*:?-{3,}:?\s*\|\s*:?-{3,}',content))
        if mode == "table" or (mode == "auto" and is_table):
            return self._compress_table(query, content)
        if mode == "keyword":
            return self._compress_keyword(query, content, max_sentences)
        return self._compress_sentences(query, content, max_sentences)

    def _compress_sentences(self, query: str, content: str, max_sentences: int) -> str:
        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])|(?<=[.])\s+(?=[A-ZQ])|\n\s*\n", content) if item.strip()]
        if not sentences:
            return content[:800]
        query_terms = set(tokenize(query))
        scored = [(i,sentence,len(query_terms & set(tokenize(sentence)))) for i,sentence in enumerate(sentences)]
        positive=[item for item in scored if item[2]>0]
        selected = sorted(sorted(positive or scored,key=lambda item:item[2],reverse=True)[:max_sentences])
        # The caller bypasses this bounded view for protected/short translated source pages.
        return "\n\n".join(sentence for _,sentence,_ in selected)[:1200]

    def _compress_keyword(self, query: str, content: str, max_sentences: int) -> str:
        terms = set(tokenize(query))
        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;\n])", content) if item.strip()]
        selected = [item for item in sentences if terms & set(tokenize(item))]
        return "".join(selected[:max_sentences])[:1200] or content[:800]

    def _compress_table(self, query: str, content: str) -> str:
        terms = set(tokenize(query))
        rows = [row for row in content.splitlines() if row.strip()]
        if len(rows) <= 2:
            return content[:1200]
        header = rows[:2]
        matched = [row for row in rows[2:] if terms & set(tokenize(row))]
        return "\n".join(header + (matched[:12] or rows[2:6]))[:1600]
