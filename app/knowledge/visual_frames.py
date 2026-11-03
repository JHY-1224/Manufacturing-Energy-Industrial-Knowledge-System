"""Source-bound semantic samples from animations; never invent undecodable frames."""
import json
from pathlib import Path
from app.storage.knowledge_store import digest,now,KnowledgeConflictError
from app.knowledge.visual_screening import screen

class VisualFrames:
 def __init__(self,store):
  self.store=store
  with store.connection() as db:db.execute('CREATE TABLE IF NOT EXISTS multimodal_frame_observations (document_id TEXT NOT NULL,revision_id TEXT NOT NULL,fingerprint TEXT NOT NULL,frame_index INTEGER NOT NULL,source_sha256 TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(document_id,revision_id,fingerprint,frame_index))')
 def current(self,document_id,fingerprint=None):
  with self.store.connection() as db:
   rows=db.execute("SELECT f.payload FROM multimodal_frame_observations f JOIN documents d ON d.document_id=f.document_id AND f.revision_id=json_extract(d.payload,'$.revision_id') AND f.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE d.deleted=0 AND f.document_id=?"+(' AND f.fingerprint=?' if fingerprint else '')+' ORDER BY f.frame_index',[document_id]+([fingerprint] if fingerprint else [])).fetchall()
  return [json.loads(r[0]) for r in rows]
 def publish(self,parent,frame,result,fingerprint,reused_parent=False):
  document=self.store.get_document(parent['document_id'])
  if not document or (document.get('revision_id'),document.get('source_sha256'))!=(parent['revision_id'],parent['source_sha256']):raise KnowledgeConflictError('Animation source changed')
  path=Path(frame['path']).resolve();root=(self.store.directory/'visual-frame-inputs').resolve()
  if not path.is_relative_to(root) or digest(path.read_bytes())!=frame['sha256']:raise ValueError('Frame source checksum changed')
  if result.get('fingerprint')!=fingerprint or not (result.get('observation') or {}).get('visible_description') or result.get('parse_error'):raise ValueError('Invalid frame model observation')
  if reused_parent and result.get('source_sha256')!=document['source_sha256']:raise ValueError('Reused first frame source changed')
  if not reused_parent and (result.get('source_sha256')!=frame['sha256'] or result.get('fingerprint')!=fingerprint):raise ValueError('Frame inference input changed')
  value={**result,'document_id':document['document_id'],'revision_id':document['revision_id'],'source_sha256':document['source_sha256'],'fingerprint':fingerprint,'frame_index':frame['frame_index'],'frame_count':parent['frame_count'],'decodable_frame_count':parent['decodable_frame_count'],'frame_input_sha256':frame['sha256'],'frame_asset':str(path.relative_to(self.store.directory)),'sampling':parent['sampling'],'all_frames_semantically_observed':False,'source_decode_failures':parent['decode_failures'],'source_recovery':parent.get('source_recovery'),'timestamp_ms':frame['timestamp_ms'],'duration_ms':frame['duration_ms'],'reused_parent_first_frame':reused_parent,'created_at':now(),'human_approved':False}
  value=screen(value,document)
  with self.store.connection() as db:
   db.execute('BEGIN IMMEDIATE');self.store._check_version(db,document['document_id'],document['write_version']);key=(document['document_id'],document['revision_id'],fingerprint,frame['frame_index']);old=db.execute('SELECT payload FROM multimodal_frame_observations WHERE document_id=? AND revision_id=? AND fingerprint=? AND frame_index=?',key).fetchone()
   if old:
    previous=json.loads(old[0])
    if previous['frame_input_sha256']!=frame['sha256']:raise KnowledgeConflictError('Existing frame identity changed')
    return previous
   db.execute('INSERT INTO multimodal_frame_observations VALUES (?,?,?,?,?,?)',(*key,document['source_sha256'],self.store.encode(value)))
  return value
 def preview(self,document_id,frame_index,fingerprint):
  value=next((v for v in self.current(document_id,fingerprint) if v['frame_index']==frame_index),None)
  if not value:raise FileNotFoundError('Current frame not found')
  path=(self.store.directory/value['frame_asset']).resolve()
  if not path.is_relative_to((self.store.directory/'visual-frame-inputs').resolve()) or digest(path.read_bytes())!=value['frame_input_sha256']:raise ValueError('Frame asset changed')
  return path
