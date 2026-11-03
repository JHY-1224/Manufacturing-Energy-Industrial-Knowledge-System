from __future__ import annotations

from pathlib import Path

from app.core.models import IngestRequest
from app.knowledge.catalog import base_policy
from app.storage.knowledge_store import KnowledgeConflictError, digest, now


class KnowledgeService:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store

    def upload(self, filename: str, content: bytes, domain: str, doc_type: str, knowledge_base_id=None, provenance=None) -> dict:
        if not content:
            raise ValueError("Uploaded file is empty")
        if len(content) > 64 * 1024 * 1024:
            raise ValueError("Document exceeds 64 MiB")
        filename = Path(filename.replace('\\', '/')).name
        if not filename or filename.startswith('.'):
            raise ValueError("Invalid filename")
        sha, stored_path = self.store.save_asset(content, filename)
        source_key = "upload:" + digest((filename + '\0' + domain + '\0' + doc_type + '\0' + sha + ('\0'+knowledge_base_id if knowledge_base_id else '')).encode())
        document = self.store.register(source_key, {
            "document_id": "doc_upload_" + digest(source_key.encode())[:24],
            "title": Path(filename).stem, "source_name": filename,
            "stored_filename": Path(stored_path).name, "local_path": stored_path,
            "domain": domain, "doc_type": doc_type, "size_bytes": len(content),
            "source_sha256": sha, "status": "uploaded", "chunk_count": 0,
            "version": "v1", "created_at": now(), "updated_at": now(), "data_source": "local",
            'knowledge_base_id':knowledge_base_id or ('electrical' if domain=='calc_logic' else 'energy'),
            'provenance':provenance or {'source_type':'user_upload','review_status':'unreviewed'},
            **base_policy(knowledge_base_id),
        })
        return self.document_view(document)

    def document_view(self, document):
        document=dict(document)
        if not document.get('source_caption') and document.get('source_caption_asset'):
            path=Path(document['source_caption_asset']).resolve()
            if path.parent==self.store.asset_dir.resolve() and path.is_file():
                with path.open('r',encoding='utf-8-sig') as handle:document['source_caption']=handle.read(1200).strip()
        size = document.get('size_bytes', 0)
        indexed = document.get('status') == 'indexed'
        return {**document, "domain": self.legacy_domain(document.get('domain', '')), "file_type": Path(document.get('source_name', '')).suffix.lstrip('.').upper() or document.get('file_type', 'Demo'),
                "file_size": f"{size} B" if size else document.get('file_size', '—'),
                "embedding_status": document.get('embedding_status', 'unknown' if indexed else 'pending'),
                "vector_index_status": "indexed" if indexed else "pending", "progress": 100 if indexed else 0,
                "version": document.get('revision_id', document.get('version', 'unprocessed'))}

    def documents(self):
        return [self.document_view(item) for item in self.store.documents()]

    def chunk_view(self, chunk):
        return {"chunk_id": chunk.chunk_id, "document_id": chunk.doc_id, "chunk_content": chunk.content,
                 "chunk_title": chunk.metadata.get('section', chunk.chunk_id), "domain": self.legacy_domain(chunk.metadata.get('domain', '')),
                 "doc_type": chunk.metadata.get('doc_type', ''), "source_file": chunk.metadata.get('source', ''),
                 "variables": chunk.metadata.get('variables', []), "token_count": len(chunk.content)//2,
                 "embedding_status": "offline_indexed", "vector_index_status": "indexed",
                 "version": chunk.metadata.get('revision_id', ''), "score": None, "rerank_score": None,
                 "metadata": chunk.metadata, "data_source": chunk.metadata.get('data_source', 'local')}

    def chunks(self):
        self.engine.refresh()
        return [self.chunk_view(chunk) for chunk in self.engine.chunks]

    @staticmethod
    def legacy_domain(domain):
        return {"风电故障诊断": "wind_oam", "区域负荷预测": "load_forecast", "储能EMS": "storage_ems", "电气工程基础": "calc_logic"}.get(domain, domain)

    def process(self, document_id, config):
        document = self.store.get_document(document_id)
        if not document:
            raise FileNotFoundError('Document not found')
        path = document.get('local_path')
        if not path or not Path(path).is_file():
            raise ValueError('This demo record has no original file. Upload a real document to process it.')
        if digest(Path(path).read_bytes()) != document.get('source_sha256'):
            raise ValueError('Original SHA256 mismatch; processing refused and current revision retained')
        mode = {'general': 'recursive', 'qa': 'business_object', 'semantic': 'business_object'}.get(config.chunk_mode, config.chunk_mode)
        result = self.engine.ingest(IngestRequest(path=path, document_id=document_id, domain=document.get('domain', '电气工程基础'),
            doc_type=document.get('doc_type', 'general'), splitter=mode, chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap))
        chunks = [item for item in self.chunks() if item['document_id'] == document_id]
        current = self.store.get_document(document_id)
        if (not current or current.get('revision_id') != result['revision_id']
                or any(item['version'] != result['revision_id'] for item in chunks)):
            raise KnowledgeConflictError('Document changed after processing; reload current state')
        return {"document": self.document_view(current), "chunks": chunks, "ingestion": result}

    def delete(self, document_id):
        if self.store.get_document(document_id) is None:
            raise FileNotFoundError('Document not found')
        self.engine.remove_document(document_id)
        return {"deleted": document_id, "original_retained": True}

    def metadata(self):
        grouped = {}
        for chunk in self.chunks():
            for kind in ('domain', 'doc_type', 'fault_type'):
                value = chunk['metadata'].get(kind)
                if value:
                    row = grouped.setdefault((kind, str(value)), {"name": str(value), "type": kind, "docs": set(), "chunk_count": 0, "updated_at": '—'})
                    row['docs'].add(chunk['document_id']); row['chunk_count'] += 1
        return [{k:v for k,v in row.items() if k != 'docs'} | {"doc_count": len(row['docs'])} for row in grouped.values()]

    def vector_config(self):
        stats = self.engine.stats()
        return {"embedding": {"model": "hash-offline (demo)", "mode": "offline", "dimension": 256, "batchSize": 1, "status": "offline"},
                "vectorDb": {"engine": "SQLite cache + exact search", "indexName": "current_revision", "indexedChunks": stats['chunks'], "failedChunks": 0, "lastIndexTime": '—'},
                "bm25": {"enabled": True, "backend": "local bm25", "indexedDocs": stats['documents'], "indexedChunks": stats['chunks']},
                "hybrid": {"vectorTopK": 10, "bm25TopK": 10, "fusionMethod": "weighted", "finalTopK": 5},
                "rerank": {"model": "score-fallback (offline)", "enabled": True, "topN": 5, "scoreThreshold": 0}, "jobs": self.store.jobs()}

    def state(self, domains, runtime):
        documents, chunks = self.documents(), self.chunks()
        metadata, vector = self.metadata(), self.vector_config()
        knowledge = [{"id": item['document_id'], "title": item['title'], "domain": item['domain'], "category": item['doc_type'],
                      "sourceDoc": item.get('source_name', item['title']), "chunkCount": item['chunk_count'], "tags": [],
                      "status": "vectorized" if item['status'] == 'indexed' else item['status'], "updatedAt": item.get('updated_at', ''),
                      "data_source": item.get('data_source', 'local')} for item in documents]
        overview = {"kpis": [{"label": label, "value": count, "delta": "持久化知识库实时统计"} for label,count in
                    [('知识条目',len(knowledge)),('入库文档',len(documents)),('Chunk 数量',len(chunks)),('Metadata 标签',len(metadata)),('已索引 Chunk',len(chunks))]],
                    "pipelineStatus": [['Persistence','SQLite'], ['Retrieval','Offline / Configured']],
                    "recentDocumentJobs": vector['jobs'][:5], "recentTests": []}
        return {"domains": domains, "overview": overview, "knowledge": knowledge, "documents": documents,
                "chunks": chunks, "metadata": metadata, "vectorConfig": vector,
                "evaluation": {"datasets": [], "runs": [], "metrics": [], "failures": []}, "runtime": runtime,
                "reports": [], "uploadDir": str(self.store.asset_dir), "data_source": "persistent", "demo_records": sum(d.get('data_source')=='demo' for d in documents)}
