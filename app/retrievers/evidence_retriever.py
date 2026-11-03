"""Optional source-text recall for exact conditions, headers and caption lookup.

Works only on the caller's already filtered scope. Windows are search views, never
new evidence: all hits return the unchanged original chunk and source identifiers.
"""
from html import unescape
import re

from app.core.models import RagChunk, RetrievalHit
from app.indexes.bm25_index import BM25Index
from app.post_retrieval.rrf_fusion import RrfFusion
from app.retrievers.table_retriever import _TableText, _TableRows

# Query vocabulary only. No corpus IDs, factual values or expected answers.
ALIASES={
    '轴承':'bearing', '安装':'mounting installation', '清洗':'cleaning washing', '外径':'outside diameter',
    '保养':'care maintenance', '原料':'raw materials', '称重':'weighing', '称量':'weighing',
    '计量':'measuring', '核对':'check', '第二人':'second person', '检查清单':'inspection checklist',
    '蒸汽':'steam', '试点':'pilot sites', '能源绩效':'energy performance', '改善':'improvement',
    '周期':'period', '几年':'years', '培训':'training', '健康档案':'health records',
    '验证':'verification', '生产':'production', '温度':'temperature', '压力':'pressure',
}
IMAGE_LOOKUP=re.compile(r'(?:查找|定位|搜索|检索|find|locate).*(?:图|image|caption)|(?:图|image|caption).*(?:查找|定位|搜索|检索|find|locate)',re.I|re.S)
HEADER_LOOKUP=re.compile(r'表格|检查表|记录表|表中|表头|栏目|\btable\b',re.I)
NUMBER_UNIT=re.compile(r'(?<![A-Za-z0-9_.+-])([+-]?\d+(?:\.\d+)?)\s*(mm|cm|km|MPa|kPa|Pa|kWh|kW|MW|Hz|MHz|°C|°F|%)(?![A-Za-z0-9])')
TABLE_REFERENCE=re.compile(r'(?:(?<![A-Za-z0-9_])table\s*|表\s*)(\d+(?:[.-]\d+)*)(?![\d.])',re.I)
VERSION_INTENT=re.compile(r'版次|扉页|第几版|哪一版|哪一年.*(?:出版|发布)|\b(?:which|what)\s+edition\b',re.I)
VERSION_LINE=re.compile(r'(?im)^\s*[^\n]{0,28}\b(?:edition|revision|version)\b[^\n]{0,24}\b(?:19|20)\d{2}\s*$')
ACRONYM_INTENT=re.compile(r'缩写|全称|展开|\bacronym|\babbreviation|stand(?:s)? for',re.I)


def lexical_tokens(text):
    tokens=re.findall(r'[A-Za-z]+(?:[_-][A-Za-z0-9]+)*|\d+(?:\.\d+)?',text.lower())
    tokens += [t[:-1] for t in tokens if len(t)>4 and t.endswith('s') and not t.endswith('ss')]
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        # Whole Chinese phrases matter more than a bag of common single characters.
        tokens.extend(run[i:i+n] for n in (2,3) for i in range(len(run)-n+1))
    return tokens


def source_text(content):
    content=re.sub(r'^【资料目录标题】[^\n]*(?:\n|$)','',content)
    if '<table' in content.lower():
        parser=_TableText();parser.feed(content);parser.close();content=' '.join(parser.parts)
    return unescape(content)


def caption_text(chunk):
    m=chunk.metadata
    if m.get('chunk_kind')!='source_caption' or not m.get('image_sha256'):return ''
    caption=m.get('source_caption')
    if caption and re.sub(r'\s+','',caption) not in re.sub(r'\s+','',chunk.content):caption=None
    # Older private image imports retain the caption as a dedicated source block.
    if not caption and chunk.content.startswith('【来源网页介绍'):
        lines=chunk.content.splitlines()[1:]
        caption='\n'.join(line for line in lines if not line.startswith(('行业：','来源搜索分类：')))
    return (caption or '').strip()


def exact_caption(query,chunk):
    caption=caption_text(chunk)
    compact=lambda value:re.sub(r'\s+','',value)
    return bool(IMAGE_LOOKUP.search(query) and len(compact(caption))>=8 and compact(caption) in compact(query))


def header_matches(query,chunk):
    if not HEADER_LOOKUP.search(query) or not chunk.metadata.get('table_id'):return []
    parser=_TableRows();parser.feed(chunk.content);parser.close()
    if not parser.rows:return []
    matched=[]
    for header in parser.rows[0]:
        # Match explicit labels, not inferred row values in merged cells.
        text=re.sub(r'^(?:是否)(?:填写|具有|有)?','',header)
        if re.fullmatch(r'[\u4e00-\u9fff]{3,16}',text) and text in query:matched.append(header)
    return list(dict.fromkeys(matched))


class EvidenceRetriever:
    def __init__(self):
        self._signature=None
        self._index=None
        self.last_cache_stats={}

    def prepare(self, chunks):
        # Scope is filtered by the caller. Content identity also protects direct users
        # of this retriever from reusing a stale revision under the same chunk ID.
        signature=tuple((c.chunk_id,c.doc_id,c.content) for c in chunks)
        if signature==self._signature:
            self.last_cache_stats={'reused':True,'windows':len(self._index.chunks),'chunks':len(chunks)}
            return
        self._bodies={c.chunk_id:source_text(c.content) for c in chunks}
        windows=[];self._origins={}
        for chunk in chunks:
            body=self._bodies[chunk.chunk_id]
            for offset in range(0,len(body),680):
                key=chunk.chunk_id+':window:'+str(offset)
                windows.append(RagChunk(doc_id=chunk.doc_id,chunk_id=key,content=body[offset:offset+800]))
                self._origins[key]=(chunk.chunk_id,offset)
        self._index=BM25Index(tokenizer=lexical_tokens,searchable_fields=())
        self._index.build(windows);self._signature=signature
        self.last_cache_stats={'reused':False,'windows':len(windows),'chunks':len(chunks)}

    @staticmethod
    def expand(query):
        aliases=list(dict.fromkeys(value for key,value in ALIASES.items() if key in query))[:12]
        return query+ ('\n检索同义术语：'+'; '.join(aliases) if aliases else ''),aliases

    def search(self,query,scoped_chunks,top_k=6):
        if not scoped_chunks or top_k<=0:return []
        expanded,_=self.expand(query)
        originals={c.chunk_id:c for c in scoped_chunks}
        self.prepare(scoped_chunks)
        ranked=self._index.search(expanded,len(self._index.chunks));best={}
        for hit in ranked:
            identity,offset=self._origins[hit.chunk_id]
            if identity not in best:best[identity]={'lexical_score':hit.score,'window_offset':offset}
        pairs=NUMBER_UNIT.findall(query);results=[]
        for identity,chunk in originals.items():
            evidence=best.get(identity,{'lexical_score':0.,'window_offset':None})
            caption=exact_caption(query,chunk);headers=header_matches(query,chunk)
            numeric=[];body=self._bodies[identity]
            table_refs=sorted(set(TABLE_REFERENCE.findall(query)) & set(TABLE_REFERENCE.findall(body)))
            for number,unit in pairs:
                if re.search(r'(?<![A-Za-z0-9_.+\-−±])'+re.escape(number)+r'\s*'+re.escape(unit)+r'(?![A-Za-z0-9])',body):numeric.append(number+' '+unit)
            # Publisher/title is a soft locator, never an extra scope filter.
            description=' '.join(str(chunk.metadata.get(k,'')) for k in ('source_title','source_publisher','title'))
            acronyms=[token for token in re.findall(r'(?<![A-Za-z])[A-Z]{3,8}(?![A-Za-z])',query)
                      if re.search(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])',description)]
            # Definition lines are stronger evidence for an abbreviation question
            # than a prose mention. Read the actual source, never hard-code expansions.
            definitions=[]
            if ACRONYM_INTENT.search(query):
                for term in dict.fromkeys(re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9/]{1,9}(?![A-Za-z0-9])',query)):
                    if re.search(r'(?m)^\s*'+re.escape(term)+r'\s+(?:[A-Za-z][A-Za-z-]*[ ,]+){1,}[A-Za-z]',body):definitions.append(term)
            version_lines=[line.strip() for line in VERSION_LINE.findall(body)] if VERSION_INTENT.search(query) else []
            value=evidence['lexical_score']+.35*min(len(numeric),2)+.15*bool(acronyms)+.2*min(len(headers),3)+2*caption+.65*bool(table_refs)+.55*min(len(definitions),3)+1.4*bool(version_lines)
            if value<=0:continue
            metadata={**chunk.metadata,'evidence_recall_score':round(value,6),'evidence_window_offset':evidence['window_offset'],
                      'evidence_window_offset_scope':'derived_search_text_only','evidence_lexical_score':evidence['lexical_score'],
                      'evidence_numeric_matches':numeric,'evidence_header_matches':headers,'evidence_source_locators':acronyms,
                      'evidence_exact_caption':caption,'evidence_table_references':table_refs,'evidence_definition_terms':definitions,'evidence_version_lines':version_lines}
            results.append(RetrievalHit(doc_id=chunk.doc_id,chunk_id=chunk.chunk_id,content=chunk.content,
                                       metadata=metadata,score=value,retrieval_sources=['source_evidence']))
        return sorted(results,key=lambda h:(h.score,h.chunk_id),reverse=True)[:top_k]

    @staticmethod
    def merge(hits,extra,top_k):
        if not extra:return hits
        originals={h.chunk_id:h for h in hits};enriched=[]
        for hit in extra:
            if hit.chunk_id in originals:
                old=originals[hit.chunk_id].model_copy(deep=True)
                old.metadata={**old.metadata,**{k:v for k,v in hit.metadata.items() if k.startswith('evidence_')}}
                old.retrieval_sources=list(dict.fromkeys([*old.retrieval_sources,'source_evidence']));hit=old
            enriched.append(hit)
        fused=RrfFusion().fuse([hits,enriched],len(hits)+len(enriched))
        for hit in fused:hit.metadata={**hit.metadata,'evidence_fusion_score':hit.score}
        quota=min(len(enriched),max(1,top_k//5));required={h.chunk_id for h in enriched[:quota]}
        selected=[h for h in fused if h.chunk_id not in required][:max(0,top_k-quota)]
        selected+=enriched[:quota]
        return sorted(selected,key=lambda h:h.score,reverse=True)

    @staticmethod
    def prefer_literal_evidence(ranked,query,scoped_chunks):
        # The raw source caption is checked, not arbitrary metadata or added OCR text.
        originals={c.chunk_id:c for c in scoped_chunks}
        anchors=[]
        for hit in ranked:
            ids={hit.chunk_id,*hit.metadata.get('evidence_chunk_ids',[])}
            matches=[originals[i] for i in ids if i in originals and originals[i].doc_id==hit.doc_id
                     and exact_caption(query,originals[i])]
            if matches:
                actual=[c.chunk_id for c in matches if c.content in hit.content]
                if actual:
                    hit.metadata={**hit.metadata,'selection_reason':'exact_source_caption_requested','literal_source_chunk_ids':actual}
                    anchors.append(hit);continue
            if VERSION_INTENT.search(query) and hit.metadata.get('evidence_version_lines'):
                hit.metadata={**hit.metadata,'selection_reason':'explicit_source_edition_line'};anchors.append(hit);continue
            original=originals.get(hit.chunk_id);headers=header_matches(query,original) if original else []
            if len(headers)>=2:
                hit.metadata={**hit.metadata,'selection_reason':'multiple_explicit_source_headers','matched_source_headers':headers}
                anchors.append(hit)
        chosen={h.chunk_id for h in anchors}
        return anchors+[h for h in ranked if h.chunk_id not in chosen]
