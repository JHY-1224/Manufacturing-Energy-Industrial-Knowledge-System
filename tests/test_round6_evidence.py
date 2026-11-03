import json,tempfile,unittest
from pathlib import Path
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError,digest
from app.knowledge.structure_resolution import StructureResolutions
from app.knowledge.visual_screening import screen
from app.preprocessors.grid_recovery import bounded_grid
from app.core.models import RagChunk,RetrievalHit
from app.retrievers.table_retriever import TableRetriever
from tests import test_round5_multimodal as fixture
import fitz

class BatchClient(fixture.Client):
 def __init__(self):super().__init__();self.inputs=[];self.reverse=False
 def describe_many(self,images):
  self.inputs+=images
  result=[{'fingerprint':'vision-fixture','source_sha256':digest(b),'observation':{'visible_description':b.decode(),'regions':[],'unknowns':['unknown']}} for b in images]
  return result[::-1] if self.reverse else result
class VisualBatchTests(unittest.TestCase):
 build=fixture.MultimodalTests.build
 def setUp(self):
  fixture.MultimodalTests.setUp(self);self.client=BatchClient();self.mm.client=self.client
 def items(self):return [dict(key=i,document_id=i,expected_write_version=self.store.get_document(i)['write_version']) for i in ('a','b')]
 def test_batch_mapping_and_cache(self):
  r=self.mm.process_many(self.items(),'vision-fixture');self.assertEqual([r[i]['observation']['visible_description'] for i in ('a','b')],['a','b']);self.assertEqual(self.client.inputs,[b'a',b'b'])
  self.assertTrue(all(v['cache_hit'] for v in self.mm.process_many(self.items(),'vision-fixture').values()));self.assertEqual(len(self.client.inputs),2)
 def test_one_bad_source_cannot_shift_batch(self):
  Path(self.store.get_document('a')['local_path']).write_bytes(b'changed')
  r=self.mm.process_many(self.items(),'vision-fixture');self.assertIsInstance(r['a'],Exception);self.assertEqual(r['b']['observation']['visible_description'],'b');self.assertEqual(self.client.inputs,[b'b'])
 def test_reversed_batch_digest_is_rejected(self):
  self.client.reverse=True;r=self.mm.process_many(self.items(),'vision-fixture');self.assertTrue(all(isinstance(x,KnowledgeConflictError) for x in r.values()))
  with self.store.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM multimodal_assets WHERE kind='observation'").fetchone()[0],0)
 def test_trace_keeps_new_image_digest_without_image_bytes(self):
  self.build('a');value=self.mm.search('automotive',image=b'private temporary query bytes');trace=self.store.get_trace(value['trace_id']);self.assertEqual(trace['runtime']['query_image_sha256'],digest(b'private temporary query bytes'));self.assertNotIn('private temporary query bytes',self.store.encode(trace));self.assertTrue(trace['runtime']['evidence_revision_checked']);self.assertEqual(len(trace['reranked_docs']),1)
 def test_failed_search_trace_is_persisted(self):
  self.build('a');self.client.hook=lambda: (_ for _ in ()).throw(ValueError('fixture unavailable'))
  with self.assertRaises(ValueError):self.mm.search('automotive',image=b'query')
  with self.store.connection() as db:v=json.loads(db.execute('SELECT payload FROM traces ORDER BY rowid DESC LIMIT 1').fetchone()[0])
  self.assertEqual(v['runtime']['status'],'failed');self.assertFalse(v['runtime']['uploaded_image_persisted'])
 def test_screening_never_promotes_causal_text(self):
  d=self.store.get_document('a');v=self.mm.process('a',d['write_version'],'observation');v['observation']['visible_description']='压力为10 MPa导致缺陷';s=screen(v,d);self.assertFalse(s['ai_screening']['retrieval_allowed']);self.assertFalse(s['human_approved']);self.assertFalse(s['ai_screening']['all_visible_claims_independently_verified'])
 def test_description_scope_revision_and_retrieval_gate(self):
  self.build('a');self.build('b')
  for i in ('a','b'):
   d=self.store.get_document(i);v=self.mm.process(i,d['write_version'],'observation');v['observation']['visible_description']='orange mechanical arm';v=screen(v,d)
   with self.store.connection() as db:db.execute("UPDATE multimodal_assets SET payload=? WHERE document_id=? AND kind='observation'",(self.store.encode(v),i))
  r=self.mm.search('automotive',text='orange mechanical arm');self.assertEqual(r['description_channel']['available'],1);self.assertTrue(all(v['document_id']=='a' for v in r['items']));self.assertTrue(any(v['channel']=='visual_description_text' for v in r['items'][0]['matches']))
  d=self.store.get_document('a');d['revision_id']='new'
  with self.store.connection() as db:db.execute("UPDATE documents SET payload=? WHERE document_id='a'",(self.store.encode(d),))
  self.assertEqual(self.mm.search('automotive',text='arm')['items'],[])

class ReviewedVisualLabelsTests(unittest.TestCase):
 build=fixture.MultimodalTests.build
 def setUp(self):
  fixture.MultimodalTests.setUp(self)
  from app.storage.evidence_review_store import EvidenceReviewStore
  self.reviews=EvidenceReviewStore(self.store)
  for i in ('a','b'):
   self.build(i);d=self.store.get_document(i);v=self.mm.process(i,d['write_version'],'observation');v['observation']['visible_description']='orange mechanical arm';v=screen(v,d)
   with self.store.connection() as db:db.execute("UPDATE multimodal_assets SET payload=? WHERE document_id=? AND kind='observation'",(self.store.encode(v),i))
 def label(self,identity='a',decision='accepted',**extra):
  d=self.store.get_document(identity)
  return self.reviews.append(identity,d['revision_id'],decision,'visual_source_labels','Direct source-image fixture review',{'fingerprint':'vision-fixture','source_sha256':d['source_sha256'],'source_inspected':True,'source_visible_description':'orange mechanical arm',**extra})
 def test_reviewed_label_replaces_weak_sentence_and_trace_binds_actual_review(self):
  review=self.label();self.label('b');result=self.mm.search('automotive',text='orange mechanical arm')
  self.assertEqual(result['description_channel']['source_reviewed_available'],1);matches=result['items'][0]['matches'];matched=next(v for v in matches if v['channel']=='source_reviewed_visual_text');self.assertEqual(matched['evidence_review_id'],review['review_id']);self.assertEqual(matched['evidence_text'],'orange mechanical arm');self.assertFalse(any(v['channel']=='visual_description_text' for v in matches));trace=self.store.get_trace(result['trace_id']);self.assertEqual(trace['reranked_docs'][0]['metadata']['multimodal_matches'],matches)
  disabled=self.mm.search('automotive',text='orange mechanical arm',description_retrieval=False);self.assertEqual(disabled['description_channel']['source_reviewed_available'],0)
 def test_uninspected_stale_quarantined_and_retracted_labels_cannot_contribute(self):
  for evidence in [{'source_sha256':'stale'},{'source_inspected':False},{'source_visible_description':'10 MPa导致故障'}]:
   self.label(**evidence);self.assertEqual(self.mm.search('automotive',text='orange mechanical arm')['description_channel']['source_reviewed_available'],0)
  self.label();self.assertEqual(self.mm.search('automotive',text='orange mechanical arm')['description_channel']['source_reviewed_available'],1)
  self.label(decision='rejected');self.assertEqual(self.mm.search('automotive',text='orange mechanical arm')['description_channel']['source_reviewed_available'],0)
 def test_label_withdrawal_during_model_call_rejects_stale_result(self):
  self.label();self.client.hook=lambda:self.label(decision='rejected')
  with self.assertRaises(KnowledgeConflictError):self.mm.search('automotive',text='orange mechanical arm')
 def test_long_layout_query_keeps_rare_object_labels_without_generic_single_word_promotion(self):
  self.label(source_visible_description='紫色平台上方有橙色机械臂')
  result=self.mm.search('automotive',text='寻找一张带有许多装置和复杂背景的图片，平台周围还有其他组件，其中上方的橙色机械臂清楚可见')
  self.assertTrue(any(m['channel']=='source_reviewed_visual_text' for m in result['items'][0]['matches']))
  generic=self.mm.search('automotive',text='平台')
  self.assertFalse(any(m['channel']=='source_reviewed_visual_text' for m in generic['items'][0]['matches']))
  self.label(source_visible_description='工艺流程示意图中的设备与系统')
  generic=self.mm.search('automotive',text='工艺流程图设备系统')
  self.assertFalse(any(m['channel']=='source_reviewed_visual_text' for m in generic['items'][0]['matches']))

class StructureResolutionTests(unittest.TestCase):
 def setUp(self):
  self.store=KnowledgeStore(tempfile.mkdtemp());self.service=StructureResolutions(self.store);self.did='d';self.source=self.store.asset_dir/'source.pdf'
  with fitz.open() as pdf:
   p=pdf.new_page(width=300,height=300);p.insert_text((30,90),'A');p.insert_text((180,90),'42');p.insert_text((30,145),'B');p.insert_text((180,145),'7');pdf.save(self.source)
  self.doc={'document_id':'d','title':'fixture','source_name':'source.pdf','local_path':str(self.source),'revision_id':'r','source_sha256':digest(self.source.read_bytes()),'knowledge_base_id':'automotive','status':'indexed'}
  self.store.register('fixture',self.doc)
 def resolution(self,disposition='graphic_reference'):
  return {'candidate_id':'figure1','document_id':'d','revision_id':'r','source_sha256':self.doc['source_sha256'],'source_table_id':'t','page':1,'disposition':disposition,'note':'Explicit AI source disposition','source_inspected':True,'original_candidate':{'old':'retained'}}
 def chunk(self):return RagChunk(chunk_id='c',doc_id='d',content='original table labels',metadata={'revision_id':'r','page':1,'knowledge_base_id':'automotive','table_id':'t'})
 def test_graphic_disposition_is_current_only_and_not_table(self):
  self.service.put(self.resolution());self.assertTrue(self.service.preview('d','figure1').startswith(b'\x89PNG'));self.assertEqual(TableRetriever(self.store).search('table', [self.chunk()]),[])
  hit=RetrievalHit(**self.chunk().model_dump(),score=1);self.service.guard_hits([hit]);self.assertIn('不得',hit.content);self.assertTrue(hit.metadata['table_recall_disabled'])
  with self.store.connection() as db:
   d=self.store.get_document('d');d['revision_id']='r2';db.execute("UPDATE documents SET payload=? WHERE document_id='d'",(self.store.encode(d),))
  self.assertEqual(self.service.current('d'),[])
  with self.assertRaises(FileNotFoundError):self.service.preview('d','figure1')
 def test_grid_recovery_missing_values_stay_blank_and_positions_retained(self):
  with fitz.open(self.source) as pdf:t=bounded_grid(pdf[0],'t',[20,130,260],[60,110,165],['Name','Value'])
  v=self.resolution('recovered_table');v['table']=t;self.service.put(v);c=self.service.table_chunks([self.chunk()])[0];self.assertIn('42',c.content);self.assertEqual(c.metadata['source_chunk_id'],'c');self.assertFalse(t['blank_cells_filled']);self.assertEqual(t['cells'][-1]['locator']['page'],1)
  hits=TableRetriever(self.store).search('table A', [self.chunk()]);self.assertEqual(len(hits),1);merged=TableRetriever().merge([RetrievalHit(**self.chunk().model_dump(),score=1)],hits,3);self.assertIn('42',merged[0].content);self.assertTrue(merged[0].metadata['derived_structure'])
 def test_stale_duplicate_and_changed_original_are_rejected(self):
  v=self.resolution();v['revision_id']='old'
  with self.assertRaises(KnowledgeConflictError):self.service.put(v)
  self.service.put(self.resolution())
  with self.assertRaises(KnowledgeConflictError):self.service.put(self.resolution())
  self.source.write_bytes(b'changed')
  with self.assertRaises(KnowledgeConflictError):self.service.preview('d','figure1')
 def test_invalid_grid_and_ai_screen_source_guard(self):
  with fitz.open(self.source) as pdf:
   with self.assertRaises(ValueError):bounded_grid(pdf[0],'t',[0,200,100],[0,20,30])
  with self.assertRaises(ValueError):screen({'source_sha256':'old'},self.doc)

class VisualFrameTests(unittest.TestCase):
 def setUp(self):
  fixture.MultimodalTests.setUp(self)
  from app.knowledge.visual_frames import VisualFrames
  self.frames=VisualFrames(self.store);self.document=self.store.get_document('a');self.path=self.store.directory/'visual-frame-inputs'/'frame0.png';self.path.parent.mkdir();self.path.write_bytes(b'frame0')
  self.parent={**self.document,'frame_count':3,'decodable_frame_count':2,'sampling':'first_middle_last_decodable_v1','decode_failures':[{'frame_index':2,'error':'truncated'}]}
  self.frame={'path':str(self.path),'sha256':digest(b'frame0'),'frame_index':0,'timestamp_ms':0,'duration_ms':100}
 def test_first_frame_reuse_and_source_bound_preview(self):
  v=self.mm.process('a',self.document['write_version'],'observation');result=self.frames.publish(self.parent,self.frame,v,'vision-fixture',True);self.assertFalse(result['all_frames_semantically_observed']);self.assertEqual(result['source_decode_failures'][0]['frame_index'],2);self.assertEqual(self.frames.preview('a',0,'vision-fixture').read_bytes(),b'frame0')
  self.path.write_bytes(b'changed')
  with self.assertRaises(ValueError):self.frames.preview('a',0,'vision-fixture')
 def test_inference_digest_and_model_identity_guards(self):
  result={'fingerprint':'vision-fixture','source_sha256':'wrong','observation':{'visible_description':'A fixture frame','unknowns':['unknown'],'regions':[]}}
  with self.assertRaises(ValueError):self.frames.publish(self.parent,self.frame,result,'vision-fixture')
  result['source_sha256']=digest(b'frame0');v=self.frames.publish(self.parent,self.frame,result,'vision-fixture');self.assertEqual(v['frame_input_sha256'],digest(b'frame0'))
  result['fingerprint']='other-model'
  with self.assertRaises(ValueError):self.frames.publish(self.parent,self.frame,result,'vision-fixture')
 def test_current_revision_removes_old_samples(self):
  v=self.mm.process('a',self.document['write_version'],'observation');self.frames.publish(self.parent,self.frame,v,'vision-fixture',True)
  d=self.store.get_document('a');d['revision_id']='new'
  with self.store.connection() as db:db.execute("UPDATE documents SET payload=? WHERE document_id='a'",(self.store.encode(d),))
  self.assertEqual(self.frames.current('a'),[])
  with self.assertRaises(KnowledgeConflictError):self.frames.publish(self.parent,self.frame,v,'vision-fixture',True)

class VisualCircuitBreakerTests(unittest.TestCase):
 def test_service_failure_stops_after_current_batch_and_retry_resumes(self):
  from app.tasks.service import BackgroundTasks
  from unittest.mock import patch
  store=KnowledgeStore(tempfile.mkdtemp());worker=BackgroundTasks(store);items=[]
  for i in range(10):
   store.register('fixture:'+str(i),{'document_id':str(i),'title':'fixture','knowledge_base_id':'automotive','source_sha256':'fixture-sha','revision_id':'fixture-rev'})
   d=store.get_document(str(i));items.append({'key':str(i),'document_id':str(i),'expected_write_version':d['write_version'],'source_sha256':d['source_sha256']})
  job=worker.tasks.create('vision_describe',{'multimodal_fingerprint':'fixture'},items);claimed=worker.tasks.claim(worker.token)
  with patch('app.knowledge.multimodal.MultimodalService.process_many',side_effect=ValueError('service unavailable')):worker._run(claimed)
  failed=worker.tasks.get(job['task_id']);self.assertEqual(failed['status'],'failed');self.assertEqual(failed['counts'],{'failed':4,'pending':6})
  worker.tasks.control(job['task_id'],'retry');claimed=worker.tasks.claim(worker.token)
  def success(items,fp):return {i['key']:{'cache_hit':False,'model_call':True,'revision_id':'fixture-rev','fingerprint':'fixture','review_status':'model_observation_pending_ai_review'} for i in items}
  with patch('app.knowledge.multimodal.MultimodalService.process_many',side_effect=success):worker._run(claimed)
  self.assertEqual(worker.tasks.get(job['task_id'])['counts'],{'completed':10})
 def test_operational_mlx_limits_are_bounded(self):
  from scripts.serve_local_vision_limited import configure_limits
  from unittest.mock import Mock
  mx=Mock();limits=configure_limits(mx);mx.set_memory_limit.assert_called_once_with(10*1024**3);self.assertEqual(limits['cache_limit_bytes'],128*1024**2)
  with self.assertRaises(ValueError):configure_limits(mx,100)

class LocalVisionTransportTests(unittest.TestCase):
 def test_redirect_cannot_forward_private_query_to_another_endpoint(self):
  from http.server import HTTPServer,BaseHTTPRequestHandler
  import threading
  from app.knowledge.multimodal import LocalVisionClient
  received=[]
  class Target(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):received.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'{}')
  target=HTTPServer(('127.0.0.1',0),Target);threading.Thread(target=target.serve_forever,daemon=True).start()
  class Redirect(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    self.rfile.read(int(self.headers['Content-Length']));self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{target.server_port}/private-forward');self.end_headers()
  redirect=HTTPServer(('127.0.0.1',0),Redirect);threading.Thread(target=redirect.serve_forever,daemon=True).start()
  try:
   from app.runtime import RuntimeAdapterError
   with self.assertRaises(RuntimeAdapterError) as caught:LocalVisionClient(f'http://127.0.0.1:{redirect.server_port}').call('/describe',{'image_base64':'synthetic-private-query'})
   self.assertEqual(caught.exception.code,'vision_redirect_refused')
   self.assertEqual(received,[])
  finally:redirect.shutdown();target.shutdown();redirect.server_close();target.server_close()
