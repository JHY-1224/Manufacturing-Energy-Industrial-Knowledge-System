from __future__ import annotations

from collections import OrderedDict

import hashlib
import json
from pathlib import Path
from threading import RLock
from time import perf_counter
from uuid import uuid4

from app.config.loader import DEFAULT_CONFIG
from app.core.models import IngestRequest, QueryRequest, QueryTrace, RagChunk
from app.embeddings import EmbeddingFactory
from app.indexes import BM25Index, HybridIndex, VectorIndex
from app.indexes.summary_index import SummaryIndex
from app.loaders import get_loader
from app.knowledge.catalog import base_policy
from app.post_retrieval import ContextCompressor, Deduplicator, DocumentFilter, ParentContextRecover
from app.post_retrieval.rrf_fusion import RrfFusion
from app.pre_retrieval import MetadataRouter, QueryExpander, QueryRewriter
from app.pre_retrieval.hyde_generator import HydeGenerator
from app.preprocessors import DocumentCleaner, MetadataEnricher, TableExtractor
from app.runtime import RuntimeSession
from app.splitters import get_splitter
from app.storage.knowledge_store import KnowledgeConflictError, KnowledgeStore, digest, now
from app.preprocessors.section_builder import SectionBuilder, section_id
from app.storage.workbench_store import WorkbenchStore
from app.vectorstores import VectorStoreFactory
from app.storage.review_store import ReviewStore, available


DOMAIN_NAMES = {"wind_oam": "风电故障诊断", "load_forecast": "区域负荷预测", "storage_ems": "储能EMS", "calc_logic": "电气工程基础", "report_template": "电气工程基础"}


class CachedEmbedding:
    def __init__(self, embedding, store):
        self.embedding, self.store = embedding, store
        self.dimension = embedding.dimension
        self.model_name = embedding.model_name
        self.hits = 0
        self.memory = {}

    def prefetch(self,texts):
        if self.store:
            keys=[digest((self.embedding.fingerprint+"\0"+text).encode()) for text in texts]
            self.memory.update(self.store.get_vectors(keys))

    def embed(self, text):
        key = digest((self.embedding.fingerprint + "\0" + text).encode())
        cached = self.memory.get(key)
        if cached is None and self.store:cached=self.store.get_vector(key)
        if cached is not None:
            self.hits += 1
            return cached
        vector = self.embedding.embed(text)
        if self.store:
            self.store.save_vector(key, vector)
        self.memory[key]=vector
        return vector


class ConfigurableRagEngine:
    def __init__(self, store: KnowledgeStore | None = None) -> None:
        self.store = store
        self._lock = RLock()
        self._generation = -1
        self._index_cache=OrderedDict()
        self._bm25_cache=OrderedDict()
        self._evidence_cache=OrderedDict()
        self._last_index_stats={}
        self.chunks: list[RagChunk] = []
        self.traces: dict[str, QueryTrace] = OrderedDict()
        self.embedding_name, self.vectorstore_name = "bge-large-zh", "chroma"
        self.embedding = EmbeddingFactory.create(self.embedding_name)
        self.trace_dir = store.directory / "traces" if store else Path("data/traces")
        self.refresh()
        self.rebuild_indexes()

    def refresh(self):
        with self._lock:
            if self.store:
                if int(self.store.get_meta("generation"))==self._generation:return
                generation, chunks = self.store.snapshot()
                if generation != self._generation:
                    self.chunks = [RagChunk.model_validate(item) for item in chunks]
                    ReviewStore(self.store).decorate(self.chunks)
                    self._generation = generation
                    self.rebuild_indexes()

    def configure(self, embedding: str, vectorstore: str) -> None:
        # Compatibility for explicit offline configuration; queries freeze their own runtime.
        with self._lock:
            self.embedding_name, self.vectorstore_name = embedding, vectorstore
            self.embedding = EmbeddingFactory.create(embedding)
            self.rebuild_indexes()

    def _indexes(self, embedding, vectorstore, summary=False, chunks=None, scope='legacy_all'):
        from app.indexes.snapshot_cache import IndexSnapshot
        active=[c for c in (self.chunks if chunks is None else chunks) if available(c.metadata)]
        underlying=getattr(embedding,'embedding',embedding)
        key=(underlying.fingerprint,vectorstore,summary,scope)
        snapshot=self._index_cache.get(key)
        if snapshot is None:
            snapshot=IndexSnapshot(embedding,vectorstore,summary);self._index_cache[key]=snapshot
        self._index_cache.move_to_end(key)
        # A concurrent ingestion may advance a revision during startup/refresh.
        # Skip superseded replicas only in the compatibility view. Explicit
        # query/rebuild publication retains strict revision-conflict checks.
        self._last_index_stats=snapshot.sync(active,embedding,self.store,strict=scope!='legacy_all')
        while len(self._index_cache)>6:self._index_cache.popitem(last=False)
        return snapshot.vector,snapshot.bm25,snapshot.hybrid,snapshot.summary

    def rebuild_indexes(self) -> None:
        self.vector_index, self.bm25_index, self.hybrid_index, self.summary_vector_index = self._indexes(self.embedding, self.vectorstore_name)
        self.vector_store = self.vector_index.store

    def add_chunks(self, chunks: list[RagChunk]) -> None:
        with self._lock:
            if self.store:
                grouped = {}
                for chunk in chunks:
                    grouped.setdefault(chunk.doc_id, []).append(chunk)
                for doc_id, items in grouped.items():
                    document = self.store.get_document(doc_id) or self.store.register("seed:" + doc_id, {
                        "document_id": doc_id, "title": items[0].metadata.get("source", doc_id),
                        "domain": items[0].metadata.get("domain", "电气工程基础"),
                        "doc_type": items[0].metadata.get("doc_type", "general"), "data_source": "demo", "created_at": now()})
                    document.update(revision_id="rev_" + uuid4().hex, status="indexed", chunk_count=len(items), updated_at=now())
                    for chunk in items:
                        chunk.metadata.update(revision_id=document["revision_id"], data_source=document.get("data_source", "local"))
                    self.store.replace_document(document, [item.model_dump() for item in items], {"source": "bootstrap"})
                self.refresh()
            else:
                existing = {chunk.chunk_id: chunk for chunk in self.chunks}
                existing.update({chunk.chunk_id: chunk for chunk in chunks})
                self.chunks = list(existing.values())
                self._generation += 1
                self.rebuild_indexes()

    def remove_document(self, document_id: str) -> int:
        with self._lock:
            if self.store:
                removed = self.store.remove_document(document_id)
                self.refresh()
                return removed
            before = len(self.chunks)
            self.chunks = [chunk for chunk in self.chunks if chunk.doc_id != document_id]
            self._generation += 1
            self.rebuild_indexes()
            return before - len(self.chunks)

    def ingest(self, request: IngestRequest) -> dict:
        with self._lock:
            path = Path(request.path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError("Document not found")
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("Document exceeds 64 MiB")
            content = path.read_bytes()
            if len(content) > 64 * 1024 * 1024:
                raise ValueError("Document exceeds 64 MiB")
            sha = digest(content)
            source_key = "path:" + str(path)
            config = request.model_dump(exclude={"path", "document_id", "expected_write_version"})
            config['pipeline_revision']='sections-protected-blocks-v1'
            if path.suffix.lower()=='.docx' or request.parser_mode!='native':
                from app.preprocessors.structured_tables import VERSION as TABLE_PARSER_VERSION
                config['table_parser_revision']=TABLE_PARSER_VERSION
            if request.parser_mode!='native':
                ocr_source=Path(__file__).resolve().parents[2]/'scripts/local_model/vision_ocr.swift'
                config['ocr_runtime_revision']=digest(ocr_source.read_bytes())
            config_key = digest(json.dumps(config, sort_keys=True, ensure_ascii=False).encode())
            doc_id = request.document_id or "doc_" + digest(source_key.encode())[:24]
            document = None
            parse_path = path
            if self.store:
                document = self.store.get_document(doc_id) if request.document_id else self.store.find_source(source_key)
                if not document and not request.document_id:
                    aliases = [item for item in self.store.documents() if item.get("local_path") == str(path)]
                    if len(aliases) > 1:
                        raise ValueError("Shared original has multiple documents; specify document_id")
                    if aliases:
                        document = aliases[0]
                if request.document_id and document is None:
                    raise FileNotFoundError("Document not found")
                if request.expected_write_version and (not document or document.get('write_version')!=request.expected_write_version):
                    raise KnowledgeConflictError('Queued document version changed before processing')
                if document and document.get('doc_type') in {'image_source_reference','text_training_reference','technical_source_reference'}:
                    raise ValueError('Imported corpus references use their dedicated importer; rebuild the current index instead of reparsing originals')
                if document and document.get("status") == "indexed" and document.get("source_sha256") == sha and document.get("processing_key") == config_key:
                    self.refresh()
                    current = self.store.get_document(document["document_id"])
                    current_chunks = [c for c in self.chunks if c.doc_id == document["document_id"]]
                    if (not current or current.get("write_version") != document.get("write_version")
                            or any(c.metadata.get("revision_id") != document["revision_id"] for c in current_chunks)):
                        raise KnowledgeConflictError("Document changed while reusing its index; reload before retrying")
                    ids = [c.chunk_id for c in current_chunks]
                    return {"path": str(path), "document_id": document["document_id"], "revision_id": document["revision_id"], "chunk_count": len(ids), "chunk_ids": ids, "reused": True}
                asset_sha, asset_path = self.store.save_asset(content, document.get("source_name", path.name) if document else path.name)
                parse_path = Path(asset_path)
                if not document:
                    document = self.store.register(source_key, {"document_id": doc_id, "title": path.stem, "source_name": path.name,
                        "domain": request.domain, "doc_type": request.doc_type, "local_path": asset_path,
                        "source_sha256": asset_sha, "status": "uploaded", "chunk_count": 0,
                        "data_source": "local", "created_at": now(), "updated_at": now(), "size_bytes": len(content)})
                doc_id = document["document_id"]
            revision_id = "rev_" + uuid4().hex
            job_id = self.store.start_job(doc_id, {**config, "source_sha256": sha, "original_path": str(parse_path)}, expected_version=document.get("write_version")) if self.store else None
            try:
                generated = []
                splitter = get_splitter(request.splitter, request.chunk_size, request.chunk_overlap)
                if request.parser_mode == 'native':
                    loaded_documents = get_loader(parse_path).load(parse_path)
                else:
                    from app.loaders.ocr_router import RoutedLoader
                    loaded_documents = RoutedLoader(parse_path, request.parser_mode, self.store.directory if self.store else Path('data/runtime')).load(parse_path)
                sections = [section for loaded in loaded_documents for section in SectionBuilder().split(loaded)]
                for loaded_index, loaded in enumerate(sections, 1):
                    metadata = MetadataEnricher().enrich(document.get("source_name", loaded.source) if document else loaded.source,
                        {**loaded.metadata, **request.metadata, "domain": DOMAIN_NAMES.get(request.domain, request.domain), "doc_type": request.doc_type,
                         "revision_id": revision_id, "source_sha256": sha, "data_source": "local", 'document_id':doc_id,
                         'section_index':loaded_index, 'knowledge_base_id':request.knowledge_base_id or (document or {}).get('knowledge_base_id','energy')})
                    metadata.update(base_policy(metadata['knowledge_base_id']))
                    metadata['section_id'] = section_id(doc_id, revision_id, metadata)
                    cleaned = DocumentCleaner().clean(loaded.content)
                    if not cleaned:
                        continue
                    node_prefix = f"{doc_id}_{revision_id}_{loaded_index}"
                    if metadata.get("category") == "Case":
                        generated.append(RagChunk(doc_id=doc_id, chunk_id=node_prefix + "_case", content=cleaned, metadata={**metadata, "chunk_kind": "case"}))
                        continue
                    body, tables = TableExtractor().extract(cleaned, metadata.get("section", "未分节"))
                    source_tables = {t['table_id']: t for t in metadata.pop('structured_tables', [])}
                    text_chunks = splitter.split(node_prefix, body, metadata)
                    cursor = 0
                    for chunk in text_chunks:
                        chunk.doc_id = doc_id
                        chunk.metadata["chunk_kind"] = "child" if chunk.parent_content else "text"
                        if chunk.parent_content:
                            chunk.metadata["parent_content"] = chunk.parent_content
                        offset = body.find(chunk.content, cursor)
                        chunk.metadata.update(start_offset=offset if offset>=0 else None, end_offset=offset+len(chunk.content) if offset>=0 else None, offset_scope='section_text_after_block_protection')
                        if offset>=0: cursor=offset+1
                    generated.extend(text_chunks)
                    for index, table in enumerate(tables, 1):
                        layout = source_tables.get(table.source_table_id)
                        layout_meta = ({'source_table_id': layout['table_id'], 'table_layout': layout,
                                        'protected_structure': True, 'structure_review': layout['review_status']}
                                       if layout else {})
                        generated.append(RagChunk(doc_id=doc_id, chunk_id=node_prefix + f"_table_{index}", content=table.content,
                            metadata={**metadata, **layout_meta, "category": "Table" if table.kind=='table' else 'Figure', "chunk_kind": table.kind, "chunk_type": "表格" if table.kind=='table' else '图片块', "table_id": table.table_id if table.kind=='table' else None, 'figure_id':table.table_id if table.kind=='figure' else metadata.get('figure_id'), "table_section": table.section}))
                if not generated:
                    raise ValueError("No readable text was extracted; the original is retained")
                if self.store:
                    document = {**document, "domain": request.domain, "doc_type": request.doc_type, "local_path": asset_path,
                        "source_sha256": sha, "processing_key": config_key, "revision_id": revision_id, "status": "indexed",
                        "chunk_count": len(generated), "size_bytes": len(content), "updated_at": now(), "data_source": "local", "process_config": config,
                        'knowledge_base_id':request.knowledge_base_id or document.get('knowledge_base_id','energy'),
                        'parse_summary':{'sections':len(sections),'routes':sorted({s.metadata.get('parse_route','native') for s in sections}),
                                         'warnings':list(dict.fromkeys(s.metadata['warning'] for s in sections if s.metadata.get('warning')))}}
                    document.update(base_policy(document['knowledge_base_id']))
                    self.store.replace_document(document, [item.model_dump() for item in generated], config, job_id)
                    self.refresh()
                else:
                    self.chunks = [chunk for chunk in self.chunks if chunk.doc_id != doc_id] + generated
                    self._generation += 1
                    self.rebuild_indexes()
            except Exception as exc:
                if self.store:
                    self.store.finish_job(job_id, "failed", f"{type(exc).__name__}: document parsing/indexing failed; previous revision retained")
                raise
            return {"path": str(path), "document_id": doc_id, "revision_id": revision_id, "job_id": job_id,
                    "chunk_count": len(generated), "chunk_ids": [c.chunk_id for c in generated], "reused": False}

    def query(self, request: QueryRequest) -> QueryTrace:
        with self._lock:
            self.refresh()
            try:
                return self._query(request.model_copy(deep=True))
            except Exception as exc:
                trace = QueryTrace(original_query=request.question, rewritten_query=request.question,
                    retrieval_mode=request.options.retrieval_mode, options=request.options.model_dump(),
                    corpus_version=self._generation, runtime={"mode": request.options.runtime_mode, "status": "failed", "error_type": type(exc).__name__, "stage": getattr(exc, "stage", None), "code": getattr(exc, "code", None)},
                    warnings=["查询未完成，未采用回退答案。"])
                self.save_trace(trace)
                exc.trace_id = trace.trace_id
                raise

    def _query(self, request):
        started = perf_counter()
        options = request.options
        runtime = RuntimeSession.from_env(options.runtime_mode, embedding_name=options.embedding)
        embedding = CachedEmbedding(runtime.embedding, self.store)
        trace = QueryTrace(original_query=request.question, rewritten_query=request.question,
                           retrieval_mode=options.retrieval_mode, options=options.model_dump(), corpus_version=self._generation)
        pre_started = perf_counter()
        query = QueryRewriter().rewrite(request.question, request.conversation_context) if options.query_rewrite else request.question
        queries = QueryExpander().expand(query) if options.query_expansion or options.multi_query or options.rag_fusion or options.retrieval_mode == "rag_fusion" else [query]
        if options.hyde:
            queries.append(HydeGenerator().generate(query))
        from app.retrievers.table_retriever import TableRetriever
        table_retriever = TableRetriever(self.store)
        table_query, table_aliases = table_retriever.expand(query) if options.table_retrieval else (query, [])
        from app.retrievers.evidence_retriever import EvidenceRetriever
        evidence_retriever=EvidenceRetriever()
        _, evidence_aliases=evidence_retriever.expand(query) if options.evidence_retrieval else (query, [])
        if table_query != query:
            queries = list(dict.fromkeys([*queries, table_query]))
        rerank_query=table_query+ ('\n检索同义术语：'+'; '.join(evidence_aliases) if evidence_aliases else '')
        if evidence_aliases:
            queries=list(dict.fromkeys([*queries,rerank_query]))
        explicit = {k: v for k, v in options.metadata_filter.items() if v is not None and v != "" and v != "all"}
        # Both switches must permit automatic routing. Intent no longer dictates structural chunk_type.
        filters = MetadataRouter().route(query, explicit) if options.metadata_router and options.query_transformation else explicit
        if filters != explicit and not any(all(chunk.metadata.get(k) == v for k, v in filters.items()) for chunk in self.chunks):
            filters = explicit
            trace.warnings.append("自动领域提示无匹配，已保留显式过滤条件后检索。")
        trace.rewritten_query, trace.expanded_queries, trace.metadata_filter = query, queries, filters
        trace.latency_ms["pre_retrieval"] = round((perf_counter() - pre_started) * 1000, 3)
        # Dedicated system collections are opt-in. Filter before embedding so
        # an unrelated query cannot transmit their bytes to a remote adapter.
        scoped=[c for c in self.chunks if all(c.metadata.get(k)==v for k,v in filters.items())
                and (not (c.metadata.get('exclusive_to_system') or base_policy(c.metadata.get('knowledge_base_id')))
                     or filters.get('knowledge_base_id')==c.metadata.get('knowledge_base_id'))
                and available(c.metadata, options.review_mode)]
        from app.runtime.privacy import ensure_local_only
        from app.knowledge.catalog import KNOWLEDGE_BASES
        private_base=any(b['id']==filters.get('knowledge_base_id') and b.get('local_only') for b in KNOWLEDGE_BASES)
        ensure_local_only(runtime,scoped,require_local=private_base)
        translation='';translation_ms=0
        if options.cross_language_query and options.runtime_mode=='configured':
            from app.pre_retrieval.cross_language import should_translate
            if should_translate(query,scoped):
                ensure_local_only(runtime,scoped,require_local=True)
                translated_started=perf_counter()
                translation=runtime.chain.translate_query(query)
                translation_ms=round((perf_counter()-translated_started)*1000,3)
                queries=list(dict.fromkeys([*queries,translation]));rerank_query+='\n'+translation
        trace.expanded_queries=queries
        trace.latency_ms['query_translation']=translation_ms
        index_started = perf_counter()
        scope_key = json.dumps({'filters': filters, 'review_mode': options.review_mode}, sort_keys=True, ensure_ascii=False)
        if options.retrieval_mode == "bm25":
            scope=scope_key
            cached=self._bm25_cache.get(scope)
            if cached is None or cached[0]!=self._generation:
                bm25=BM25Index();bm25.build(scoped);self._bm25_cache[scope]=(self._generation,bm25)
                self._last_index_stats={'reused_chunks':0,'updated_chunks':len(scoped),'removed_chunks':0}
            else:
                bm25=cached[1];self._last_index_stats={'reused_chunks':len(scoped),'updated_chunks':0,'removed_chunks':0}
            self._bm25_cache.move_to_end(scope)
            while len(self._bm25_cache)>6:self._bm25_cache.popitem(last=False)
            vector = hybrid = summary = None
        else:
            vector, bm25, hybrid, summary = self._indexes(embedding, options.vectorstore, options.retrieval_mode == "summary", scoped, scope_key)
        trace.latency_ms["index_preparation"] = round((perf_counter() - index_started) * 1000, 3)
        def retrieve(candidate):
            if options.retrieval_mode == "vector": return vector.search(candidate, options.top_k, filters)
            if options.retrieval_mode == "bm25": return bm25.search(candidate, options.top_k, filters)
            if options.retrieval_mode == "summary": return summary.search(candidate, options.top_k, filters)
            return hybrid.search(candidate, options.top_k, options.vector_weight, options.bm25_weight, filters, options.fusion_method)
        retrieval_started = perf_counter()
        multi = options.multi_query or options.rag_fusion or options.retrieval_mode == "rag_fusion" or bool(translation)
        hits = RrfFusion().fuse([retrieve(candidate) for candidate in queries], options.top_k) if multi else retrieve(queries[-1])
        if options.evidence_retrieval:
            key=(self._generation,scope_key)
            if key not in self._evidence_cache:self._evidence_cache[key]=evidence_retriever
            evidence_retriever=self._evidence_cache[key]
            self._evidence_cache.move_to_end(key)
            while len(self._evidence_cache)>6:self._evidence_cache.popitem(last=False)
        evidence_hits=evidence_retriever.search(query+('\n'+translation if translation else ''),scoped,min(6,options.top_k)) if options.evidence_retrieval else []
        hits=evidence_retriever.merge(hits,evidence_hits,options.top_k)
        table_hits = table_retriever.search(table_query, scoped, min(6, options.top_k)) if options.table_retrieval else []
        hits = table_retriever.merge(hits, table_hits, options.top_k)
        if self.store:
            from app.knowledge.structure_resolution import StructureResolutions
            hits=StructureResolutions(self.store).guard_hits(hits)
        for rank, hit in enumerate(hits, 1):
            hit.original_rank = rank
            if options.retrieval_mode=='summary':hit.chunk_id=hit.metadata.get('source_chunk_id',hit.chunk_id)
        trace.retrieved_docs = [hit.model_copy(deep=True) for hit in hits]
        trace.latency_ms["retrieval"] = round((perf_counter() - retrieval_started) * 1000, 3)
        if options.deduplicate: hits = Deduplicator().deduplicate(hits)
        if options.document_filter: hits = DocumentFilter().filter(hits)
        if options.image_context:
            from app.post_retrieval.image_context import ImageContextRecover
            hits=ImageContextRecover().recover(query,hits,scoped)
            if options.deduplicate:hits=Deduplicator().deduplicate(hits)
        if options.parent_recovery or options.retrieval_mode in {"parent_child", "parent_child_hybrid"}:
            hits = ParentContextRecover().recover(hits)
            if options.deduplicate:
                seen = set(); unique = []
                for hit in hits:
                    key = (hit.doc_id, hit.content)
                    if key not in seen: unique.append(hit); seen.add(key)
                hits = unique
        rerank_started = perf_counter()
        rerank_count=len(hits) if options.table_retrieval or options.evidence_retrieval else options.final_top_k
        ranked=runtime.reranker.rerank(rerank_query,hits,rerank_count) if options.rerank and hits else hits
        if options.rerank and options.table_retrieval:
            ranked=table_retriever.prefer_checked_tables(ranked,table_query)
        if options.evidence_retrieval:
            ranked=evidence_retriever.prefer_literal_evidence(ranked,query,scoped)
        hits=ranked[:options.final_top_k]
        for rank, hit in enumerate(hits, 1): hit.final_rank = rank
        trace.reranked_docs = [hit.model_copy(deep=True) for hit in hits]
        trace.latency_ms["rerank"] = round((perf_counter() - rerank_started) * 1000, 3)
        compression_started = perf_counter()
        trace.compressed_context = [ContextCompressor().compress(query+('\n'+translation if translation else ''), hit.content, mode=options.compression_mode,
            preserve_structure=bool(hit.metadata.get('protected_structure') or hit.metadata.get('image_context_recovered')
                or hit.metadata.get('chunk_kind')=='source_caption'
                or (options.cross_language_query and hit.metadata.get('source_language') in {'en','zh'} and len(hit.content)<=6000))) if options.compression else hit.content for hit in hits]
        trace.compressed_context = [('【当前修订审核的适用范围与限制】\n' + hit.metadata['knowledge_applicability'] + '\n\n' + context)
            if hit.metadata.get('knowledge_review_state') == 'published' and hit.metadata.get('knowledge_applicability') else context
            for hit, context in zip(hits, trace.compressed_context)]
        trace.latency_ms["compression"] = round((perf_counter() - compression_started) * 1000, 3)
        generation_started = perf_counter()
        trace.answer = runtime.chain.generate(request.question, trace.compressed_context, [hit.chunk_id for hit in hits]) if request.generate_answer else ''
        trace.latency_ms["generation"] = round((perf_counter() - generation_started) * 1000, 3)
        trace.latency_ms["total"] = round((perf_counter() - started) * 1000, 3)
        trace.runtime = {**runtime.metadata(), "status": "completed", "vector_cache_hits": embedding.hits, "index_cache":dict(self._last_index_stats), "vector_storage": "sqlite_cache_exact_search" if self.store else "memory_exact_search"}
        if request.generate_answer and getattr(runtime.chain,'last_call',None):
            if runtime.chain.last_call.get('reason') in {'incomplete_named_table_evidence','source_table_lookup'}:
                trace.runtime['generation_evidence_check']=runtime.chain.last_call['reason']
        trace.runtime['table_retrieval'] = {'enabled': options.table_retrieval, 'aliases': table_aliases,
            'candidate_count': len(table_hits), 'evidence_text_unchanged': not any(h.metadata.get('derived_structure') or h.metadata.get('graphic_structure_only') for h in hits),
            'graphic_source_notes_added':sum(bool(h.metadata.get('graphic_structure_only')) for h in hits),
            'derived_structure_ids':[h.metadata['structure_resolution_id'] for h in hits if h.metadata.get('structure_resolution_id')]}
        trace.runtime['evidence_retrieval']={'enabled':options.evidence_retrieval,'aliases':evidence_aliases,
            'candidate_count':len(evidence_hits),'evidence_text_unchanged':True,'rerank_query':rerank_query,
            'window_index':evidence_retriever.last_cache_stats}
        trace.runtime['cross_language_query']={'enabled':options.cross_language_query,'translated_query':translation,'translation_is_not_evidence':True}
        trace.runtime['review_mode'] = options.review_mode
        if any(h.metadata.get('knowledge_review_state', 'unreviewed') != 'published' for h in hits):
            trace.warnings.append('检索包含未发布审核的来源参考，不能作为已批准的工艺结论。')
        if options.runtime_mode == "offline":
            trace.token_usage = {"input_tokens": sum(map(len, trace.compressed_context)) // 2, "output_tokens": len(trace.answer) // 2}
            trace.runtime["token_usage_kind"] = "character_estimate"
        else:
            chat_usage = next((call.get("usage") or {} for call in reversed(runtime.calls) if call["stage"] == "chat"), {})
            trace.token_usage = {k: v for k, v in {"input_tokens": chat_usage.get("prompt_tokens", chat_usage.get("input_tokens")), "output_tokens": chat_usage.get("completion_tokens", chat_usage.get("output_tokens"))}.items() if v is not None}
            trace.runtime["token_usage_kind"] = "provider_reported" if chat_usage else "unavailable"
        trace.cost = {"total": None}
        if self.store:
            trace.runtime['corpus_version_at_validation']=WorkbenchStore(self.store).assert_sources_current(trace.reranked_docs)
            trace.runtime['evidence_revision_checked']=True
        self.save_trace(trace)
        return trace

    def save_trace(self, trace: QueryTrace) -> None:
        if self.store:
            self.store.save_trace(trace.model_dump())
        else:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with (self.trace_dir / f"{trace.trace_id}.json").open("x", encoding="utf-8") as handle:
                json.dump(trace.model_dump(), handle, ensure_ascii=False, indent=2)
        self.traces[trace.trace_id] = trace
        # Durable traces remain in SQLite (or JSON files); bound only the hot cache.
        while len(self.traces)>256:self.traces.pop(next(iter(self.traces)))

    def get_trace(self, trace_id: str) -> QueryTrace | None:
        if trace_id in self.traces: return self.traces[trace_id]
        payload = self.store.get_trace(trace_id) if self.store else None
        return QueryTrace.model_validate(payload) if payload else None

    def stats(self) -> dict:
        with self._lock:
            self.refresh()
            return {"chunks": len(self.chunks), "documents": len({c.doc_id for c in self.chunks}),
                    "embedding": self.embedding_name, "embedding_dimension": self.embedding.dimension,
                    "vectorstore": self.vectorstore_name, "traces": self.store.trace_count() if self.store else len(self.traces), "default_config": DEFAULT_CONFIG,
                    "runtime_mode": "offline", "persistent": self.store is not None,
                    "corpus_version": self._generation, "supported_runtime_modes": ["offline", "configured"]}
