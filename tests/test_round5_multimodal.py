import json,tempfile,unittest
from pathlib import Path
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError,digest
from app.knowledge.multimodal import MultimodalService,LocalVisionClient
from app.core.engine import ConfigurableRagEngine
from app.core.models import IngestRequest

class Client:
 def __init__(self):self.calls=0;self.hook=None
 def health(self):return {'embedding_fingerprint':'fixture','vision_fingerprint':'vision-fixture'}
 def encode(self,**kwargs):
  self.calls+=1
  if self.hook:self.hook()
  return {'vector':[1.,0.],'dimension':2,'fingerprint':'fixture'}
 def describe(self,image):return {'fingerprint':'vision-fixture','observation':{'visible_description':'fixture','regions':[],'unknowns':['fixture unknown']}}
class MultimodalTests(unittest.TestCase):
 def setUp(self):
  self.store=KnowledgeStore(tempfile.mkdtemp());engine=ConfigurableRagEngine(self.store)
  for identity,base in [('a','automotive'),('b','cosmetics_manufacturing')]:
   source=self.store.directory/(identity+'.txt');source.write_text(identity+' fixture visible image caption')
   self.store.register('fixture:'+identity,{'document_id':identity,'title':identity,'source_name':identity+'.txt','local_path':str(source),'knowledge_base_id':base,'status':'uploaded','chunk_count':0,'source_sha256':digest(source.read_bytes())})
   engine.ingest(IngestRequest(path=str(source),document_id=identity,knowledge_base_id=base))
  for identity in ('a','b'):
   path=self.store.asset_dir/(identity+'.png');path.write_bytes(identity.encode());d=self.store.get_document(identity);d.update(image_sha256=digest(path.read_bytes()),source_sha256=digest(path.read_bytes()),local_path=str(path))
   with self.store.connection() as db:db.execute('UPDATE documents SET payload=? WHERE document_id=?',(self.store.encode(d),identity))
  self.client=Client();self.mm=MultimodalService(self.store,self.client)
 def build(self,identity):return self.mm.process(identity,self.store.get_document(identity)['write_version'])
 def test_cache_is_revision_and_model_bound(self):
  self.assertFalse(self.build('a')['cache_hit']);self.assertTrue(self.build('a')['cache_hit']);self.assertEqual(self.client.calls,1)
  with self.assertRaises(KnowledgeConflictError):self.mm.process('a','stale')
 def test_scope_and_published_filter_before_model_call(self):
  self.build('a');self.build('b');self.client.calls=0
  r=self.mm.search('automotive',text='image');self.assertEqual([x['document_id'] for x in r['items']],['a'])
  self.assertEqual(self.mm.search('automotive',text='image',review_mode='published_only')['items'],[]);self.assertEqual(self.client.calls,1)
 def test_new_image_does_not_ingest(self):
  self.build('a');before=list(self.store.asset_dir.iterdir());r=self.mm.search('automotive',image=b'new query');self.assertFalse(r['uploaded_image_persisted']);self.assertEqual(before,list(self.store.asset_dir.iterdir()))
 def test_source_changed_during_query_rejected(self):
  self.build('a')
  def change():
   d=self.store.get_document('a');d['write_version']='new'
   with self.store.connection() as db:db.execute('UPDATE documents SET payload=? WHERE document_id=?',(self.store.encode(d),'a'))
  self.client.hook=change
  with self.assertRaises(KnowledgeConflictError):self.mm.search('automotive',text='image')
 def test_source_digest_and_size_guard(self):
  d=self.store.get_document('a');Path(d['local_path']).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.build('a')
  with self.assertRaises(ValueError):self.mm.search('automotive',image=b'x'*(8*1024*1024+1))
 def test_remote_service_is_rejected(self):
  for url in ['https://example.com','http://example.com','http://user:secret@localhost:8083','http://localhost/?redirect=bad']:
   with self.assertRaises(ValueError):LocalVisionClient(url)
 def test_observations_never_imply_approval(self):
  d=self.store.get_document('a');self.mm.process('a',d['write_version'],'observation');r=self.mm.observations('a');self.assertFalse(r['human_approved']);self.assertFalse(r['items'][0]['human_approved'])

 def test_cache_cannot_hide_changed_source_digest(self):
  self.build('a');d=self.store.get_document('a');d['source_sha256']='changed-source'
  with self.store.connection() as db:db.execute('UPDATE documents SET payload=? WHERE document_id=?',(self.store.encode(d),'a'))
  with self.assertRaises(KnowledgeConflictError):self.build('a')
 def test_weak_lexical_match_cannot_displace_strong_visual_result(self):
  self.build('a');self.build('b')
  # Add a second automotive image with strong text overlap but opposite visual vector.
  d=self.store.get_document('b');d['knowledge_base_id']='automotive'
  with self.store.connection() as db:
   db.execute('UPDATE documents SET payload=? WHERE document_id=?',(self.store.encode(d),'b'))
   row=db.execute("SELECT payload FROM multimodal_assets WHERE document_id='b'").fetchone();value=json.loads(row[0]);value['vector']=[-1.,0.];db.execute("UPDATE multimodal_assets SET payload=? WHERE document_id='b'",(self.store.encode(value),))
  result=self.mm.search('automotive',text='private fixture caption')
  self.assertEqual(result['items'][0]['document_id'],'a');self.assertEqual(result['fusion'],'rrf_k60_top30_lexical_weight035')
