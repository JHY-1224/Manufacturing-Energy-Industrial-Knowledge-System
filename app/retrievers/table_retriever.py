"""Optional bilingual table recall. Original cells, numbers and footnotes stay intact."""
from html.parser import HTMLParser
import re

from app.indexes.bm25_index import BM25Index
from app.post_retrieval.rrf_fusion import RrfFusion
from app.embeddings.embedding_factory import tokenize


# Retrieval vocabulary only: never insert translated facts or numerical answers.
TERMS = {
    '抗拉强度': 'tensile strength', '钢材分组': 'steel group', '点焊': 'resistance spot welding',
    '熔点': 'melting point', '聚酰胺': 'polyamide', '化学结构': 'chemical structure',
    '车间寿命': 'floor life', '湿敏': 'moisture sensitivity', '潮湿敏感': 'moisture sensitivity',
    '湿度': 'relative humidity', '采样频率': 'sampling rate', '传感器': 'sensor',
    '力控': 'force control', '稳态误差': 'steady-state error', '超调': 'overshoot',
    '稳定时间': 'settling time', '障碍': 'obstruction', '扰动': 'disturbance',
    '熔点单位': 'melting point unit', '铂族金属': 'platinum group metal PGM',
    '电解': 'electrolysis', '能耗': 'energy efficiency', '能效': 'energy efficiency',
    '电堆': 'stack', '系统': 'system', '寿命': 'lifetime', '中期': 'interim', '最终目标': 'ultimate',
    '词头': 'prefix', '基本单位': 'base unit', '真空度': 'vacuum',
    '分组': 'group classification', '重复单元': 'chemical structure repeat',
    '下标': 'subscripts', '脚注': 'footnote', '能量输入': 'energy inputs',
}
TABLE_INTENT = re.compile(r'表格|表中|表\s*\d|分组|单位|符号|词头|熔点|强度|多少|各是|分别|参数|指标|采样|车间寿命|重复单元|下标|脚注|能效|\btable\b|\bprefix(?:es)?\b|MSL|\bGroup\b|\bPA\s*6|PGM|kWh', re.I)


class _TableText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


class _TableRows(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.cell=None;self.merged=False
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag in {'td','th'}:
            self.cell=[]
            if any(k in {'rowspan','colspan'} and v!='1' for k,v in attrs):self.merged=True
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.cell is not None:
            if self.row is not None:self.row.append(' '.join(''.join(self.cell).split()))
            self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None


class TableRetriever:
    def __init__(self, store=None):
        self.store=store
    @staticmethod
    def structural_matches(content, query):
        """Match source cell names/identifiers, never inferred cell relationships."""
        parser=_TableRows();parser.feed(content);parser.close()
        if parser.merged or len(parser.rows)<2:return []
        normalize=lambda text: ' '.join(re.sub('[®™]', '', text).split())
        query=normalize(query);matches=[]
        for row in parser.rows[1:]:
            for value in row:
                value=normalize(value)
                # Complete names/grades, not one-letter symbols, numbers or prose.
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9 -]{3,35}',value):continue
                if re.search(r'(?<![A-Za-z0-9])'+re.escape(value)+r'(?![A-Za-z0-9])',query,re.I):matches.append(value)
        # A technology identifier can head multiple status/target columns.
        for header in parser.rows[0]:
            for name in re.findall(r'\b[A-Z][A-Z0-9]*-[A-Z][A-Z0-9-]*\b',header):
                if re.search(r'(?<![A-Za-z0-9])'+re.escape(name)+r'(?![A-Za-z0-9])',query):matches.append(name)
        return list(dict.fromkeys(matches))

    @staticmethod
    def prefer_checked_tables(ranked, query=''):
        """A cell-checked table supersedes machine OCR of the very same figure."""
        checked={}
        for hit in ranked:
            if (hit.metadata.get('table_id') and hit.metadata.get('figure_id') and
                    str(hit.metadata.get('structure_review','')).startswith('agent_visual_cell_transcription') and
                    'table' in hit.retrieval_sources):
                checked.setdefault((hit.doc_id,hit.metadata['figure_id']),hit)
        result=[];seen=set()
        for hit in ranked:
            replacement=checked.get((hit.doc_id,hit.metadata.get('figure_id')))
            if replacement and hit.metadata.get('chunk_kind') in {'image_table_ocr','image_ocr'}:
                replacement=replacement.model_copy(deep=True)
                replacement.metadata.update(selection_reason='source_cell_checked_table_supersedes_same_figure_machine_ocr',
                    replaced_ocr_chunk_id=hit.chunk_id)
                hit=replacement
            if hit.chunk_id not in seen:
                result.append(hit);seen.add(hit.chunk_id)
        # Exact structural row lookup (e.g. Group 4, MSL 3, sensor PS1) is
        # stronger evidence than an unrelated formula with a high model score.
        anchors=[]
        for hit in result:
            if not hit.metadata.get('table_id') or 'table' not in hit.retrieval_sources:
                continue
            checked=str(hit.metadata.get('structure_review','')).startswith(('agent_visual_cell_transcription','source_dictionary'))
            original_dictionary=(hit.metadata.get('reference_type')=='dataset_archive' and str(hit.metadata.get('source_method','')).startswith('original_'))
            if not (checked or original_dictionary):continue
            matches=TableRetriever.structural_matches(hit.content,query)
            if matches:
                hit.metadata={**hit.metadata,'selection_reason':'exact_table_cell_name', 'matched_table_cells':matches}
                anchors.append(hit)
                continue
            # Scope questions need the table's attached restrictions, even when
            # they do not request a particular row or numeric parameter.
            title_terms=set(tokenize(hit.metadata.get('title','')))
            if (re.search(r'批准|适用|能否|限制',query) and '【来源范围与说明】' in hit.content
                    and len(title_terms & set(tokenize(query)))>=2):
                hit.metadata={**hit.metadata,'selection_reason':'table_scope_note_with_matching_title'}
                anchors.append(hit)
                continue
            parser=_TableRows();parser.feed(hit.content);parser.close()
            if parser.merged or len(parser.rows)<2 or not parser.rows[0]:continue
            for row in parser.rows[1:]:
                if not row:continue
                matched=None
                for column,(header,value) in enumerate(zip(parser.rows[0],row)):
                    if not re.fullmatch(r'[A-Za-z][A-Za-z ]{0,30}',header):continue
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 -]{0,30}',value):continue
                    pattern=r'(?<![A-Za-z0-9])'+re.escape(header)+r'\s*'+re.escape(value)+r'(?![A-Za-z0-9])'
                    identifier=column==0 and bool(re.fullmatch(r'(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{2,20}',value))
                    exact_identifier=identifier and re.search(r'(?<![A-Za-z0-9])'+re.escape(value)+r'(?![A-Za-z0-9])',query,re.I)
                    if re.search(pattern,query,re.I) or exact_identifier:matched=header+': '+value;break
                if matched:
                    hit.metadata={**hit.metadata,'selection_reason':'exact_table_header_row_key', 'matched_table_row_key':matched}
                    anchors.append(hit);break
        if anchors:
            selected=max(anchors,key=lambda h:(len(h.metadata.get('matched_table_cells',[])),h.metadata.get('table_recall_score',0),h.rerank_score or 0))
            result=[selected]+[h for h in result if h.chunk_id!=selected.chunk_id]
        return result

    @staticmethod
    def expand(query):
        if not TABLE_INTENT.search(query):
            return query, []
        aliases = list(dict.fromkeys(value for key, value in TERMS.items() if key in query))[:8]
        if re.search(r'Ultramid|聚酰胺|熔点|melting point', query, re.I):
            aliases += ['polyamide '+m.group(1) for m in re.finditer(r'\bPA\s*(66|610|6)\b',query)]
            aliases = list(dict.fromkeys(aliases))
        return (query + ('\n检索同义术语：' + '; '.join(aliases) if aliases else '')), aliases

    def search(self, query, chunks, top_k=6):
        originals = {c.chunk_id: c for c in chunks if c.metadata.get('table_id') or
                     c.metadata.get('chunk_kind') in {'table', 'technical_table'}}
        if self.store:
            from app.knowledge.structure_resolution import StructureResolutions
            resolutions=StructureResolutions(self.store)
            graphics=resolutions.current()
            originals={k:c for k,c in originals.items() if not any(v['disposition']!='recovered_table' and c.doc_id==v['document_id'] and c.metadata.get('table_id')==v.get('source_table_id') and c.metadata.get('revision_id')==v['revision_id'] for v in graphics)}
            originals.update({c.chunk_id:c for c in resolutions.table_chunks(chunks)})
        if not originals or not TABLE_INTENT.search(query):
            return []
        indexed = []
        for chunk in originals.values():
            parser = _TableText(); parser.feed(chunk.content); parser.close()
            text = ' '.join(parser.parts) if '<table' in chunk.content.lower() else chunk.content
            indexed.append(chunk.model_copy(update={'content': chunk.metadata.get('title','')+'\n'+text}))
        index = BM25Index(); index.build(indexed)
        hits = index.search(query, len(indexed))
        # Preserve exact named cells before the bounded recall quota is applied.
        hits.sort(key=lambda h:(len(self.structural_matches(originals[h.chunk_id].content,query)),h.score),reverse=True)
        hits=hits[:top_k]
        for hit in hits:
            hit.metadata = {**hit.metadata, 'table_recall_score': hit.score,
                            'table_match_basis': 'table_text_headers_rows_and_query_terms'}
            hit.content = originals[hit.chunk_id].content
            hit.retrieval_sources = ['table']
            hit.bm25_score = 0.0  # Table-only normalization is a separate channel.
        return hits

    def merge(self, hits, table_hits, top_k):
        if not table_hits:
            return hits
        original = {h.chunk_id: h for h in hits}
        tables = []
        for table in table_hits:
            if table.chunk_id in original:
                hit = original[table.chunk_id].model_copy(deep=True)
                hit.retrieval_sources = list(dict.fromkeys([*hit.retrieval_sources, 'table']))
                if table.metadata.get('derived_structure'):
                    hit.content=table.content
                    hit.metadata={**hit.metadata,**table.metadata}
                hit.metadata = {**hit.metadata, **{k: v for k, v in table.metadata.items() if k.startswith('table_')}}
                table = hit
            tables.append(table)
        combined = RrfFusion().fuse([hits, tables], len(hits) + len(tables))
        for hit in combined:
            hit.metadata = {**hit.metadata, 'table_fusion_score': hit.score}
        # Reserve a bounded recall quota, while letting the reranker decide final rank.
        quota = min(len(tables), max(1, top_k // 3))
        required = {h.chunk_id for h in tables[:quota]}
        selected = [h for h in combined if h.chunk_id not in required][:max(0, top_k-quota)]
        selected += tables[:quota]
        return sorted(selected, key=lambda h: h.score, reverse=True)
