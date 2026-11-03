"""Bounded concurrency/soak validation on a new database snapshot; never live data."""
import argparse,json,os,resource,sqlite3,sys,time,subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--store',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--minutes',type=float,default=30);a=p.parse_args()
 assert 0<a.minutes<=240 and not a.output.exists();a.output.mkdir(parents=True);snapshot=a.output/'snapshot';snapshot.mkdir()
 with sqlite3.connect((a.store/'knowledge.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(snapshot/'knowledge.sqlite3') as dst:src.backup(dst)
 from scripts.run_local_stack import local_environment
 os.environ.update(local_environment())
 from app.storage.knowledge_store import KnowledgeStore
 from app.core.engine import ConfigurableRagEngine
 from app.core.models import QueryOptions,QueryRequest
 from app.storage.workbench_store import WorkbenchStore
 engine=ConfigurableRagEngine(KnowledgeStore(snapshot));records=WorkbenchStore(engine.store)
 requests=[('high_end_equipment','NIST报告缩写 AGV 与 AMR 的全称'),('electronics','TI MSL 3 168 hours'),('energy','steam pilot energy performance'),('consumer_goods','FDA raw materials weighing second person')]
 def query(item,mode='offline'):
  base,q=item;t=time.monotonic();trace=engine.query(QueryRequest(question=q,options=QueryOptions(runtime_mode=mode,retrieval_mode='hybrid' if mode=='configured' else 'bm25',top_k=30,final_top_k=5,query_rewrite=False,query_expansion=False,metadata_router=False,query_transformation=False,parent_recovery=False,rerank=mode=='configured',table_retrieval=True,evidence_retrieval=True,metadata_filter={'knowledge_base_id':base}),generate_answer=False))
  assert all(h.metadata['knowledge_base_id']==base for h in trace.reranked_docs)
  assert trace.runtime['evidence_revision_checked']
  return {'elapsed_ms':round((time.monotonic()-t)*1000,2),'trace_id':trace.trace_id,'ids':[h.chunk_id for h in trace.reranked_docs]}
 with ThreadPoolExecutor(max_workers=4) as pool:real=list(pool.map(lambda q:query(q,'configured'),requests*2))
 baseline=[query(q)['ids'] for q in requests];started=time.monotonic();count=0;errors=[];rss=[];times=[]
 with (a.output/'observations.jsonl').open('x') as log,ThreadPoolExecutor(max_workers=4) as pool:
  while time.monotonic()-started<a.minutes*60:
   batch=list(pool.map(query,requests))
   for i,v in enumerate(batch):
    assert v['ids']==baseline[i], 'Concurrent ranking changed';times.append(v['elapsed_ms'])
   count+=len(batch);usage=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;rss.append(usage)
   assert len(engine.traces)<=256
   page=records.sessions_page(page_size=20);assert len(page['items'])<=20
   log.write(json.dumps({'seconds':round(time.monotonic()-started,2),'queries':count,'rss_high_water_bytes':usage,'trace_cache':len(engine.traces),'scope_caches':len(engine._evidence_cache),'max_batch_ms':max(v['elapsed_ms'] for v in batch)})+'\n');log.flush()
   time.sleep(min(5,max(0,a.minutes*60-(time.monotonic()-started))))
 with sqlite3.connect(snapshot/'knowledge.sqlite3') as db:integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
 result={'status':'passed','duration_seconds':time.monotonic()-started,'workers':4,'offline_soak_queries':count,'configured_concurrent_queries':len(real),'configured_latency_ms':[v['elapsed_ms'] for v in real],'errors':errors,'trace_cache_bounded':len(engine.traces)<=256,'first_saved_trace_reloadable':engine.get_trace(real[0]['trace_id']) is not None,'rss_first_bytes':rss[0],'rss_high_water_bytes':max(rss),'integrity':integrity,'scope_violations':0,'limitation':'30-minute local bounded soak, not 24-hour or production capacity SLA'}
 (a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
if __name__=='__main__':main()
