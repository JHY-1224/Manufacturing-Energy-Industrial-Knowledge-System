"""Inspectable retrieval/citation contracts; no training or fabricated semantic scores."""
import json
from html import unescape
from collections import Counter
from pathlib import Path
from statistics import mean
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models import QueryOptions, QueryRequest
from app.evaluation.report_generator import ReportGenerator
from app.storage.knowledge_store import digest, now


class QualityCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: str
    category: str
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_table_id: str | None = None
    expected_empty: bool = False
    required_evidence: list[str] = Field(default_factory=list)
    required_answer_fragments: list[str] = Field(default_factory=list)
    document_id: str | None = None
    review_mode: str = 'reference'
    review_status: str = 'source_checked_pending_human'
    reviewer: str | None = None
    evidence_snapshots: list[dict] = Field(default_factory=list)
    limitation: str = ''
    training_allowed: bool = False

    @model_validator(mode='after')
    def contract(self):
        from app.knowledge.catalog import validate_base
        validate_base(self.knowledge_base_id)
        if self.training_allowed or self.review_mode not in {'reference','published_only'}:
            raise ValueError('Quality cases are evaluation-only with an explicit review scope')
        if self.review_status not in {'synthetic_contract','source_checked_pending_human','human_reviewed','ai_source_reviewed'}:
            raise ValueError('Unknown evaluation review status')
        if self.review_status in {'human_reviewed','ai_source_reviewed'} and not (self.reviewer or '').strip():
            raise ValueError('Review requires reviewer attribution')
        if self.expected_empty == bool(self.expected_doc_ids or self.expected_chunk_ids):
            raise ValueError('Positive cases require reference IDs; empty cases cannot contain positive references')
        if any(not x.strip() for x in self.required_evidence+self.required_answer_fragments):
            raise ValueError('Evidence and answer fragments cannot be blank')
        if not self.expected_empty and not self.required_evidence:
            raise ValueError('Positive cases require inspectable evidence fragments')
        return self


def load_quality_cases(path):
    path=Path(path)
    if path.stat().st_size > 4*1024*1024:
        raise ValueError('Quality dataset exceeds 4 MiB')
    cases=[QualityCase.model_validate(json.loads(line)) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not 1 <= len(cases) <= 1000 or len({c.query_id for c in cases}) != len(cases):
        raise ValueError('Quality dataset requires 1-1000 unique cases')
    return cases


def validate_quality_sources(engine, cases):
    engine.refresh(); chunks={c.chunk_id:c for c in engine.chunks}
    documents={c.doc_id for c in engine.chunks}
    for case in cases:
        if set(case.expected_doc_ids)-documents or set(case.expected_chunk_ids)-set(chunks):
            raise ValueError('Unknown quality reference: '+case.query_id)
        reference=[c for c in chunks.values() if c.chunk_id in case.expected_chunk_ids or
                   (not case.expected_chunk_ids and c.doc_id in case.expected_doc_ids and
                    (not case.expected_table_id or c.metadata.get('table_id')==case.expected_table_id))]
        if any(c.metadata.get('knowledge_base_id') != case.knowledge_base_id for c in reference):
            raise ValueError('Quality reference is outside its knowledge base: '+case.query_id)
        if any(c.metadata.get('evidence_kind')=='training_reference' or c.metadata.get('doc_type')=='text_training_reference' for c in reference):
            raise ValueError('Training answers cannot serve as independent quality gold: '+case.query_id)
        text=unescape('\n'.join(c.content for c in reference))
        if any(fragment not in text for fragment in case.required_evidence):
            raise ValueError('Expected fact is absent from frozen evidence: '+case.query_id)
        for snapshot in case.evidence_snapshots:
            chunk=chunks.get(snapshot.get('chunk_id'))
            if not chunk or chunk.metadata.get('revision_id') != snapshot.get('revision_id') or digest(chunk.content.encode()) != snapshot.get('content_sha256'):
                raise ValueError('Frozen evidence revision changed: '+case.query_id)
            if snapshot.get('source_sha256') and snapshot['source_sha256'] != chunk.metadata.get('source_sha256'):
                raise ValueError('Frozen source fingerprint changed: '+case.query_id)


class QualityBenchmark:
    def __init__(self, engine):
        self.engine=engine

    def run(self, cases, runtime_mode='offline', table_retrieval=False, modes=('bm25','vector','hybrid'),
            answer_ids=(), progress=None, evidence_retrieval=False, cross_language_query=False):
        validate_quality_sources(self.engine,cases)
        if set(modes)-{'bm25','vector','hybrid'} or not modes:
            raise ValueError('Unsupported quality benchmark strategy')
        if set(answer_ids)-{c.query_id for c in cases}:
            raise ValueError('Unknown requested answer case')
        started=now();samples=[];failure_cases=[]
        document_for_chunk={c.chunk_id:c.doc_id for c in self.engine.chunks}
        for case in cases:
            expected_docs=set(case.expected_doc_ids) or {document_for_chunk[i] for i in case.expected_chunk_ids}
            for mode in modes:
                filters={'knowledge_base_id':case.knowledge_base_id}
                if case.document_id:filters['document_id']=case.document_id
                options=QueryOptions(runtime_mode=runtime_mode,retrieval_mode=mode,fusion_method='rrf',
                    top_k=30,final_top_k=1 if case.query_id in answer_ids else 5,
                    query_rewrite=False,query_expansion=False,metadata_router=False,query_transformation=False,
                    parent_recovery=False,compression=True,rerank=runtime_mode=='configured',
                    table_retrieval=table_retrieval,evidence_retrieval=evidence_retrieval,cross_language_query=cross_language_query,review_mode=case.review_mode,metadata_filter=filters)
                generate=case.expected_empty or (case.query_id in answer_ids and mode=='hybrid')
                try:
                    trace=self.engine.query(QueryRequest(question=case.question,options=options,generate_answer=generate))
                    hits=trace.reranked_docs
                    def identities(hit):
                        return {hit.chunk_id,*hit.metadata.get('evidence_chunk_ids',[]),hit.metadata.get('source_chunk_id',hit.chunk_id)}
                    doc_hit=any(h.doc_id in expected_docs for h in hits)
                    chunk_hit=any(identities(h)&set(case.expected_chunk_ids) for h in hits) if case.expected_chunk_ids else None
                    table_hit=any(h.metadata.get('table_id')==case.expected_table_id and h.doc_id in expected_docs for h in hits) if case.expected_table_id else None
                    matched=[context for h,context in zip(hits,trace.compressed_context)
                             if identities(h)&set(case.expected_chunk_ids) or (not case.expected_chunk_ids and h.doc_id in expected_docs
                                and (not case.expected_table_id or h.metadata.get('table_id')==case.expected_table_id))]
                    checks={'scope_ok':all(h.metadata.get('knowledge_base_id')==case.knowledge_base_id and
                                (case.review_mode!='published_only' or h.metadata.get('knowledge_review_state')=='published') for h in hits),
                            'document_hit':doc_hit if not case.expected_empty else None,'chunk_hit':chunk_hit,'table_hit':table_hit,
                            'required_evidence_present':all(f in unescape('\n'.join(matched)) for f in case.required_evidence) if not case.expected_empty else None,
                            'protected_context_complete':all(h.content in context for h,context in zip(hits,trace.compressed_context) if h.metadata.get('protected_structure')),
                            'empty_scope_refusal':not hits and bool(trace.answer) and not any(c['stage']=='chat' for c in trace.runtime.get('calls',[])) if case.expected_empty else None,
                            'answer_fragments_present':all(f in trace.answer for f in case.required_answer_fragments) if generate and not case.expected_empty else None,
                            'answer_cites_actual_source':any(h.chunk_id in trace.answer for h in hits) if generate and not case.expected_empty else None,
                            'evidence_revision_checked':trace.runtime.get('evidence_revision_checked',False)}
                    sample={'eval_id':case.query_id+'__'+mode,'question':case.question,'strategy':mode,'category':case.category,
                        'knowledge_base_id':case.knowledge_base_id,'review_status':case.review_status,
                        'reference_context_ids':case.expected_chunk_ids or case.expected_doc_ids,
                        'ground_truth':' / '.join(case.required_answer_fragments or case.required_evidence),
                        'generated_answer':trace.answer if generate else '本样本仅检查检索与证据，未生成答案。',
                        'retrieved_contexts':trace.compressed_context,'retrieved_chunk_ids':[h.chunk_id for h in hits],
                        'checks':checks,'metrics':{},'trace_id':trace.trace_id,'runtime':trace.runtime,
                        'latency_ms':trace.latency_ms,'answer_generated':generate,'limitation':case.limitation}
                    sample.update(query_options=trace.options,token_usage=trace.token_usage)
                    failures=[k for k,v in checks.items() if v is False]
                    sample['failure_type']='quality_contract_failed' if failures else None
                    sample['root_cause']=', '.join(failures)
                except Exception as exc:
                    sample={'eval_id':case.query_id+'__'+mode,'question':case.question,'strategy':mode,
                        'category':case.category,'knowledge_base_id':case.knowledge_base_id,'review_status':case.review_status,
                        'checks':{'scope_ok':False,'evidence_revision_checked':False,
                                  'document_hit':False if not case.expected_empty else None,
                                  'table_hit':False if case.expected_table_id else None,
                                  'empty_scope_refusal':False if case.expected_empty else None},
                        'metrics':{},'failure_type':'execution_error','root_cause':type(exc).__name__,
                        'trace_id':getattr(exc,'trace_id',None),'answer_generated':False}
                samples.append(sample)
                if sample['failure_type']:failure_cases.append(sample)
                if progress:progress({'eval_id':sample['eval_id'],'checks':sample['checks'],'failure_type':sample['failure_type']})
        names=sorted({k for sample in samples for k in sample['checks']})
        metrics={key+'_rate':round(mean(float(s['checks'][key]) for s in samples if s['checks'].get(key) is not None),4)
                 for key in names if any(s['checks'].get(key) is not None for s in samples)}
        errors=sum(s['failure_type']=='execution_error' for s in samples)
        latencies=sorted(s['latency_ms']['total'] for s in samples if s.get('latency_ms',{}).get('total') is not None)
        return {'run_id':'quality_'+uuid4().hex,'experiment':'knowledge_quality_contracts','status':'failed' if errors else 'completed',
            'started_at':started,'finished_at':now(),'case_count':len(cases),'sample_count':len(samples),
            'evaluation_mode':'source_reference_contracts','evaluation_limitation':'检索、来源版本、字面事实片段与引用检查；不是官方 RAGAS、语义裁判或工业正确率。待人工确认的真实来源用例不作为已审核金标。',
            'options':{'runtime_mode':runtime_mode,'table_retrieval':table_retrieval,'evidence_retrieval':evidence_retrieval,'cross_language_query':cross_language_query,'modes':list(modes)},
            'review_status_counts':dict(Counter(c.review_status for c in cases)),
            'metrics':metrics,'execution_errors':errors,'sample_results':samples,'failed_cases':failure_cases,
            'latency_ms':{'p50':latencies[len(latencies)//2],'p95':latencies[min(len(latencies)-1,int(len(latencies)*.95))]} if latencies else {},
            'query_translation_calls':sum(call.get('stage')=='query_translation' for sample in samples for call in sample.get('runtime',{}).get('calls',[])),
            'generation_calls':sum(call.get('stage')=='chat' for sample in samples for call in sample.get('runtime',{}).get('calls',[])),
            'training_performed':False,'training_answers_used_as_gold':False}

    @staticmethod
    def write(report, directory):
        return ReportGenerator(directory).write('knowledge_quality_contracts',report)
