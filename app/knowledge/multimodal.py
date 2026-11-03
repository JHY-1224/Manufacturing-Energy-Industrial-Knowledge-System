"""Local, revision-bound visual observations and cross-modal vectors.

Derived outputs never publish knowledge or replace original/OCR evidence.
"""
import base64,json,math,os,re
from time import perf_counter
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from app.storage.knowledge_store import digest,now,KnowledgeConflictError
from app.storage.review_store import available,ReviewStore,effective_state
from app.knowledge.catalog import validate_base

class _NoVisionRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

class LocalVisionClient:
 def __init__(self,url=None):
  self.url=(url or os.environ.get('RAG_VISION_URL','http://127.0.0.1:8083')).rstrip('/')
  u=urlparse(self.url)
  if u.scheme!='http' or u.hostname not in {'127.0.0.1','localhost','::1'} or u.username or u.password or u.query or u.fragment:raise ValueError('Vision service must be loopback-only')
  self.opener=build_opener(ProxyHandler({}),_NoVisionRedirect())
 def call(self,path,payload=None):
  req=Request(self.url+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
  try:
   with self.opener.open(req,timeout=240) as response:return json.load(response)
  except HTTPError as e:
   if e.code in {413,422}:raise ValueError('Local vision model rejected the image input') from e
   from app.runtime import RuntimeAdapterError
   raise RuntimeAdapterError('vision_redirect_refused' if 300<=e.code<400 else 'vision_http_error','vision',e.code) from e
  except Exception as e:
   from app.runtime import RuntimeAdapterError
   raise RuntimeAdapterError('vision_transport_error','vision') from e
 def health(self):return self.call('/health')
 def encode(self,*,text='',image=None):
  result=self.call('/encode',{'text':text,'image_base64':base64.b64encode(image).decode() if image else ''})
  vector=result.get('vector',[])
  if len(vector)!=result.get('dimension') or not vector or any(not math.isfinite(x) for x in vector):raise ValueError('Invalid multimodal vector')
  if not .99<sum(x*x for x in vector)<1.01:raise ValueError('Multimodal vector is not normalized')
  return result
 def describe(self,image):return self.call('/describe',{'image_base64':base64.b64encode(image).decode()})
 def describe_many(self,images):return self.call('/describe-batch',{'images':[base64.b64encode(x).decode() for x in images]})['items']

class MultimodalService:
 def __init__(self,store,client=None):
  self.store=store;self.client=client or LocalVisionClient();ReviewStore(store)
  with store.connection() as db:db.execute('CREATE TABLE IF NOT EXISTS multimodal_assets (document_id TEXT NOT NULL,revision_id TEXT NOT NULL,fingerprint TEXT NOT NULL,kind TEXT NOT NULL,source_sha256 TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(document_id,revision_id,fingerprint,kind))')
 def process(self,document_id,expected_write_version,kind='embedding',expected_fingerprint=None,_inference=None):
  if kind not in {'embedding','observation'}:raise ValueError('Unknown multimodal kind')
  document=self.store.get_document(document_id)
  if not document or document.get('write_version')!=expected_write_version:raise KnowledgeConflictError('Document changed')
  if not document.get('image_sha256'):raise ValueError('Source is not an image')
  health=self.client.health();fingerprint=health['embedding_fingerprint' if kind=='embedding' else 'vision_fingerprint']
  if expected_fingerprint and fingerprint!=expected_fingerprint:raise KnowledgeConflictError('Vision model changed')
  key=(document_id,document['revision_id'],fingerprint,kind)
  with self.store.connection() as db:old=db.execute('SELECT payload,source_sha256 FROM multimodal_assets WHERE document_id=? AND revision_id=? AND fingerprint=? AND kind=?',key).fetchone()
  if old:
   if old[1]!=document['source_sha256']:raise KnowledgeConflictError('Source changed without a new derived revision')
   return {'cache_hit':True,'model_call':False,**json.loads(old[0])}
  model_input,preprocessing=self.prepare_input(document)
  result=_inference(model_input) if _inference else self.client.encode(image=model_input) if kind=='embedding' else self.client.describe(model_input)
  if result.get('source_sha256') and result['source_sha256']!=digest(model_input):raise KnowledgeConflictError('Visual model input changed')
  result.update(model_input_sha256=digest(model_input),input_preprocessing=preprocessing)
  if result['fingerprint']!=fingerprint:raise ValueError('Vision model identity changed during inference')
  if kind=='observation' and (not result.get('observation') or result.get('parse_error')):
   self.store.save_asset(self.store.encode(result).encode(),'failed-vision-observation.json')
   raise ValueError('Vision observation is incomplete; raw output retained')
  result.update(source_sha256=document['source_sha256'],document_id=document_id,revision_id=document['revision_id'],review_status='model_observation_pending_ai_review' if kind=='observation' else 'derived_embedding',human_approved=False,created_at=now(),kind=kind)
  if kind=='observation':
   from app.knowledge.visual_screening import screen
   result=screen(result,document)
  with self.store.connection() as db:
   db.execute('BEGIN IMMEDIATE');self.store._check_version(db,document_id,expected_write_version)
   db.execute('INSERT OR IGNORE INTO multimodal_assets VALUES (?,?,?,?,?,?)',(*key,document['source_sha256'],self.store.encode(result)))
  return {**result,'cache_hit':False,'model_call':True}
 def prepare_input(self,document):
  path=Path(document['local_path']).resolve()
  if path.parent!=self.store.asset_dir.resolve():raise ValueError('Original is outside this store')
  data=path.read_bytes()
  if digest(data)!=document['source_sha256']:raise ValueError('Source checksum changed')
  model_input=data;preprocessing='original_bytes'
  if len(data)>8*1024*1024 or document.get('image_width',0)*document.get('image_height',0)>40_000_000:
   import fitz
   pix=fitz.Pixmap(str(path))
   if pix.width*pix.height>80_000_000:raise ValueError('Stored source exceeds preprocessing pixel budget')
   shrink=max(1,math.ceil(math.log2(max(pix.width,pix.height)/1600)))
   pix.shrink(shrink);model_input=pix.tobytes('png');preprocessing='large_source_png_downsample_max1600_v1'
   if len(model_input)>8*1024*1024:raise ValueError('Derived query image exceeds size budget')
  return model_input,preprocessing
 def process_many(self,items,expected_fingerprint):
  health=self.client.health()
  if health['vision_fingerprint']!=expected_fingerprint:raise KnowledgeConflictError('Vision model changed')
  results={};pending=[];inputs=[]
  for item in items:
   try:
    d=self.store.get_document(item['document_id'])
    if not d or d['write_version']!=item['expected_write_version']:raise KnowledgeConflictError('Source changed')
    with self.store.connection() as db:cached=db.execute("SELECT 1 FROM multimodal_assets WHERE document_id=? AND revision_id=? AND source_sha256=? AND fingerprint=? AND kind='observation'",(d['document_id'],d['revision_id'],d['source_sha256'],expected_fingerprint)).fetchone()
    if cached:results[item['key']]=self.process(d['document_id'],d['write_version'],'observation',expected_fingerprint)
    else:
     model_input=self.prepare_input(d)[0];pending.append(item);inputs.append(model_input)
   except Exception as exc:results[item['key']]=exc
  if pending:
   outputs=self.client.describe_many(inputs)
   if len(outputs)!=len(pending):raise ValueError('Visual batch count mismatch')
   for item,output in zip(pending,outputs):
    try:results[item['key']]=self.process(item['document_id'],item['expected_write_version'],'observation',expected_fingerprint,_inference=lambda data,out=output:out)
    except Exception as exc:results[item['key']]=exc
  return results
 def observations(self,document_id):
  doc=self.store.get_document(document_id)
  if not doc:raise FileNotFoundError('Document not found')
  with self.store.connection() as db:rows=db.execute("SELECT payload FROM multimodal_assets WHERE document_id=? AND revision_id=? AND source_sha256=? AND kind='observation' ORDER BY rowid DESC",(document_id,doc.get('revision_id'),doc.get('source_sha256'))).fetchall()
  from app.storage.evidence_review_store import EvidenceReviewStore
  reviews=EvidenceReviewStore(self.store).current(document_id)['items'];items=[json.loads(r[0]) for r in rows]
  from app.knowledge.visual_frames import VisualFrames
  frames=VisualFrames(self.store).current(document_id)
  for item in items:
   item['frame_samples']=[f for f in frames if f['fingerprint']==item['fingerprint']]
   review=next((r for r in reviews if r['scope']=='visual_observation' and r['evidence'].get('fingerprint')==item['fingerprint'] and r['evidence'].get('source_sha256')==doc.get('source_sha256')),None)
   if review:
    item['ai_review']=review;item['review_status']='ai_source_reviewed'
    if review['evidence'].get('corrected_observation'):
     item['raw_observation']=item['observation'];item['observation']=review['evidence']['corrected_observation']
  return {'items':items,'document_id':document_id,'revision_id':doc.get('revision_id'),'source_sha256':doc.get('source_sha256'),'human_approved':False}
 def search(self,base_id,*,text='',image=None,top_k=10,review_mode='reference',cross_language_query=False,description_retrieval=True):
  from app.core.models import QueryTrace,RetrievalHit
  started=perf_counter();trace=QueryTrace(original_query=text[:4000] or '[临时图片查询]',rewritten_query=text[:4000] or '[临时图片查询]',retrieval_mode='multimodal_rrf',metadata_filter={'knowledge_base_id':base_id},options={'top_k':top_k,'review_mode':review_mode,'cross_language_query':cross_language_query,'description_retrieval':description_retrieval},corpus_version=int(self.store.get_meta('generation')))
  trace.runtime={'query_image_sha256':digest(image) if image else None,'uploaded_image_persisted':False}
  try:
   result=self._search(base_id,text=text,image=image,top_k=top_k,review_mode=review_mode,cross_language_query=cross_language_query,description_retrieval=description_retrieval)
   for rank,item in enumerate(result['items'],1):
    with self.store.connection() as db:row=db.execute("SELECT c.payload,d.payload,r.state,r.revision_id,r.version FROM chunks c JOIN documents d ON d.document_id=c.document_id LEFT JOIN knowledge_reviews r ON r.document_id=d.document_id WHERE c.document_id=? AND d.deleted=0 AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1 ORDER BY c.rowid LIMIT 1",(item['document_id'],)).fetchone()
    source=json.loads(row[0]) if row else None;document=json.loads(row[1]) if row else {};review={'state':row[2],'revision_id':row[3]} if row and row[2] else None;state=effective_state(review,document.get('revision_id'))
    if not source or source['metadata'].get('revision_id')!=item['revision_id'] or document.get('revision_id')!=item['revision_id'] or document.get('source_sha256')!=item['source_sha256'] or not available({'knowledge_review_state':state},review_mode):raise KnowledgeConflictError('Image evidence changed before trace publication')
    trace.reranked_docs.append(RetrievalHit(doc_id=item['document_id'],chunk_id=source['chunk_id'],content=source['content'],metadata={**source['metadata'],'source_sha256':item['source_sha256'],'knowledge_review_version':row[4] or 0,'knowledge_review_state':state,'multimodal_matches':item['matches']},score=item['score'],final_rank=rank,retrieval_sources=[m['channel'] for m in item['matches']]))
   from app.storage.workbench_store import WorkbenchStore
   checked_generation=WorkbenchStore(self.store).assert_sources_current(trace.reranked_docs)
   trace.retrieved_docs=[h.model_copy(deep=True) for h in trace.reranked_docs];trace.latency_ms={'total':round((perf_counter()-started)*1000,3)};trace.cost={'total':None};trace.runtime.update(status='completed',embedding_model=result.get('model'),description_channel=result.get('description_channel'),query_translation=result.get('query_translation'),scope=base_id,evidence_revision_checked=True,corpus_version_at_validation=checked_generation);self.store.save_trace(trace.model_dump());return {**result,'trace_id':trace.trace_id,'latency_ms':trace.latency_ms}
  except Exception as exc:
   trace.runtime.update(status='failed',error_type=type(exc).__name__,stage=getattr(exc,'stage',None),code=getattr(exc,'code',None));trace.latency_ms={'total':round((perf_counter()-started)*1000,3)};self.store.save_trace(trace.model_dump());exc.trace_id=trace.trace_id;raise
 def _search(self,base_id,*,text='',image=None,top_k=10,review_mode='reference',cross_language_query=False,description_retrieval=True):
  validate_base(base_id)
  if review_mode not in {'reference','published_only'} or not 1<=top_k<=30:raise ValueError('Invalid search options')
  if not text.strip() and not image:raise ValueError('Text or a new image is required')
  if len(text)>4000 or (image and len(image)>8*1024*1024):raise ValueError('Query exceeds text/image limit')
  health=self.client.health();fingerprint=health['embedding_fingerprint']
  with self.store.connection() as db:
   rows=db.execute("SELECT d.payload,a.payload,r.state,r.revision_id FROM documents d JOIN multimodal_assets a ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') LEFT JOIN knowledge_reviews r ON r.document_id=d.document_id WHERE d.deleted=0 AND json_extract(d.payload,'$.knowledge_base_id')=? AND a.kind='embedding' AND a.fingerprint=? AND EXISTS(SELECT 1 FROM chunks c WHERE c.document_id=d.document_id AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1)",(base_id,fingerprint)).fetchall()
   scoped=[]
   for row in rows:
    d=json.loads(row[0]);review={'state':row[2],'revision_id':row[3]} if row[2] else None
    if available({'knowledge_review_state':effective_state(review,d.get('revision_id'))},review_mode):scoped.append((d,json.loads(row[1])))
   ids=[d['document_id'] for d,_ in scoped];texts={};descriptions={};sampled_descriptions={};reviewed_descriptions={};reviewed_evidence={}
   # Only the selected base/current searchable evidence contributes lexical matches.
   for identity in ids:
    texts[identity]='\n'.join(json.loads(r[0])['content'] for r in db.execute("SELECT payload FROM chunks WHERE document_id=? AND coalesce(json_extract(payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(payload,'$.metadata.retrievable'),1)=1",(identity,)))
   if description_retrieval:
    # Rejected/quarantined descriptions never contribute retrieval text. A model
    # sentence is a separate channel, not an OCR or technical-fact assertion.
    has_reviews=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_reviews'").fetchone())
    has_frames=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='multimodal_frame_observations'").fetchone())
    for identity,d in ((d['document_id'],d) for d,_ in scoped):
     row=db.execute("SELECT payload FROM multimodal_assets WHERE document_id=? AND revision_id=? AND source_sha256=? AND kind='observation' AND fingerprint=? ORDER BY rowid DESC LIMIT 1",(identity,d['revision_id'],d['source_sha256'],health['vision_fingerprint'])).fetchone()
     if row:
      value=json.loads(row[0]);ob=value.get('observation') or {}
      review_row=db.execute("SELECT payload FROM evidence_reviews WHERE document_id=? AND revision_id=? AND json_extract(payload,'$.scope')='visual_observation' AND json_extract(payload,'$.evidence.fingerprint')=? AND json_extract(payload,'$.evidence.source_sha256')=? ORDER BY rowid DESC LIMIT 1",(identity,d['revision_id'],health['vision_fingerprint'],d['source_sha256'])).fetchone() if has_reviews else None
      if review_row:
       review=json.loads(review_row[0])
       if review['decision'] in {'rejected','insufficient_evidence'}:continue
       if review['evidence'].get('corrected_observation'):
        from app.knowledge.visual_screening import screen
        ob=review['evidence']['corrected_observation'];value=screen({**value,'observation':ob},d)
      if value.get('ai_screening',{}).get('retrieval_allowed') is True:descriptions[identity]=value['ai_screening'].get('retrieval_text',ob.get('visible_description',''))
      if has_frames:
       sampled=[json.loads(v[0]) for v in db.execute("SELECT payload FROM multimodal_frame_observations WHERE document_id=? AND revision_id=? AND source_sha256=? AND fingerprint=? AND json_extract(payload,'$.ai_screening.retrieval_allowed')=1",(identity,d['revision_id'],d['source_sha256'],health['vision_fingerprint']))]
       if sampled:
        sampled_descriptions[identity]='\n'.join(v['ai_screening']['retrieval_text'] for v in sampled)
        descriptions[identity]='\n'.join([descriptions.get(identity,''),sampled_descriptions[identity]])
      label_row=db.execute("SELECT payload FROM evidence_reviews WHERE document_id=? AND revision_id=? AND json_extract(payload,'$.scope')='visual_source_labels' AND json_extract(payload,'$.evidence.fingerprint')=? AND json_extract(payload,'$.evidence.source_sha256')=? ORDER BY rowid DESC LIMIT 1",(identity,d['revision_id'],health['vision_fingerprint'],d['source_sha256'])).fetchone() if has_reviews else None
      if label_row:
       label=json.loads(label_row[0]);label_text=label['evidence'].get('source_visible_description','')
       if label['decision'] in {'accepted','corrected'} and label['evidence'].get('source_inspected') is True and isinstance(label_text,str) and label_text.strip():
        from app.knowledge.visual_screening import screen
        checked=screen({**value,'observation':{'visible_description':label_text}},d)['ai_screening']
        if checked['retrieval_allowed']:
         reviewed_descriptions[identity]=checked['retrieval_text'];reviewed_evidence[identity]={'review_id':label['review_id'],'source_text':label_text}
  if not scoped:return {'items':[],'indexed_documents':0,'scope':base_id,'model':fingerprint,'reason':'No indexed images in this review scope'}
  queries=[];encoded_text=text;translation=None
  if cross_language_query and text:
   if re.search(r'[\u3400-\u9fff]',text):
    from app.runtime import RuntimeSession
    from app.runtime.privacy import ensure_local_only
    runtime=RuntimeSession.from_env('configured');ensure_local_only(runtime,[],require_local=True)
    encoded_text=runtime.chain.translate_query(text);translation={'text':encoded_text,'model':runtime.chain.model_name,'is_evidence':False}
  if text:queries.append(('text_image',self.client.encode(text=encoded_text)))
  if image:queries.append(('image_image',self.client.encode(image=image)))
  if any(q['fingerprint']!=fingerprint for _,q in queries):raise ValueError('Cross-modal model changed')
  ranked=[]
  for channel,q in queries:
   scores=[]
   for d,a in scoped:
    if len(q['vector'])!=len(a['vector']):raise ValueError('Cross-modal dimension changed')
    scores.append((d['document_id'],sum(x*y for x,y in zip(q['vector'],a['vector']))))
   ranked.append((channel,sorted(scores,key=lambda x:x[1],reverse=True)))
  if text:
   from app.retrievers.evidence_retriever import lexical_tokens
   stop={'a','an','the','and','or','of','in','on','at','to','for','with','by','is','are','showing','pictures','image','photo'}
   terms=set(lexical_tokens(text))-stop;lex=[(i,len(terms&set(lexical_tokens(t)))/max(1,len(terms))) for i,t in texts.items()]
   ranked.append(('source_ocr_text',sorted((x for x in lex if x[1]>=.2),key=lambda x:x[1],reverse=True)))
   if descriptions:
    # A reviewed first-frame label replaces the weak first-frame sentence;
    # unreviewed animation samples keep their own weak evidence weight.
    desc=[(i,len(terms&set(lexical_tokens(sampled_descriptions.get(i,'') if i in reviewed_descriptions else t)))/max(1,len(terms))) for i,t in descriptions.items()]
    ranked.append(('visual_description_text',sorted((x for x in desc if x[1]>=.2),key=lambda x:x[1],reverse=True)))
   if reviewed_descriptions:
    # Source-checked labels are often short summaries of long layout queries.
    # Reuse BM25's document-frequency weighting instead of diluting rare object
    # phrases by the number of overlapping query n-grams.
    from app.indexes.bm25_index import BM25Index
    from app.core.models import RagChunk
    label_stop=stop|{'图片','画面','图像','图中','其中','流程','程图','流程图','示意','意图','示意图','工艺','设备','系统','生产','工厂','车间','布局','diagram','chart','figure','process','system','equipment','device','layout','picture','show','visible'}
    generic_phrases={t for t in label_stop if all('\u3400'<=c<='\u9fff' for c in t)}|{'工艺流程示意图','工艺流程图','工艺流程','流程示意图','系统架构图','设备系统','示意'}
    generic_pattern='|'.join(re.escape(t) for t in sorted(generic_phrases,key=lambda t:(-len(t),t)))
    def label_tokens(t):return sorted(set(lexical_tokens(re.sub(generic_pattern,' ',t)))-label_stop)
    label_query_terms=set(label_tokens(text))
    label_index=BM25Index(tokenizer=label_tokens,searchable_fields=())
    label_chunks=[];eligible_labels=set()
    for identity,label_text in reviewed_descriptions.items():
     shared=label_query_terms&set(label_tokens(label_text))
     if len(shared)>=2 or any(len(t)>=3 if any('\u3400'<=c<='\u9fff' for c in t) else len(t)>=5 for t in shared):eligible_labels.add(identity)
     label_chunks.append(RagChunk(doc_id=identity,chunk_id=reviewed_evidence[identity]['review_id'],content=label_text))
    label_index.build(label_chunks)
    ranked.append(('source_reviewed_visual_text',[(h.doc_id,h.score) for h in label_index.search(text,len(label_chunks)) if h.doc_id in eligible_labels][:30]))
  fused={};docs={d['document_id']:d for d,_ in scoped}
  for channel,ranking in ranked:
   for rank,(identity,score) in enumerate(ranking[:30],1):
    value=fused.setdefault(identity,{'score':0,'matches':[]});value['score']+=(.2 if channel=='visual_description_text' else .35 if channel=='source_ocr_text' else 1)/(60+rank);match={'channel':channel,'rank':rank,'raw_score':score,'is_model_observation':channel=='visual_description_text'}
    if channel=='source_reviewed_visual_text':match.update(evidence_review_id=reviewed_evidence[identity]['review_id'],evidence_text=reviewed_evidence[identity]['source_text'],source_inspection_scope='visible_objects_and_layout_only')
    value['matches'].append(match)
  items=[]
  for identity,value in sorted(fused.items(),key=lambda x:x[1]['score'],reverse=True)[:top_k]:
   d=docs[identity];items.append({'document_id':identity,'revision_id':d['revision_id'],'source_sha256':d['source_sha256'],'knowledge_base_id':base_id,'source_caption':d.get('source_caption',d.get('title','')),'source_page':d.get('source_page'),'review_status':'source_reference','has_visual_description':identity in descriptions,**value})
  # Recheck current source and review scope after the potentially slow inference.
  with self.store.connection() as db:
   db.execute('BEGIN')
   for item in items:
    row=db.execute('SELECT d.payload,d.deleted,r.state,r.revision_id FROM documents d LEFT JOIN knowledge_reviews r ON r.document_id=d.document_id WHERE d.document_id=?',(item['document_id'],)).fetchone()
    current=json.loads(row[0]) if row else {}
    review={'state':row[2],'revision_id':row[3]} if row and row[2] else None
    state=effective_state(review,current.get('revision_id'))
    active=db.execute("SELECT 1 FROM chunks WHERE document_id=? AND coalesce(json_extract(payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(payload,'$.metadata.retrievable'),1)=1 LIMIT 1",(item['document_id'],)).fetchone()
    if not row or row[1] or current.get('write_version')!=docs[item['document_id']].get('write_version') or not active or not available({'knowledge_review_state':state},review_mode):raise KnowledgeConflictError('Image evidence changed during search; retry with current scope')
    if any(m['channel']=='source_reviewed_visual_text' for m in item['matches']):
     latest=db.execute("SELECT review_id FROM evidence_reviews WHERE document_id=? AND revision_id=? AND json_extract(payload,'$.scope')='visual_source_labels' AND json_extract(payload,'$.evidence.fingerprint')=? AND json_extract(payload,'$.evidence.source_sha256')=? ORDER BY rowid DESC LIMIT 1",(item['document_id'],item['revision_id'],health['vision_fingerprint'],item['source_sha256'])).fetchone()
     if not latest or latest[0]!=reviewed_evidence[item['document_id']]['review_id']:raise KnowledgeConflictError('Source-visible labels changed during search')
    item['knowledge_review_state']=state
  return {'items':items,'scope':base_id,'indexed_documents':len(scoped),'model':fingerprint,'query_translation':translation,'fusion':'rrf_k60_top30_lexical_weight035','description_channel':{'enabled':description_retrieval,'available':len(descriptions),'rrf_weight':.2,'model':health['vision_fingerprint'],'model_text_is_not_ocr':True,'source_reviewed_available':len(reviewed_descriptions),'source_reviewed_rrf_weight':1},'uploaded_image_persisted':False,'interpretation':'图文相似、模型观察与来源文字分别匹配，不代表缺陷根因、工艺适用性或质量放行。'}
