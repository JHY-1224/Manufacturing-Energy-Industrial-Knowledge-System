"""Run an independent synthetic retrieval baseline in a new isolated store; never import it into the main library."""
import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from statistics import mean
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core.engine import ConfigurableRagEngine
from app.core.models import RagChunk,QueryOptions,QueryRequest
from app.knowledge.catalog import base_policy
from app.storage.knowledge_store import KnowledgeStore,digest,now
from app.tasks.service import BackgroundTasks
from app.evaluation.retrieval_metrics import calculate_retrieval_metrics
from scripts.run_local_stack import local_environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-mode',choices=['offline','configured'],default='offline')
    parser.add_argument('--output-parent',default=str(ROOT/'data/runtime/p2-baselines'))
    parser.add_argument('--answers',action='store_true',help='Generate six local acceptance answers after retrieval comparison')
    args=parser.parse_args()
    if args.runtime_mode=='configured':os.environ.update(local_environment())
    fixture=ROOT/'tests/fixtures/p2_baseline';data=json.loads((fixture/'corpus.json').read_text())
    cases=[json.loads(line) for line in (fixture/'queries.jsonl').read_text().splitlines() if line.strip()]
    output=Path(args.output_parent)/uuid4().hex;store=KnowledgeStore(output/'isolated-corpus');store.set_meta('initialized','1')
    assert store.directory!=ROOT/'data/runtime'
    for row in data['records']:
        doc=store.register('synthetic_fixture:'+row['doc_id'],{'document_id':row['doc_id'],'source_name':row['title']+'.md',
            'title':row['title'],'knowledge_base_id':row['knowledge_base_id'],'domain':row['knowledge_base_id'],
            'doc_type':'synthetic_fixture','data_source':'synthetic_evaluation_only','created_at':now(),**base_policy(row['knowledge_base_id'])})
        doc.update(revision_id='fixture_revision_'+row['doc_id'],status='indexed',chunk_count=1)
        chunk=RagChunk(doc_id=row['doc_id'],chunk_id=row['chunk_id'],content=row['content'],metadata={
            'knowledge_base_id':row['knowledge_base_id'],'document_id':row['doc_id'],'source':doc['source_name'],
            'revision_id':doc['revision_id'],'data_source':'synthetic_evaluation_only',**base_policy(row['knowledge_base_id'])})
        store.replace_document(doc,[chunk.model_dump()],{'operation':'isolated_synthetic_fixture'})
    worker=BackgroundTasks(store);task=worker.create('index',runtime_mode=args.runtime_mode);claimed=worker.tasks.claim(worker.token);worker._run(claimed)
    ready=worker.tasks.get(task['task_id']);assert ready['status']=='completed',ready
    engine=ConfigurableRagEngine(store);results=[]
    for strategy in ['bm25','vector','hybrid']:
        samples=[]
        for case in cases:
            options=QueryOptions(runtime_mode=args.runtime_mode,retrieval_mode=strategy,fusion_method='rrf',query_rewrite=False,query_expansion=False,
                query_transformation=False,metadata_router=False,parent_recovery=False,compression=False,rerank=strategy=='hybrid',
                top_k=8,final_top_k=5,metadata_filter={'knowledge_base_id':case['knowledge_base_id']})
            start=perf_counter();trace=engine.query(QueryRequest(question=case['query'],options=options,generate_answer=False))
            ids=list(dict.fromkeys(hit.doc_id for hit in trace.reranked_docs))
            metrics=calculate_retrieval_metrics(ids,case['gold_doc_ids'],{}) if case['gold_doc_ids'] else {}
            samples.append({'query_id':case['query_id'],'question':case['query'],'gold_doc_ids':case['gold_doc_ids'],'retrieved_doc_ids':ids,
                'metrics':metrics,'scope_isolation_passed':not ids if case['expected_empty'] else all(hit.metadata['knowledge_base_id']==case['knowledge_base_id'] for hit in trace.reranked_docs),
                'milliseconds':round((perf_counter()-start)*1000,3),'trace_id':trace.trace_id,'index_cache':trace.runtime.get('index_cache'),
                'embedding_calls':sum(c['stage']=='embedding' for c in trace.runtime.get('calls',[]))})
        keys=sorted({k for sample in samples for k in sample['metrics']})
        results.append({'strategy':strategy,'metrics':{k:mean(sample['metrics'][k] for sample in samples if k in sample['metrics']) for k in keys},
            'scope_checks_passed':sum(sample['scope_isolation_passed'] for sample in samples),'samples':samples})
    answers=[]
    if args.answers:
        for case in [cases[i] for i in [0,2,4,8,11,13]]:
            options=QueryOptions(runtime_mode=args.runtime_mode,retrieval_mode='hybrid',fusion_method='rrf',query_rewrite=False,query_expansion=False,
                query_transformation=False,metadata_router=False,parent_recovery=False,compression=False,rerank=True,
                top_k=8,final_top_k=5,metadata_filter={'knowledge_base_id':case['knowledge_base_id']})
            trace=engine.query(QueryRequest(question=case['query']+' 请保留资料中的编号和数值，并引用来源 ID。',options=options))
            expected=all(fragment.casefold() in trace.answer.casefold() for fragment in case['required_answer_fragments'])
            if case['expected_empty']:expected=expected and '无法依据知识库回答' in trace.answer and not any(c['stage']=='chat' for c in trace.runtime.get('calls',[]))
            cited=any(hit.chunk_id in trace.answer for hit in trace.reranked_docs) if case['gold_doc_ids'] else not trace.reranked_docs
            answers.append({'query_id':case['query_id'],'answer':trace.answer,'trace_id':trace.trace_id,'expected_fragments_present':expected,'source_citation_present':cited,
                'check_kind':'literal_fixture_assertions_not_industry_accuracy_or_llm_judge'})
    report={'status':'completed','scope':'isolated_synthetic_engineering_baseline','runtime_mode':args.runtime_mode,
        'corpus_documents':len(data['records']),'queries':len(cases),'source_hashes':{p.name:digest(p.read_bytes()) for p in [fixture/'corpus.json',fixture/'queries.jsonl']},
        'source_training_material_used':False,'main_library_modified':False,'official_ragas':False,'manual_industry_review':'not_applicable_synthetic_only',
        'index_task':ready,'retrieval_experiments':results,'answer_checks':answers}
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(output),'strategies':[{k:r[k] for k in ['strategy','metrics','scope_checks_passed']} for r in results],
         'answer_checks_passed':sum(r['expected_fragments_present'] and r['source_citation_present'] for r in answers),'answer_checks':len(answers)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
