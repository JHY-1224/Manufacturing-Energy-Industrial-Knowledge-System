from __future__ import annotations
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from app.core.models import IngestRequest,RagChunk
from app.runtime import RuntimeSession,RuntimeAdapterError
from app.runtime.privacy import ensure_local_only
from app.storage.knowledge_store import KnowledgeConflictError,digest
from app.storage.task_store import TaskStore
from app.knowledge.catalog import validate_base


def safe_error(exc):
    return f'{type(exc).__name__}: {getattr(exc,"code","processing_failed")}'


class BackgroundTasks:
    def __init__(self,store,runtime_factory=None):
        self.store=store;self.tasks=TaskStore(store)
        self.runtime_factory=runtime_factory or RuntimeSession.from_env
        self.stop_event=threading.Event();self.wake=threading.Event();self.thread=None
        self.token=uuid4().hex;self.active_task=None;self.last_error=None;self.start_lock=threading.Lock()
        self.process_engine=None

    def create(self,kind,knowledge_base_id=None,document_ids=None,runtime_mode='configured',process_options=None):
        if kind not in {'index','process','image','image_frames','multimodal_index','vision_describe'}:raise ValueError('Unknown task kind')
        if runtime_mode not in {'offline','configured'}:raise ValueError('Unknown runtime mode')
        if knowledge_base_id:validate_base(knowledge_base_id)
        ids=list(dict.fromkeys(document_ids or []));config={'knowledge_base_id':knowledge_base_id,'document_ids':sorted(ids),'runtime_mode':runtime_mode}
        model=None
        if kind=='index':
            runtime=self.runtime_factory(runtime_mode);ensure_local_only(runtime,[],require_local=True)
            embedding=runtime.embedding
            model={'fingerprint':embedding.fingerprint,'model':embedding.model_name if runtime_mode=='configured' else 'offline-hash-sha256-v1','dimension':embedding.dimension}
            config['model']=model
        elif kind in {'multimodal_index','vision_describe'}:
            from app.knowledge.multimodal import MultimodalService
            multimodal=MultimodalService(self.store);info=multimodal.client.health()
            config['runtime_mode']='local_pretrained';config['multimodal_kind']='embedding' if kind=='multimodal_index' else 'observation'
            config['multimodal_fingerprint']=info['embedding_fingerprint' if kind=='multimodal_index' else 'vision_fingerprint']
        elif kind in {'image','image_frames'}:
            from app.knowledge.image_analysis import ImageAnalysisService
            analysis=ImageAnalysisService(self.store)
            config['runtime_mode']='local_vision';config['image_model']=analysis.analyzer.fingerprint
        else:
            config['runtime_mode']='offline';config['process_options']=process_options or {'parser_mode':'auto','splitter':'parent_child'}
        with self.store.connection() as db:
            db.execute('BEGIN');where=['deleted=0'];args=[]
            if knowledge_base_id:where.append("json_extract(payload,'$.knowledge_base_id')=?");args.append(knowledge_base_id)
            if ids:where.append('document_id IN ('+','.join('?' for _ in ids)+')');args.extend(ids)
            docs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM documents WHERE '+' AND '.join(where)+' ORDER BY rowid',args)]
            if ids and set(ids)!={d['document_id'] for d in docs}:raise ValueError('Requested document is missing or outside this knowledge base')
            items=[]
            for doc in docs:
                if kind in {'multimodal_index','vision_describe'}:
                    if not doc.get('image_sha256') or not doc.get('local_path'):continue
                    items.append({'key':doc['document_id'],'document_id':doc['document_id'],'expected_write_version':doc['write_version'],'source_sha256':doc['source_sha256'],'source_name':doc.get('source_name',doc['document_id'])})
                elif kind in {'image','image_frames'}:
                    if not doc.get('image_sha256') or not doc.get('local_path'):continue
                    if kind=='image_frames':
                        if doc.get('frame_count',1)<=1 or doc.get('frames_processed',1)>=doc['frame_count']:continue
                    elif (doc.get('image_analysis_key')==analysis.key(doc['source_sha256']) or
                          (doc.get('visual_embedding_model')==analysis.analyzer.fingerprint and doc.get('frames_processed',1)>=doc.get('frame_count',1))):continue
                    items.append({'key':doc['document_id'],'document_id':doc['document_id'],'expected_write_version':doc['write_version'],
                                  'source_sha256':doc['source_sha256'],'source_name':doc.get('source_name',doc['document_id'])})
                elif kind=='process':
                    if doc.get('status')=='indexed':
                        if ids:raise ValueError('Indexed documents keep their current edits; use index tasks')
                        continue
                    if not doc.get('local_path'):continue
                    if doc.get('doc_type') in {'image_source_reference','text_training_reference'}:raise ValueError('Source reference collections require their dedicated importer')
                    items.append({'key':doc['document_id'],'document_id':doc['document_id'],'expected_write_version':doc['write_version'],
                                  'source_sha256':doc['source_sha256'],'source_name':doc.get('source_name',doc['document_id'])})
                else:
                    for row in db.execute('SELECT payload FROM chunks WHERE document_id=? ORDER BY rowid',(doc['document_id'],)):
                        chunk=json.loads(row[0]);meta=chunk['metadata']
                        if not meta.get('enabled',True) or not meta.get('retrievable',True):continue
                        items.append({'key':chunk['chunk_id'],'document_id':doc['document_id'],'revision_id':meta.get('revision_id',''),
                            'content_sha256':digest(chunk['content'].encode()),'characters':len(chunk['content']),
                            'cache_key':digest((model['fingerprint']+'\0'+chunk['content']).encode()),'source_name':doc.get('source_name',doc['document_id'])})
        if not items:raise ValueError('No eligible documents or chunks in this scope')
        result=self.tasks.create(kind,config,items);self.wake.set();return result

    def start(self):
        with self.start_lock:
            if self.thread and self.thread.is_alive():return
            self.stop_event.clear();self.token=uuid4().hex
            self.thread=threading.Thread(target=self._loop,name='local-knowledge-worker',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=130)

    def health(self):
        return {'running':bool(self.thread and self.thread.is_alive()),'active_task':self.active_task,'last_error':self.last_error,'concurrency':1}

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                task=self.tasks.claim(self.token)
                if task:
                    self.active_task=task['task_id'];self._run(task);self.active_task=None;continue
            except Exception as exc:self.last_error=safe_error(exc);self.active_task=None
            self.wake.wait(1);self.wake.clear()

    def _run(self,task):
        identity=task['task_id'];failure=None
        try:
            runtime=None
            if task['kind']=='index':
                runtime=self.runtime_factory(task['config']['runtime_mode']);ensure_local_only(runtime,[],require_local=True)
                if runtime.embedding.fingerprint!=task['config']['model']['fingerprint']:
                    raise RuntimeAdapterError('task_model_changed','embedding')
            while not self.stop_event.is_set():
                if self.tasks.release(identity,self.token):return
                items=self.tasks.take(identity,self.token,8 if task['kind']=='index' else 4 if task['kind']=='vision_describe' else 2 if task['kind'] in {'image','image_frames'} else 1)
                if not items:continue
                if task['kind']=='index':self._index(task,items,runtime)
                elif task['kind'] in {'image','image_frames'}:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(lambda item:self._image(task,item),items))
                elif task['kind']=='vision_describe':self._visual_batch(task,items)
                elif task['kind']=='multimodal_index':self._multimodal(task,items[0])
                else:self._process(task,items[0])
        except Exception as exc:
            failure=safe_error(exc);self.last_error=failure
        finally:
            # A completed/paused task was already released; never reclaim its state.
            try:self.tasks.release(identity,self.token,failure=failure,stopping=self.stop_event.is_set())
            except KnowledgeConflictError:pass

    def _index(self,task,items,runtime):
        results=[];valid=[]
        with self.store.connection() as db:
            rows=db.execute('SELECT c.chunk_id,c.payload FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE d.deleted=0 AND c.chunk_id IN ('+','.join('?' for _ in items)+')',[i['key'] for i in items]).fetchall()
            current={r[0]:json.loads(r[1]) for r in rows}
        for item in items:
            chunk=current.get(item['key'])
            if (not chunk or chunk['metadata'].get('revision_id','')!=item['revision_id'] or digest(chunk['content'].encode())!=item['content_sha256']
                    or not chunk['metadata'].get('enabled',True) or not chunk['metadata'].get('retrievable',True)):
                results.append({**item,'status':'stale','error':'Source changed; create a new task for current revision'})
            else:valid.append((item,RagChunk.model_validate(chunk)))
        if results:self.tasks.publish(task['task_id'],self.token,results)
        if not valid:return
        ensure_local_only(runtime,[c for _,c in valid],require_local=True)
        cached=self.store.get_vectors([i['cache_key'] for i,_ in valid]);missing={}
        for item,chunk in valid:
            if item['cache_key'] not in cached:missing[item['cache_key']]=chunk.content
        try:
            if missing:
                embed=runtime.embedding
                vectors=embed.embed_many(list(missing.values())) if hasattr(embed,'embed_many') else [embed.embed(t) for t in missing.values()]
                if len(vectors)!=len(missing):raise RuntimeAdapterError('invalid_embedding_rows','embedding')
                cached.update(zip(missing,vectors))
            result=[]
            for item,_ in valid:
                vector=cached[item['cache_key']]
                if (len(vector)!=runtime.embedding.dimension or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector)
                        or not 0<sum(v*v for v in vector)<math.inf):raise RuntimeAdapterError('invalid_cached_vector','embedding')
                result.append({**item,'vector':vector,'output':{'cache_hit':item['cache_key'] not in missing,'model_call':item['cache_key'] in missing}})
            self.tasks.publish(task['task_id'],self.token,result,task['config']['model'])
        except Exception as exc:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'failed','error':safe_error(exc)} for item,_ in valid])
            raise

    def _image(self,task,item):
        from app.knowledge.image_analysis import ImageAnalysisService
        try:
            analysis=ImageAnalysisService(self.store)
            if analysis.analyzer.fingerprint!=task['config']['image_model']:raise ValueError('Image model changed')
            document=self.store.get_document(item['document_id'])
            if not document or document.get('source_sha256')!=item['source_sha256']:raise KnowledgeConflictError('Source changed')
            output=analysis.process(item['document_id'],item['expected_write_version'],all_frames=task['kind']=='image_frames')
            self.tasks.publish(task['task_id'],self.token,[{**item,'output':output}])
        except KnowledgeConflictError:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'stale','error':'Document changed; create a new image task'}])
        except Exception as exc:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'failed','error':safe_error(exc)}])

    def _process(self,task,item):
        try:
            document=self.store.get_document(item['document_id'])
            marker=(document or {}).get('process_config',{}).get('metadata',{})
            if (document and document.get('status')=='indexed' and document.get('source_sha256')==item['source_sha256']
                    and marker.get('background_task_id')==task['task_id'] and marker.get('background_task_item')==item['ordinal']):
                self.tasks.publish(task['task_id'],self.token,[{**item,'output':{'revision_id':document['revision_id'],'chunk_count':document['chunk_count'],'recovered_committed_result':True}}]);return
            if not document or document['write_version']!=item['expected_write_version']:raise KnowledgeConflictError('Source changed')
            if digest(Path(document['local_path']).read_bytes())!=item['source_sha256']:
                raise ValueError('Original checksum changed')
            if self.process_engine is None:
                from app.core.engine import ConfigurableRagEngine
                self.process_engine=ConfigurableRagEngine(self.store)
            result=self.process_engine.ingest(IngestRequest(path=document['local_path'],document_id=document['document_id'],
                domain=document.get('domain',''),doc_type=document.get('doc_type','general'),knowledge_base_id=document.get('knowledge_base_id','energy'),
                expected_write_version=item['expected_write_version'],metadata={'background_task_id':task['task_id'],'background_task_item':item['ordinal']},**task['config']['process_options']))
            self.tasks.publish(task['task_id'],self.token,[{**item,'output':{'revision_id':result['revision_id'],'chunk_count':result['chunk_count']}}])
        except KnowledgeConflictError:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'stale','error':'Document changed; create a new task for the current version'}])
        except Exception as exc:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'failed','error':safe_error(exc)}])

    def _multimodal(self,task,item):
        from app.knowledge.multimodal import MultimodalService
        try:
            output=MultimodalService(self.store).process(item['document_id'],item['expected_write_version'],task['config']['multimodal_kind'],task['config']['multimodal_fingerprint'])
            # Full vectors/observations live once in the revision-bound derived cache.
            result={k:output[k] for k in ('cache_hit','model_call','revision_id','fingerprint','review_status')}
            self.tasks.publish(task['task_id'],self.token,[{**item,'output':result}])
        except KnowledgeConflictError:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'stale','error':'Source/model changed; create a new task'}])
        except Exception as exc:
            self.tasks.publish(task['task_id'],self.token,[{**item,'status':'failed','error':safe_error(exc)}])

    def _visual_batch(self,task,items):
        from app.knowledge.multimodal import MultimodalService
        shared_failure=None
        try:
            outputs=MultimodalService(self.store).process_many(items,task['config']['multimodal_fingerprint'])
        except Exception as exc:
            shared_failure=exc
            outputs={item['key']:exc for item in items}
        results=[]
        for item in items:
            value=outputs[item['key']]
            if isinstance(value,Exception):
                results.append({**item,'status':'stale' if isinstance(value,KnowledgeConflictError) else 'failed','error':safe_error(value)})
            else:results.append({**item,'output':{k:value[k] for k in ('cache_hit','model_call','revision_id','fingerprint','review_status')}})
        self.tasks.publish(task['task_id'],self.token,results)
        if shared_failure:raise shared_failure
