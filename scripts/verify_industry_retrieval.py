"""Actual-library source lookup and retrieval contract checks, not industry gold scoring."""
import argparse,json,sqlite3,time
from pathlib import Path
from statistics import median
from urllib.request import Request,ProxyHandler,build_opener
from uuid import uuid4

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--store',default='data/runtime');parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    opener=build_opener(ProxyHandler({}));root='http://127.0.0.1:8000/api/v2/workbench'
    def call(path,body=None):
        req=Request(root+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=240) as response:return json.load(response)
    with sqlite3.connect('file:'+str(Path(args.store)/'knowledge.sqlite3')+'?mode=ro',uri=True) as db:
        docs=[json.loads(r[0]) for r in db.execute("select payload from documents where deleted=0 and json_extract(payload,'$.image_sha256') is not null")]
        ocr_rows=[json.loads(r[0]) for r in db.execute("select payload from chunks where json_extract(payload,'$.metadata.chunk_kind')='image_ocr' and json_extract(payload,'$.metadata.knowledge_base_id')='materials'")]
    cases=[]
    for base,word in [('automotive','电动汽车制造'),('high_end_equipment','工业机器人'),('electronics','PCB'),('materials','PVC生产工艺流程图'),('energy','电解水'),('cosmetics_manufacturing','玻璃')]:
        candidates=[d for d in docs if d['knowledge_base_id']==base]
        for d in candidates:
            if not d.get('source_caption'):d['source_caption']=Path(d['source_caption_asset']).read_text(encoding='utf-8-sig').strip()
        target=next((d for d in candidates if word in d['source_caption'] and 8<=len(d['source_caption'])<=120),candidates[0])
        gold=[d['document_id'] for d in candidates if d['source_caption']==target['source_caption'] or (target.get('source_page') and d.get('source_page')==target['source_page'])]
        cases.append({'knowledge_base_id':base,'query':target['source_caption'][:120],'gold_doc_ids':gold,'selected_document_id':target['document_id']})
    (out/'source-lookup-cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
    results=[]
    def check(case,mode,fusion='rrf'):
        body={'question':case['query'],'knowledge_base_id':case['knowledge_base_id'],
              'options':{'runtime_mode':'configured','retrieval_mode':mode,'fusion_method':fusion,'top_k':30,'final_top_k':5,
                         'query_rewrite':False,'query_expansion':False,'metadata_router':False,'query_transformation':False,
                         'parent_recovery':mode.startswith('parent_child'),'rerank':True,'compression':True}}
        trace=call('/retrieve',body);hits=trace['reranked_docs']
        item={'knowledge_base_id':case['knowledge_base_id'],'mode':mode,'fusion':fusion,'trace_id':trace['trace_id'],
              'source_hit':any(h['doc_id'] in case['gold_doc_ids'] for h in hits),'hits':len(hits),
              'scope_correct':all(h['metadata']['knowledge_base_id']==case['knowledge_base_id'] for h in hits),
              'source_ids_resolve':True,'latency_ms':trace['latency_ms'],'runtime':trace['runtime']}
        for h in hits:
            current=call('/chunks/'+h['chunk_id']);assert current['doc_id']==h['doc_id']
            assert current['metadata']['revision_id']==h['metadata']['revision_id']
            for identity in h['metadata'].get('evidence_chunk_ids',[]):
                evidence=call('/chunks/'+identity)
                assert evidence['doc_id']==h['doc_id'] and evidence['metadata']['revision_id']==h['metadata']['revision_id']
        (out/(trace['trace_id']+'.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2))
        assert item['scope_correct'] and item['source_hit'],item
        results.append(item);print(case['knowledge_base_id']+' '+mode+' passed',flush=True)
    for case in cases:
        for mode in ['bm25','vector','hybrid']:check(case,mode)
    for mode in ['parent_child','parent_child_hybrid','summary','rag_fusion']:check(cases[3],mode)
    check(cases[3],'hybrid','weighted')
    empty=call('/retrieve',{'question':'玻璃瓶 化妆品','knowledge_base_id':'consumer_goods','options':{'runtime_mode':'configured','retrieval_mode':'hybrid'}})
    assert not empty['reranked_docs'],'Exclusive cosmetics leaked into parent scope'
    visual=[]
    for case in cases:
        r=call('/documents/'+case['selected_document_id']+'/similar-images?knowledge_base_id='+case['knowledge_base_id'])
        assert r['items'] and r['dimension']==768
        assert all(i['knowledge_base_id']==case['knowledge_base_id'] and -1<=i['score']<=1 for i in r['items'])
        visual.append({'knowledge_base_id':case['knowledge_base_id'],'hits':len(r['items']),'dimension':r['dimension']})
    timings={}
    for name in ['documents','chunks']:
        values=[]
        for _ in range(5):
            t=time.perf_counter();r=call('/'+name+'?page=2&page_size=50');values.append((time.perf_counter()-t)*1000);assert len(r['items'])==50
        timings[name]={'median_ms':round(median(values),2),'samples_ms':values}
    # Readable terms already present in machine OCR; this checks evidence transfer,
    # not whether a web diagram is an approved manufacturing process.
    ocr=next(c for c in ocr_rows if all(term in c['content'] for term in ['高速混合','低速搅拌','塑炼造粒']))
    picture=next(d for d in docs if d['document_id']==ocr['doc_id'])
    recovered=call('/retrieve',{'question':picture['source_caption'],'knowledge_base_id':'materials','document_id':picture['document_id'],
        'options':{'runtime_mode':'configured','retrieval_mode':'hybrid','top_k':5,'final_top_k':3,'parent_recovery':False,
                   'compression':False,'query_rewrite':False,'query_expansion':False,'image_context':True}})
    assert len(recovered['reranked_docs'])==1
    evidence=recovered['reranked_docs'][0]
    assert evidence['metadata']['image_context_recovered'] and ocr['chunk_id'] in evidence['metadata']['evidence_chunk_ids']
    assert all(term in evidence['content'] for term in ['高速混合','低速搅拌','塑炼造粒'])
    (out/'ocr-context-recovery.json').write_text(json.dumps(recovered,ensure_ascii=False,indent=2))
    session=call('/sessions',{'knowledge_base_id':'materials'})
    body={'question':'【图文链路验收】请从这张硬质 PVC 管材工艺参考图的 OCR 证据中列出三个可读的工艺术语。说明自动识别未经审核，不推测流程顺序或参数，并引用来源。',
          'request_id':'qa_'+uuid4().hex,'options':{'runtime_mode':'configured','retrieval_mode':'hybrid','fusion_method':'rrf','top_k':10,'final_top_k':3,
          'query_rewrite':False,'query_expansion':False,'metadata_router':False,'query_transformation':False,'parent_recovery':False,
          'compression':False,'image_context':True,'metadata_filter':{'document_id':picture['document_id']}}}
    req=Request(root+'/sessions/'+session['session_id']+'/stream',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    events=[]
    with opener.open(req,timeout=240) as response:
        for raw in response:
            line=raw.decode('utf-8').strip()
            if line.startswith('data:'):events.append(json.loads(line[5:]))
    (out/'sse-events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    assert not any(e['type']=='error' for e in events),events[-1]
    assert any(e['type']=='done' for e in events) and any(e['type']=='token' for e in events)
    done=next(e for e in events if e['type']=='done')
    assert sum(word in done['answer'] for word in ['高速混合','低速搅拌','塑炼造粒','冷却定型','捆扎包装'])>=2,done['answer']
    source_ids={s['chunk_id'] for s in done['sources']}|{i for s in done['sources'] for i in s['metadata'].get('evidence_chunk_ids',[])}
    assert any(identity in done['answer'] for identity in source_ids),'Missing real source citation'
    persisted=call('/sessions/'+session['session_id']);(out/'sse-session.json').write_text(json.dumps(persisted,ensure_ascii=False,indent=2))
    assert persisted['messages'][-1]['status']=='completed' and persisted['messages'][-1]['content']==done['answer']
    report={'status':'passed','scope':'source_caption_lookup_smoke_not_semantic_quality_gold','text_checks':results,
            'empty_parent_scope_isolated':True,'visual_checks':visual,'pagination':timings,'sse_session_id':session['session_id'],
            'token_events':sum(e['type']=='token' for e in events),'source_captions_verified_as_industry_facts':False,
            'same_image_ocr_recovered':True,'sse_ocr_terms_and_citation_verified':True,
            'official_ragas':False,'training_performed':False}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('Verified '+str(len(results))+' retrieval configurations, 6 visual scopes and real SSE',flush=True)
if __name__=='__main__':main()
