"""Read-only body/revision and source-byte checks, separate from derived publication."""
import argparse,json,sqlite3,hashlib,time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--before',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise FileExistsError('Preserve prior integrity evidence')
 with sqlite3.connect(a.before.resolve().as_uri()+'?mode=ro',uri=True) as before,sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
  db.execute('BEGIN');result={'sqlite_integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'tables':{},'source_bytes':0,'source_groups':0,'source_errors':[],'missing_original_metadata':[]}
  for table,key in [('documents','document_id'),('chunks','chunk_id'),('sections','section_id'),('revisions','revision_id')]:
   old=before.execute(f'SELECT * FROM {table} ORDER BY {key}').fetchall();new=db.execute(f'SELECT * FROM {table} ORDER BY {key}').fetchall();result['tables'][table]={'count':len(new),'unchanged':old==new}
  groups={}
  for identity,payload in db.execute('SELECT document_id,payload FROM documents WHERE deleted=0'):
   value=json.loads(payload);path=value.get('local_path');sha=value.get('source_sha256')
   if not path or not sha:result['missing_original_metadata'].append(identity);continue
   groups.setdefault((path,sha),[]).append(identity)
  for (path,sha),identities in groups.items():
   data=Path(path).read_bytes();result['source_bytes']+=len(data);result['source_groups']+=1
   if hashlib.sha256(data).hexdigest()!=sha:result['source_errors'].append(identities)
  result['whole_document_reviews']=db.execute('SELECT count(*) FROM knowledge_reviews').fetchone()[0];result['whole_document_review_events']=db.execute('SELECT count(*) FROM knowledge_review_events').fetchone()[0]
 result['passed']=result['sqlite_integrity']=='ok' and all(v['unchanged'] for v in result['tables'].values()) and not result['source_errors'];a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
