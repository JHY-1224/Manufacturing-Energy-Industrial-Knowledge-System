from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from app.core.engine import CachedEmbedding
from app.core.models import IngestRequest, QueryOptions, QueryRequest
from app.knowledge.catalog import KNOWLEDGE_BASES, validate_base
from app.runtime import RuntimeSession
from app.storage.knowledge_store import KnowledgeConflictError, now, digest
from app.storage.workbench_store import WorkbenchStore
from app.storage.workbench_read import WorkbenchReadStore
from app.storage.review_store import ReviewStore


class WorkbenchService:
    def __init__(self, knowledge):
        self.knowledge,self.engine,self.store=knowledge,knowledge.engine,knowledge.store
        self.records=WorkbenchStore(self.store)
        self.reader=WorkbenchReadStore(self.store)
        self.reviews=ReviewStore(self.store)

    def bases(self):
        counts=self.reader.counts()['bases']
        return [{**b,'document_count':counts.get(b['id'],0),
                 'status':'ready_for_materials'} for b in KNOWLEDGE_BASES]

    def documents(self):
        return self.knowledge.documents()

    def chunks(self, document_id=None, query=''):
        _,raw=self.store.snapshot()
        result=[]
        for chunk in raw:
            if document_id and chunk['doc_id']!=document_id:continue
            if query and query.casefold() not in (chunk['content']+' '+str(chunk['metadata'].get('section',''))).casefold():continue
            result.append({**chunk,'enabled':chunk['metadata'].get('enabled',True),
                           'vector_records':self.records.vector_records(chunk['chunk_id']),
                           'token_count':len(chunk['content'])//2,'token_count_kind':'character_estimate'})
        return result

    def process(self, document_id, parser_mode='auto', splitter='parent_child', chunk_size=700, chunk_overlap=120):
        document=self.store.get_document(document_id)
        if not document:raise FileNotFoundError('Document not found')
        if not document.get('local_path'):raise ValueError('Demo has no original file; upload a document to process')
        path=Path(document['local_path'])
        if digest(path.read_bytes())!=document.get('source_sha256'):
            raise ValueError('Stored original failed SHA256 verification')
        return self.engine.ingest(IngestRequest(path=document['local_path'],document_id=document_id,domain=document['domain'],
            doc_type=document['doc_type'],knowledge_base_id=document.get('knowledge_base_id','energy'),
            parser_mode=parser_mode,splitter=splitter,chunk_size=chunk_size,chunk_overlap=chunk_overlap,
            metadata={'provenance':document.get('provenance',{})}))

    def mutate_chunk(self,chunk_id,expected_revision,content=None,enabled=None,delete=False):
        _,all_chunks=self.store.snapshot()
        target=next((c for c in all_chunks if c['chunk_id']==chunk_id),None)
        if not target:raise FileNotFoundError('Chunk not found')
        document,chunks=self.records.document_snapshot(target['doc_id'])
        if document['revision_id']!=expected_revision:raise KnowledgeConflictError('Document revision changed; refresh before editing')
        target=next((c for c in chunks if c['chunk_id']==chunk_id),None)
        if target is None:raise KnowledgeConflictError('Chunk changed; refresh before editing')
        previous_revision=document['revision_id'];revision='rev_'+uuid4().hex
        parent=target.get('parent_id')
        for chunk in chunks:
            chunk['metadata']['revision_id']=revision
            if chunk['chunk_id']==chunk_id:
                if content is not None:
                    if not content.strip():raise ValueError('Chunk content must not be empty')
                    chunk['content']=content;chunk['metadata'].update(manually_edited=True,edited_at=now(),start_offset=None,end_offset=None)
                if enabled is not None:chunk['metadata']['enabled']=enabled
            # Parent snapshots contain the old/disabled text. Invalidate the entire group.
            if (parent and chunk.get('parent_id')==parent) or chunk['chunk_id']==chunk_id:
                chunk['parent_id']=None;chunk['parent_content']=None
                chunk['metadata'].pop('parent_content',None);chunk['metadata'].pop('parent_id',None)
            chunk['metadata'].pop('section_id',None)
        if delete:chunks=[c for c in chunks if c['chunk_id']!=chunk_id]
        document.update(revision_id=revision,chunk_count=len(chunks),updated_at=now(),processing_key=None)
        self.store.replace_document(document,chunks,{'operation':'delete_chunk' if delete else 'edit_chunk','chunk_id':chunk_id,'previous_revision':previous_revision})
        self.engine.refresh()
        return {'document_id':document['document_id'],'revision_id':revision,'chunk_count':len(chunks),'history_retained':True}

    def rebuild(self,document_id,runtime_mode):
        # Index rebuilding uses current edited chunks; it never reparses the original.
        document,chunks=self.records.document_snapshot(document_id)
        runtime=RuntimeSession.from_env(runtime_mode)
        embedding=CachedEmbedding(runtime.embedding,self.store)
        from app.core.models import RagChunk
        active=[RagChunk.model_validate(c) for c in chunks if c['metadata'].get('enabled',True) and c['metadata'].get('retrievable',True)]
        from app.runtime.privacy import ensure_local_only
        ensure_local_only(runtime,active,require_local=document.get('allow_cloud_upload') is False)
        for chunk in active:embedding.embed(chunk.content)
        current=self.store.get_document(document_id)
        if not current or current['write_version']!=document['write_version']:raise KnowledgeConflictError('Document changed during index rebuild')
        model='offline-hash-sha256-v1' if runtime_mode=='offline' else runtime.embedding.model_name
        self.records.record_vectors(active,runtime.embedding.fingerprint,model,runtime.embedding.dimension,'sqlite_exact')
        return {'status':'completed','document_id':document_id,'revision_id':document['revision_id'],'indexed_chunks':len(active),
                'runtime':runtime.metadata(),'vector_cache_hits':embedding.hits,'preserved_manual_edits':True}

    def retrieve(self,question,options):
        return self.engine.query(QueryRequest(question=question,options=options,generate_answer=False))

    def revisions(self,document_id):
        with self.store.connection() as db:
            result=[]
            for row in db.execute('SELECT revision_id,payload,created_at FROM revisions WHERE document_id=? ORDER BY rowid DESC',(document_id,)):
                data=json.loads(row['payload'])
                result.append({'revision_id':row['revision_id'],'created_at':row['created_at'],'chunk_count':len(data['chunks']),'operation':data.get('config',{}).get('operation','ingest')})
            return result

    def source(self,document_id,revision_id=None):
        document=self.store.get_document(document_id)
        if revision_id:
            with self.store.connection() as db:
                row=db.execute('SELECT payload FROM revisions WHERE revision_id=? AND document_id=?',(revision_id,document_id)).fetchone()
                if row:document=json.loads(row[0])['document']
                else:raise FileNotFoundError('Revision not found')
        if not document or not document.get('local_path'):raise FileNotFoundError('Original not available')
        path=Path(document['local_path']).resolve()
        if path.parent!=self.store.asset_dir.resolve() or not path.is_file():raise FileNotFoundError('Original not available in this store')
        return path,document.get('source_name',path.name)

    def status(self):
        import os, importlib.util
        models=[]
        for stage in ['CHAT','EMBEDDING','RERANK']:
            models.append({'id':stage.lower(),'name':os.environ.get('RAG_'+stage+'_MODEL','未配置'),'status':'configured' if os.environ.get('RAG_'+stage+'_URL') else 'not_configured'})
        return {'backend':'online','metadata_store':{'name':'SQLite','status':'ready'},'vector_store':{'name':'SQLite 向量缓存 / Exact Search','status':'ready'},
                'postgres':{'status':'not_configured'},'milvus':{'status':'not_configured'},'redis':{'status':'optional_not_required'},
                'agent_runtime':'langgraph' if importlib.util.find_spec('langgraph') else 'not_installed',
                'ocr':{'provider':'Apple Vision','status':'available' if __import__('platform').system()=='Darwin' else 'not_configured'},'models':models}

    def summary(self):
        counts=self.reader.counts();sessions=self.records.sessions_page(page_size=6)
        recent=self.reader.documents(page_size=6)['items']
        return {'document_count':counts['documents'],'chunk_count':counts['chunks'],'session_count':sessions['total'],
                'image_count':counts['images'],'analyzed_image_count':counts['analyzed_images'],'ocr_with_text':counts['ocr_with_text'],'categories':counts['categories'],
                'documents':[self.knowledge.document_view(d) for d in recent],'sessions':sessions['items'],
                'status':self.status(),'knowledge_bases':[{**b,'document_count':counts['bases'].get(b['id'],0),
                'status':'ready_for_materials'} for b in KNOWLEDGE_BASES],'demo_records':counts['demo']}
