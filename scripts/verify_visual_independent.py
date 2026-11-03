"""Frozen, in-corpus multimodal acceptance; outputs never become training data."""
import argparse,json,os,sys,time,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest
from app.knowledge.multimodal import MultimodalService,LocalVisionClient
from app.knowledge.visual_screening import screen
from scripts.run_local_stack import local_environment

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',type=Path);p.add_argument('--freeze',type=Path);p.add_argument('--query-inputs',type=Path);p.add_argument('--resume',action='store_true');p.add_argument('--development-regression',action='store_true');p.add_argument('--vision-url',default='http://127.0.0.1:8083');a=p.parse_args();r=a.root;out=a.output;cases=a.cases or r/'independent-visual12.json';frozen=json.loads((a.freeze or r/'independent-freeze.json').read_text());print(frozen,flush=True)
 if digest(cases.read_bytes())!=frozen['sha256']:raise ValueError('Frozen case fingerprint changed')
 os.environ.update(local_environment());store=KnowledgeStore(r/'snapshot');store.asset_dir=(Path.cwd()/'data/runtime/originals').resolve();mm=MultimodalService(store,LocalVisionClient(a.vision_url));health=mm.client.health();fp=health['vision_fingerprint'];xs=json.loads(cases.read_text());results=[];described=[]
 with store.connection() as db:
  expected=db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.image_sha256') IS NOT NULL").fetchone()[0]
  covered=db.execute("SELECT count(*) FROM multimodal_assets a JOIN documents d ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE a.kind='observation' AND a.fingerprint=? AND json_extract(a.payload,'$.ai_screening.source_revision_verified')=1 AND d.deleted=0",(fp,)).fetchone()[0]
 if covered!=expected:raise ValueError(f'Independent acceptance requires full description coverage: {covered}/{expected}')
 if a.resume:
  if (out/'results.json').exists():raise FileExistsError('Completed acceptance must not be rerun into the same output')
  results=json.loads((out/'results.partial.json').read_text())
  if len({v['case_id'] for v in results})!=len(results):raise ValueError('Duplicate partial acceptance cases')
 else:out.mkdir(parents=True,exist_ok=False)
 query_inputs={}
 if a.query_inputs:
  for v in json.loads(a.query_inputs.read_text())['items']:
   if set(v)!={'case_id','source_sha256','query_image_path','query_image_sha256'}:raise ValueError('Query adapter may not override expected sources or questions')
   query_inputs[v['case_id']]=v
 for c in xs:
  entry=next(v for v in mm.observations(c['document_id'])['items'] if v['fingerprint']==fp)
  described.append({'case_id':c['id'],'document_id':c['document_id'],'description':entry['observation'],'screening':entry['ai_screening']})
 if a.resume:
  if json.loads((out/'descriptions.json').read_text())!=described:raise ValueError('Observations changed during interrupted independent run')
 else:(out/'descriptions.json').write_text(json.dumps(described,ensure_ascii=False,indent=2))
 bases=list(dict.fromkeys(c['knowledge_base_id'] for c in xs))
 for c in xs:
  d=store.get_document(c['document_id'])
  if (d['revision_id'],d['source_sha256'])!=(c['revision_id'],c['source_sha256']):raise ValueError('Frozen independent source changed')
  for kind in ['text','new_image','scope']:
   if any(v['case_id']==c['id']+'_'+kind for v in results):continue
   start=time.monotonic()
   if kind=='text':query=dict(text=c['question'],cross_language_query=True)
   elif kind=='new_image':
    # Different image bytes from the corpus original: rasterized downsampled PNG.
    adapter=query_inputs.get(c['id'])
    if adapter and adapter['source_sha256']!=c['source_sha256']:raise ValueError('Query adapter source changed')
    image_input=adapter or c
    if image_input.get('query_image_path'):
     data=Path(image_input['query_image_path']).read_bytes()
     if digest(data)!=image_input['query_image_sha256']:raise ValueError('Frozen query input changed')
    else:
     import fitz
     path=Path(c['source_path']);pix=fitz.Pixmap(str(path))
     if max(pix.width,pix.height)>640:pix.shrink(1)
     data=pix.tobytes('png')
    query=dict(image=data)
   else:
    wrong=bases[(bases.index(c['knowledge_base_id'])+1)%len(bases)];wrong_result=mm.search(wrong,image=Path(c['source_path']).read_bytes(),top_k=8);published=mm.search(c['knowledge_base_id'],text=c['question'],top_k=8,review_mode='published_only');passed=not any(x['document_id']==c['document_id'] for x in wrong_result['items']) and all(x['knowledge_base_id']==wrong for x in wrong_result['items']) and not published['items'];entry={'case_id':c['id']+'_'+kind,'passed':passed,'wrong_scope':wrong,'wrong_scope_result':wrong_result,'published_result':published,'elapsed_ms':round((time.monotonic()-start)*1000,2)};results.append(entry);print(entry['case_id'],passed,flush=True);(out/'results.partial.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));continue
   result=mm.search(c['knowledge_base_id'],top_k=8,**query);ids=[x['document_id'] for x in result['items']];rank=ids.index(c['document_id'])+1 if c['document_id'] in ids else None;entry={'case_id':c['id']+'_'+kind,'passed':rank is not None,'expected_document_id':c['document_id'],'rank_at_8':rank,'elapsed_ms':round((time.monotonic()-start)*1000,2),'result':result};results.append(entry);print(entry['case_id'],rank,flush=True)
   (out/'results.partial.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 from collections import Counter
 summary={'contracts':len(results),'passed':sum(x['passed'] for x in results),'by_kind':{k:{'total':sum(x['case_id'].endswith('_'+k) for x in results),'passed':sum(x['case_id'].endswith('_'+k) and x['passed'] for x in results)} for k in ['text','new_image','scope']},'source_count':len(xs),'case_sha256':digest(cases.read_bytes()),'first_independent_run':not a.development_regression,'development_regression':a.development_regression,'resumed_without_repeating_completed_contracts':a.resume,'query_adapter_manifest_sha256':digest(a.query_inputs.read_bytes()) if a.query_inputs else None,'model_fingerprint':fp,'corpus_scope':'in_corpus_development_regression' if a.development_regression else 'in_corpus_holdout_expected_sources_excluded_from_round5','human_approved':False,'training_allowed':False,'results':results}
 (out/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
