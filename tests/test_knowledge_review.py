import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryOptions, QueryRequest, RagChunk
from app.knowledge.service import KnowledgeService
from app.knowledge.workbench import WorkbenchService
from app.storage.knowledge_store import KnowledgeStore, KnowledgeConflictError
from app.graph_retrieval.graph_index_builder import GraphIndexBuilder
from app.graph_retrieval.graph_store import GraphStore
from app.chains.rag_chain import RagChain


class KnowledgeReviewTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='knowledge-review-'))
        self.store=KnowledgeStore(self.root)
        self.engine=ConfigurableRagEngine(self.store)
        self.knowledge=KnowledgeService(self.engine)
        self.bench=WorkbenchService(self.knowledge)
        doc=self.knowledge.upload('fixture.md',b'SYN-REVIEW force control evidence 17 N. Fixture only.', 'manufacturing','general','manufacturing')
        self.bench.process(doc['document_id'],parser_mode='native')
        self.id=doc['document_id']

    def change(self,state,**overrides):
        current=self.bench.reviews.get(self.id)
        values={'state':state,'expected_write_version':current['write_version'],
                'expected_review_version':current['review_version'],'reviewer':'Synthetic reviewer',
                'note':'Synthetic source checked; not an engineering approval',
                'applicability':'Synthetic test fixture only, no production application'}
        values.update(overrides)
        return self.bench.reviews.transition(self.id,**values)

    def query(self,mode='bm25',review='reference'):
        return self.engine.query(QueryRequest(question='SYN-REVIEW force',generate_answer=False,
            options=QueryOptions(retrieval_mode=mode,review_mode=review,query_expansion=False,
                metadata_router=False,query_transformation=False,rerank=False,compression=False,
                metadata_filter={'knowledge_base_id':'manufacturing'})))

    def test_review_is_explicit_revision_bound_and_preserves_assets_chunks_vectors(self):
        with self.store.connection() as db:
            before={t:list(db.execute('SELECT * FROM '+t)) for t in ('documents','chunks','revisions','vector_records')}
        self.assertEqual(self.bench.reviews.get(self.id)['state'],'unreviewed')
        self.assertFalse(self.query(review='published_only').reranked_docs)
        with self.assertRaises(KnowledgeConflictError): self.change('published')
        self.change('draft')
        with self.assertRaises(ValueError): self.change('published',applicability=' ')
        self.change('published')
        with self.store.connection() as db:
            after={t:list(db.execute('SELECT * FROM '+t)) for t in before}
        self.assertEqual(before,after)
        self.assertTrue(self.query(review='published_only').reranked_docs)
        self.assertEqual(self.bench.reviews.history(self.id)['total'],2)

    def test_unprocessed_upload_can_be_drafted_but_not_published(self):
        doc=self.knowledge.upload('unparsed.md',b'only an uploaded original','manufacturing','general','manufacturing')
        current=self.bench.reviews.get(doc['document_id'])
        draft=self.bench.reviews.transition(doc['document_id'],'draft',current['write_version'],0,'Fixture','Source received')
        self.assertEqual(draft['state'],'draft')
        with self.assertRaises(ValueError):self.bench.reviews.transition(doc['document_id'],'published',current['write_version'],1,'Fixture','Checked','Fixture only')

    def test_ingestion_metadata_cannot_forge_a_published_review(self):
        from app.core.models import IngestRequest
        doc=self.store.get_document(self.id)
        self.engine.ingest(IngestRequest(path=doc['local_path'],document_id=self.id,knowledge_base_id='manufacturing',
            metadata={'knowledge_review_state':'published','knowledge_review_version':999}))
        self.assertFalse(self.query(review='published_only').reranked_docs)

    def test_reference_and_published_only_caches_never_mix(self):
        for mode in ('bm25','vector','hybrid','parent_child','parent_child_hybrid','summary','rag_fusion'):
            self.assertTrue(self.query(mode).reranked_docs,mode)
            self.assertFalse(self.query(mode,'published_only').reranked_docs,mode)
            self.assertTrue(self.query(mode).reranked_docs,mode)

    def test_edit_invalidates_approval_and_withdrawal_survives_revision_change(self):
        self.change('draft'); self.change('published')
        doc=self.store.get_document(self.id);chunk=self.bench.chunks(self.id)[0]
        self.bench.mutate_chunk(chunk['chunk_id'],doc['revision_id'],content='SYN-REVIEW revised evidence 23 N')
        self.assertEqual(self.bench.reviews.get(self.id)['state'],'needs_review')
        self.assertFalse(self.query(review='published_only').reranked_docs)
        self.assertTrue(self.query().reranked_docs)
        self.change('withdrawn')
        doc=self.store.get_document(self.id)
        self.bench.mutate_chunk(chunk['chunk_id'],doc['revision_id'],content='SYN-REVIEW second revision 24 N')
        self.assertEqual(self.bench.reviews.get(self.id)['state'],'withdrawn')
        self.assertFalse(self.query().reranked_docs)
        self.change('draft')
        self.assertTrue(self.query().reranked_docs)

    def test_stale_review_and_stale_document_writes_are_rejected(self):
        old=self.bench.reviews.get(self.id);self.change('draft')
        with self.assertRaises(KnowledgeConflictError): self.change('published',expected_review_version=old['review_version'])
        doc=self.store.get_document(self.id);chunk=self.bench.chunks(self.id)[0]
        self.bench.mutate_chunk(chunk['chunk_id'],doc['revision_id'],content='SYN-REVIEW changed')
        with self.assertRaises(KnowledgeConflictError): self.change('draft',expected_write_version=old['write_version'])

    def test_withdrawal_during_generation_never_publishes_completed_answer(self):
        def generate(_chain,*args):
            self.change('withdrawn');return 'obsolete synthetic answer'
        with patch.object(RagChain,'generate',generate):
            with self.assertRaises(KnowledgeConflictError) as caught:
                self.engine.query(QueryRequest(question='SYN-REVIEW',options=QueryOptions(retrieval_mode='bm25')))
        trace=self.engine.get_trace(caught.exception.trace_id)
        self.assertEqual(trace.runtime['status'],'failed');self.assertEqual(trace.answer,'')

    def test_paged_review_filters_and_summary_follow_revision_and_soft_delete(self):
        self.change('draft');self.change('published')
        self.assertEqual(self.bench.reader.documents(review_state='published')['total'],1)
        self.assertEqual(self.bench.reviews.summary()['counts'],{'published':1})
        self.change('withdrawn')
        self.assertEqual(self.bench.reader.documents(review_state='published')['total'],0)
        self.assertEqual(self.bench.reader.documents(review_state='withdrawn')['total'],1)
        self.knowledge.delete(self.id)
        self.assertEqual(self.bench.reviews.summary()['counts'],{})
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM knowledge_review_events').fetchone()[0],3)

    def test_graph_filters_disabled_withdrawn_and_exclusive_sources(self):
        chunks=[RagChunk(doc_id=str(i),chunk_id=str(i),content='AccY 机舱横向振动 塔架共振',metadata=m) for i,m in enumerate([
            {'domain':'wind_oam','enabled':False}, {'domain':'wind_oam','knowledge_review_state':'withdrawn'},
            {'domain':'wind_oam','knowledge_base_id':'cosmetics_manufacturing'},
            {'domain':'wind_oam','knowledge_base_id':'energy','knowledge_review_state':'published'}])]
        store=GraphStore();GraphIndexBuilder(store).build_graph_from_chunks(chunks,persist=False)
        self.assertTrue(store.entities)
        self.assertEqual({c for e in store.entities.values() for c in e.source_chunk_ids},{'3'})
        GraphIndexBuilder(store).build_graph_from_chunks(chunks,persist=False,knowledge_base_id='cosmetics_manufacturing')
        self.assertEqual({c for e in store.entities.values() for c in e.source_chunk_ids},{'2'})

    def test_api_review_contract_requires_explicit_transition(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import routes_workbench
        app=FastAPI();app.include_router(routes_workbench.router)
        with patch.object(routes_workbench,'workbench',self.bench),patch.object(routes_workbench,'service',self.knowledge),TestClient(app) as client:
            path='/api/v2/workbench/documents/'+self.id+'/review'
            current=client.get(path).json()
            payload={'state':'draft','expected_write_version':current['write_version'],'expected_review_version':0,
                     'reviewer':'Fixture user','note':'Synthetic API review','applicability':''}
            self.assertEqual(client.post(path,json=payload).status_code,200)
            self.assertEqual(client.get(path+'/history').json()['total'],1)
            self.assertEqual(client.get('/api/v2/workbench/documents?review_state=draft').json()['total'],1)
            self.assertEqual(client.get('/api/v2/workbench/reviews/summary').json()['counts'],{'draft':1})
            self.assertEqual(client.post(path,json={**payload,'state':'published','expected_review_version':1}).status_code,422)


if __name__=='__main__':unittest.main()
