"""Incrementally maintain a bounded, query-scoped in-memory exact index."""
from app.indexes import BM25Index,VectorIndex,HybridIndex
from app.indexes.summary_index import SummaryIndex
from app.vectorstores import VectorStoreFactory
from app.storage.workbench_store import WorkbenchStore
from app.embeddings.embedding_factory import HashEmbedding


class IndexSnapshot:
    def __init__(self,embedding,vectorstore,summary=False):
        self.vector=VectorIndex(embedding,VectorStoreFactory.create(vectorstore))
        self.bm25=BM25Index();self.hybrid=HybridIndex(self.vector,self.bm25)
        self.summary=VectorIndex(embedding,VectorStoreFactory.create(vectorstore)) if summary else None
        self.signatures={}

    def sync(self,chunks,embedding,store,*,strict=True):
        signatures={c.chunk_id:c.model_dump_json() for c in chunks}
        changed=[c for c in chunks if self.signatures.get(c.chunk_id)!=signatures[c.chunk_id]]
        removed=set(self.signatures)-set(signatures)
        self.vector.embedding=embedding
        if self.summary:self.summary.embedding=embedding
        for identity in removed:
            self.vector.store.rows.pop(identity,None)
            if self.summary:self.summary.store.rows.pop('summary_'+identity,None)
        if hasattr(embedding,'prefetch'):embedding.prefetch([c.content for c in changed])
        self.vector.build(changed)
        if self.summary:self.summary.build(SummaryIndex().build(changed))
        if changed or removed or not self.signatures:self.bm25.build(chunks)
        if store and changed:
            underlying=getattr(embedding,'embedding',embedding)
            model='offline-hash-sha256-v1' if isinstance(underlying,HashEmbedding) else embedding.model_name
            WorkbenchStore(store).record_vectors(changed,underlying.fingerprint,model,embedding.dimension,'sqlite_exact',strict=strict)
        self.signatures=signatures
        return {'reused_chunks':len(chunks)-len(changed),'updated_chunks':len(changed),'removed_chunks':len(removed)}
