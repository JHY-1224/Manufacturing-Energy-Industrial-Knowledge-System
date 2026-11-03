import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryOptions, RagChunk, RetrievalHit
from app.knowledge.service import KnowledgeService
from app.knowledge.workbench import WorkbenchService
from app.storage.knowledge_store import KnowledgeStore, KnowledgeConflictError
from app.agent.service import AgentService


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='workbench-test-'))
        self.store=KnowledgeStore(self.root)
        self.store.set_meta('initialized','1')
        self.engine=ConfigurableRagEngine(self.store)
        self.service=KnowledgeService(self.engine)
        self.workbench=WorkbenchService(self.service)
        self.options=QueryOptions(retrieval_mode='bm25',query_rewrite=False,query_expansion=False,metadata_router=False,query_transformation=False,parent_recovery=False,compression=False,rerank=False)

    def ingest(self,content='# 合成规程\n\nALPHA 测试参数为 12。\n\n## 检验\n\nBETA 测试参数为 24。',base='manufacturing'):
        doc=self.service.upload('synthetic.md',content.encode(),base,'general',base)
        self.workbench.process(doc['document_id'])
        return self.store.get_document(doc['document_id'])

    def test_layered_records_edits_rebuild_restart_and_stale_writer(self):
        doc=self.ingest();chunks=self.workbench.chunks(doc['document_id'])
        self.assertEqual(len(self.workbench.records.sections(doc['document_id'])),2)
        self.assertTrue(all(c['vector_records'] for c in chunks))
        selected=chunks[0]
        self.workbench.mutate_chunk(selected['chunk_id'],doc['revision_id'],content='ALPHA 已人工修订为 36。')
        with self.assertRaises(KnowledgeConflictError):self.workbench.mutate_chunk(selected['chunk_id'],doc['revision_id'],content='过时写入')
        self.workbench.rebuild(doc['document_id'],'offline')
        restored=WorkbenchService(KnowledgeService(ConfigurableRagEngine(KnowledgeStore(self.root))))
        current=restored.chunks(doc['document_id'])
        self.assertIn('36',next(c for c in current if c['chunk_id']==selected['chunk_id'])['content'])
        self.assertEqual(len(restored.revisions(doc['document_id'])),2)
        self.assertEqual(Path(doc['local_path']).read_text(), '# 合成规程\n\nALPHA 测试参数为 12。\n\n## 检验\n\nBETA 测试参数为 24。')
        self.assertEqual(restored.source(doc['document_id'],doc['revision_id'])[0],Path(doc['local_path']))

    def test_disabled_or_deleted_chunks_never_reappear_in_retrieval(self):
        doc=self.ingest();chunk=self.workbench.chunks(doc['document_id'])[0]
        self.workbench.mutate_chunk(chunk['chunk_id'],doc['revision_id'],enabled=False)
        trace=self.workbench.retrieve('ALPHA',self.options)
        self.assertFalse(any(hit.chunk_id==chunk['chunk_id'] for hit in trace.reranked_docs))
        current=self.store.get_document(doc['document_id'])
        self.workbench.mutate_chunk(chunk['chunk_id'],current['revision_id'],delete=True)
        self.workbench.rebuild(doc['document_id'],'offline')
        self.assertNotIn(chunk['chunk_id'],[c['chunk_id'] for c in self.workbench.chunks()])
        self.service.delete(doc['document_id'])
        self.assertEqual(self.workbench.records.sections(doc['document_id']),[])
        self.assertTrue(Path(doc['local_path']).exists())

    def test_knowledge_base_filter_never_relaxes(self):
        self.ingest(base='energy')
        self.options.metadata_filter={'knowledge_base_id':'manufacturing'}
        self.assertEqual(self.workbench.retrieve('ALPHA',self.options).reranked_docs,[])

    def test_tampered_original_and_stale_vector_are_rejected(self):
        doc=self.ingest();old=RagChunk.model_validate(self.workbench.chunks()[0])
        self.workbench.mutate_chunk(old.chunk_id,doc['revision_id'],enabled=False)
        with self.assertRaises(KnowledgeConflictError):self.workbench.records.record_vectors([old],'fingerprint','model',256,'local')
        Path(doc['local_path']).write_text('synthetic tampering')
        with self.assertRaisesRegex(ValueError,'SHA256'):self.workbench.process(doc['document_id'])

    def test_agent_prefetch_sources_memory_persistence_and_idempotency(self):
        doc=self.ingest();session=self.workbench.records.create_session('manufacturing')
        def events(question,request):
            return [json.loads(frame.split('data: ',1)[1]) for frame in AgentService(self.workbench).stream(session['session_id'],request,question,self.options) if frame.startswith('event:')]
        first=events('ALPHA 测试参数是什么？','request_001')
        self.assertEqual(first[-1]['type'],'done',first)
        self.assertIn('12',first[-1]['answer'])
        self.assertTrue(first[-1]['sources'])
        self.assertLess([e['type'] for e in first].index('tool_end'),[e['type'] for e in first].index('answer'))
        with self.assertRaises(KnowledgeConflictError):list(AgentService(self.workbench).stream(session['session_id'],'request_001','重复',self.options))
        second=events('我刚问了什么？','request_002')
        self.assertEqual(second[-1]['route'],'conversation_memory')
        self.assertIn('ALPHA',second[-1]['answer'])
        self.assertEqual(second[-1]['sources'],[])
        restored=WorkbenchService(KnowledgeService(ConfigurableRagEngine(KnowledgeStore(self.root))))
        saved=restored.records.session(session['session_id'])
        self.assertEqual(len(saved['messages']),4)
        self.assertTrue(saved['events'])
        self.assertTrue(all(m['status']=='completed' for m in saved['messages']))

    def test_empty_domain_refuses_and_failed_model_records_status(self):
        session=self.workbench.records.create_session('automotive')
        frames=list(AgentService(self.workbench).stream(session['session_id'],'request_empty','ALPHA 是什么',self.options))
        last=json.loads(frames[-1].split('data: ',1)[1])
        self.assertEqual(last['type'],'done',last)
        self.assertEqual(last['sources'],[])
        self.ingest(base='automotive')
        self.options.runtime_mode='configured'
        with patch.dict('os.environ',{},clear=True):
            frames=list(AgentService(self.workbench).stream(session['session_id'],'request_failed','ALPHA',self.options))
        last=json.loads(frames[-1].split('data: ',1)[1])
        self.assertEqual(last['type'],'error')
        saved=self.workbench.records.session(session['session_id'])
        self.assertEqual(saved['messages'][-1]['status'],'failed')
        self.assertIsNone(saved['active_request'])

    def test_live_session_has_one_writer(self):
        session=self.workbench.records.create_session('manufacturing')
        self.workbench.records.begin_turn(session['session_id'],'request_live','问题')
        with self.assertRaises(KnowledgeConflictError):self.workbench.records.begin_turn(session['session_id'],'request_other','另一问题')
        with self.assertRaises(KnowledgeConflictError):self.workbench.records.archive_session(session['session_id'])

    def test_restart_recovers_dead_session_without_retrying_model(self):
        session=self.workbench.records.create_session('manufacturing')
        self.workbench.records.begin_turn(session['session_id'],'request_dead','合成中断')
        with patch.object(KnowledgeStore,'_owner_is_alive',return_value=False):
            store=KnowledgeStore(self.root)
        from app.storage.workbench_store import WorkbenchStore
        recovered=WorkbenchStore(store).session(session['session_id'])
        self.assertIsNone(recovered['active_request'])
        self.assertEqual(recovered['messages'][-1]['status'],'interrupted')
        self.assertEqual(recovered['messages'][-1]['content'],'')

    def test_existing_p1_store_gets_additive_current_record_migration(self):
        doc=self.ingest()
        with self.store.connection() as db:
            db.execute("DELETE FROM meta WHERE key='workbench_schema_v1'")
            db.execute('DELETE FROM sections')
            for row in db.execute('SELECT chunk_id,payload FROM chunks').fetchall():
                chunk=json.loads(row['payload']);chunk['metadata'].pop('knowledge_base_id',None)
                db.execute('UPDATE chunks SET payload=? WHERE chunk_id=?',(self.store.encode(chunk),row['chunk_id']))
        migrated=KnowledgeStore(self.root)
        self.assertTrue(WorkbenchService(KnowledgeService(ConfigurableRagEngine(migrated))).records.sections())
        self.assertTrue(all(c['metadata']['knowledge_base_id']=='manufacturing' for c in migrated.snapshot()[1]))
        self.assertEqual(len(self.workbench.revisions(doc['document_id'])),1)


class ParserAndFusionTests(unittest.TestCase):
    def test_rrf_keeps_raw_scores_and_sources(self):
        from app.indexes.hybrid_index import HybridIndex
        from unittest.mock import Mock
        def hit(id,v=0,b=0):return RetrievalHit(doc_id='d',chunk_id=id,content=id,score=0,vector_score=v,bm25_score=b)
        vector=Mock();bm25=Mock()
        vector.search.return_value=[hit('a',v=.8),hit('b',v=.7)]
        bm25.search.return_value=[hit('b',b=4),hit('c',b=2)]
        hits=HybridIndex(vector,bm25).search('x',3,fusion_method='rrf')
        self.assertEqual(hits[0].chunk_id,'b')
        self.assertAlmostEqual(hits[0].fusion_score,1/62+1/61)
        self.assertEqual(hits[0].vector_score,.7)
        self.assertEqual(hits[0].bm25_score,4)
        self.assertEqual(hits[0].retrieval_sources,['vector','bm25'])

    def test_tables_and_images_are_protected_and_headers_are_sections(self):
        from app.preprocessors.table_extractor import TableExtractor
        from app.preprocessors.section_builder import SectionBuilder
        from app.loaders.base_loader import LoadedDocument
        text='# 工艺\n正文\n<table><tr><td>参数</td><td>12</td></tr></table>\n<img src="local.png" alt="合成图">\n## 检查\n完成'
        sections=SectionBuilder().split(LoadedDocument(text,'test.html',{}))
        self.assertEqual(len(sections),2)
        body,blocks=TableExtractor().extract(sections[0].content)
        self.assertEqual([b.kind for b in blocks],['table','figure'])
        self.assertNotIn('<td>',body)

    def test_image_only_docx_uses_ocr_and_empty_image_is_not_evidence(self):
        from app.loaders.ocr_router import RoutedLoader, NativeVisionOcr
        from docx import Document
        import fitz
        root=Path(tempfile.mkdtemp(prefix='ocr-routing-'))
        path=root/'image.png'
        pixels=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,300,100),False);pixels.clear_with(255);pixels.save(path)
        doc=Document();doc.add_picture(str(path));doc.save(root/'image.docx')
        with patch.object(NativeVisionOcr,'recognize',return_value={'text':'','regions':[]}):
            loaded=RoutedLoader(root/'image.docx','auto',root).load(root/'image.docx')
        self.assertEqual(len(loaded),1)
        self.assertFalse(loaded[0].metadata['retrievable'])
        self.assertEqual(loaded[0].metadata['parse_route'],'ocr')


if __name__=='__main__':unittest.main()
