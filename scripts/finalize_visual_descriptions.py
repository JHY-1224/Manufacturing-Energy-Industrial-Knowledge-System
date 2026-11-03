"""Screen a complete frozen visual corpus, preserving raw model outputs and review scope."""
import argparse,json,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest,KnowledgeConflictError
from app.storage.task_store import TaskStore
from app.knowledge.multimodal import LocalVisionClient
from app.knowledge.visual_screening import screen

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--wait',action='store_true');p.add_argument('--vision-url',default='http://127.0.0.1:8083');a=p.parse_args();r=a.root
 if (r/'visual-full-screening.json').exists():raise FileExistsError('Preserve completed screening evidence')
 store=KnowledgeStore(r/'snapshot');tasks=TaskStore(store);identity=json.loads((r/'visual-all-task.json').read_text())['task_id']
 while True:
  task=tasks.get(identity)
  if task['status']=='completed':break
  if task['status'] not in {'queued','running'} or not a.wait:raise ValueError('Visual task is not complete: '+str(task['status']))
  print('waiting on frozen visual task',task['counts'],flush=True);time.sleep(20)
 health=LocalVisionClient(a.vision_url).health();fp=health['vision_fingerprint']
 if fp!=task['config']['multimodal_fingerprint']:raise ValueError('Final screening model changed')
 with store.connection() as db:
  expected=db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.image_sha256') IS NOT NULL").fetchone()[0]
  rows=db.execute("SELECT d.payload,a.payload FROM documents d JOIN multimodal_assets a ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE d.deleted=0 AND a.kind='observation' AND a.fingerprint=?",(fp,)).fetchall()
 if len(rows)!=expected:raise ValueError(f'Incomplete current visual coverage {len(rows)}/{expected}')
 records=[];pending=[];flags=Counter();bases=Counter();quarantined=0;preprocessing=Counter()
 for row in rows:
  d,value=(json.loads(v) for v in row);raw=value.get('raw_text','');ob=value.get('observation') or {}
  if not raw.strip() or not ob.get('visible_description') or not value.get('model_input_sha256') or not ob.get('unknowns') or value.get('parse_error'):raise ValueError('Incomplete visual record: '+d['document_id'])
  old=store.encode(value);value=screen(value,d);value['ai_screening']['raw_output_sha256']=digest(raw.encode());flag=value['ai_screening']['flags'];flags.update(flag);bases.update([d['knowledge_base_id']]);preprocessing.update([value.get('input_preprocessing')]);quarantined+=not value['ai_screening']['retrieval_allowed'];pending.append((d,old,value))
  records.append({'document_id':d['document_id'],'revision_id':d['revision_id'],'source_sha256':d['source_sha256'],'model_input_sha256':value['model_input_sha256'],'flags':flag,'retrieval_allowed':value['ai_screening']['retrieval_allowed'],'raw_output_sha256':digest(raw.encode())})
 for offset in range(0,len(pending),32):
  with store.connection() as db:
   db.execute('BEGIN IMMEDIATE')
   for d,old,value in pending[offset:offset+32]:
    store._check_version(db,d['document_id'],d['write_version']);changed=db.execute("UPDATE multimodal_assets SET payload=? WHERE document_id=? AND revision_id=? AND fingerprint=? AND kind='observation' AND source_sha256=? AND payload=?",(store.encode(value),d['document_id'],d['revision_id'],fp,d['source_sha256'],old)).rowcount
    if changed!=1:raise KnowledgeConflictError('Visual screening input changed')
 result={'expected_images':expected,'described_and_source_screened':len(records),'model_fingerprint':fp,'by_base':dict(bases),'flags':dict(flags),'retrieval_quarantined':quarantined,'input_preprocessing':dict(preprocessing),'scope':'source identity, schema, unknowns and explicit claim boundaries; not exhaustive visual factual certification','original_model_outputs_preserved':True,'whole_document_published':False,'human_approved':False,'training_allowed':False,'records':records}
 output=r/'visual-full-screening.json'
 if output.exists():raise FileExistsError('Preserve the completed screening receipt')
 output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
