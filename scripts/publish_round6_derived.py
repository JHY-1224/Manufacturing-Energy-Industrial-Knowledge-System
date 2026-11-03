"""Publish only verified derived evidence; never replace documents, chunks or originals."""
import argparse,json,sqlite3,sys,shutil
from uuid import uuid4
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest
from app.knowledge.multimodal import MultimodalService,LocalVisionClient
from app.knowledge.structure_resolution import StructureResolutions
from app.knowledge.visual_frames import VisualFrames
from app.storage.evidence_review_store import EvidenceReviewStore

def insert_checked(db,table,row):
 if table not in {'multimodal_assets','structure_resolutions','multimodal_frame_observations','evidence_reviews','background_tasks','background_items','background_events'}:raise ValueError('Unexpected derived table')
 schema=db.execute(f'PRAGMA table_info({table})').fetchall();keys=sorted(((int(c[5]),i,c[1]) for i,c in enumerate(schema) if c[5]))
 if not keys:raise ValueError('Derived table needs an explicit primary key')
 where=' AND '.join('"'+name.replace('"','""')+'"=?' for _,_,name in keys)
 old=db.execute(f'SELECT * FROM {table} WHERE '+where,[row[i] for _,i,_ in keys]).fetchone()
 if old is not None:
  if tuple(old)!=tuple(row):raise ValueError('Existing derived evidence conflicts; original row preserved: '+table)
  return 0
 return db.execute(f'INSERT INTO {table} VALUES ('+','.join('?' for _ in row)+')',row).rowcount

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();r=a.root.resolve();output=r/'derived-publication.json'
 if output.exists():raise FileExistsError('Publication receipt already exists; verify it instead of repeating writes')
 screen=json.loads((r/'visual-full-screening.json').read_text());fp=screen['model_fingerprint'];task_id=json.loads((r/'visual-all-task.json').read_text())['task_id'];src=KnowledgeStore(r/'snapshot');main=KnowledgeStore(Path.cwd()/'data/runtime');health=LocalVisionClient().health()
 if health['vision_fingerprint']!=fp:raise ValueError('Publication model identity changed')
 MultimodalService(main);StructureResolutions(main);VisualFrames(main);EvidenceReviewStore(main)
 backup=r/('pre-derived-publication-'+uuid4().hex+'.sqlite3')
 if backup.exists():raise FileExistsError('Preserve the previous independent publication backup')
 with sqlite3.connect(main.path) as source,sqlite3.connect(backup) as target:source.backup(target)
 staged=[]
 def asset(relative,expected,source_path=None):
  source=(Path(source_path) if source_path else src.directory/relative).resolve();target=(main.directory/relative).resolve()
  if not source.is_relative_to(r) or not target.is_relative_to(main.directory) or relative.startswith('originals/'):raise ValueError('Derived asset outside allowed roots')
  data=source.read_bytes()
  if digest(data)!=expected:raise ValueError('Derived asset checksum changed')
  if target.exists():
   if digest(target.read_bytes())!=expected:raise ValueError('Existing derived asset conflicts')
  else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
  staged.append({'relative_path':relative,'sha256':expected,'bytes':len(data)})
 with src.connection() as db:
  task=db.execute('SELECT * FROM background_tasks WHERE task_id=?',(task_id,)).fetchone()
  if not task or task['status']!='completed':raise ValueError('Only a completed visual task can be published')
  observations=[tuple(row) for row in db.execute("SELECT * FROM multimodal_assets WHERE kind='observation' AND fingerprint=?",(fp,))]
  structures=[tuple(row) for row in db.execute('SELECT * FROM structure_resolutions')];frames=[tuple(row) for row in db.execute('SELECT * FROM multimodal_frame_observations WHERE fingerprint=?',(fp,))];reviews=[tuple(row) for row in db.execute('SELECT * FROM evidence_reviews')];items=[tuple(row) for row in db.execute('SELECT * FROM background_items WHERE task_id=?',(task_id,))];events=[tuple(row) for row in db.execute('SELECT * FROM background_events WHERE task_id=?',(task_id,))]
 if len(observations)!=screen['expected_images'] or screen['described_and_source_screened']!=len(observations):raise ValueError('Visual coverage receipt changed')
 frozen={v['document_id']:v for v in screen['records']}
 for row in observations:
  v=json.loads(row[-1]);record=frozen[v['document_id']]
  if not v.get('ai_screening',{}).get('source_revision_verified') or record['raw_output_sha256']!=digest(v['raw_text'].encode()):raise ValueError('Observation changed after screening')
 for row in structures:
  value=json.loads(row[-1]);graphic=value.get('graphic_asset')
  if graphic:asset(graphic['relative_path'],graphic['sha256'])
 normalized_frames=[]
 for row in frames:
  value=json.loads(row[-1]);asset(value['frame_asset'],value['frame_input_sha256']);recovered=value.get('source_recovery')
  if recovered:
   relative='visual-source-recoveries/'+recovered['recovered_sha256']+'.gif';asset(relative,recovered['recovered_sha256']);value['recovery_asset']=relative
  normalized_frames.append((*row[:-1],main.encode(value)))
 frames=normalized_frames;counts={}
 with main.connection() as db:existing_review_ids={row[0] for row in db.execute('SELECT review_id FROM evidence_reviews')}
 reviews=[row for row in reviews if row[0] not in existing_review_ids]
 with main.connection() as db:
  db.execute('BEGIN IMMEDIATE')
  # Revalidate every referenced revision and source, not the unrelated global generation.
  docs={row['document_id']:json.loads(row['payload']) for row in db.execute('SELECT document_id,payload FROM documents WHERE deleted=0')}
  for table,rows in [('multimodal_assets',observations),('structure_resolutions',structures),('multimodal_frame_observations',frames),('evidence_reviews',reviews)]:
   added=0
   for row in rows:
    value=json.loads(row[-1]);d=docs.get(value['document_id']);source=value.get('source_sha256') or value.get('evidence',{}).get('source_sha256')
    if not d or d.get('revision_id')!=value['revision_id'] or source and d.get('source_sha256')!=source:raise ValueError('Current source/revision changed before publication')
    added+=insert_checked(db,table,row)
   counts[table]=added
  for table,rows in [('background_tasks',[tuple(task)]),('background_items',items),('background_events',events)]:
   added=0
   for row in rows:added+=insert_checked(db,table,row)
   counts[table]=added
  main.bump(db)
  integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
  if integrity!='ok':raise ValueError('Main database integrity check failed')
 result={'published':True,'only_derived_evidence':True,'counts':counts,'visual_model_fingerprint':fp,'staged_assets':list({x['relative_path']:x for x in staged}.values()),'independent_database_backup':str(backup),'main_database_replaced':False,'documents_and_chunks_modified':False,'originals_replaced':False,'whole_documents_published':False,'human_approved':False,'training_performed':False,'sqlite_integrity':integrity}
 output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='staged_assets'},ensure_ascii=False))
if __name__=='__main__':main()
