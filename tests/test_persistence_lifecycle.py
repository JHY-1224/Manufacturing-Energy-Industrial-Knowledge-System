import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.core.engine import ConfigurableRagEngine
from app.core.models import IngestRequest, QueryOptions, QueryRequest
from app.knowledge.service import KnowledgeService
from app.storage.knowledge_store import KnowledgeStore, digest


class PersistenceLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='p1-lifecycle-'))
        self.store = KnowledgeStore(self.root/'runtime')
        self.engine = ConfigurableRagEngine(self.store)
        self.service = KnowledgeService(self.engine)

    def source(self, content='制造试验站的合成参数是 12 千瓦。', suffix='.md'):
        path = self.root / ('source'+suffix)
        path.write_text(content, encoding='utf-8')
        return path

    def test_ingest_idempotency_and_restart_trace(self):
        request = IngestRequest(path=str(self.source()))
        first = self.engine.ingest(request)
        again = self.engine.ingest(request)
        self.assertTrue(again['reused'])
        self.assertEqual(first['chunk_ids'], again['chunk_ids'])
        trace = self.engine.query(QueryRequest(question='试验站参数是多少？'))
        restarted = ConfigurableRagEngine(KnowledgeStore(self.store.directory))
        self.assertEqual(restarted.get_trace(trace.trace_id).answer, trace.answer)
        self.assertEqual([c.chunk_id for c in restarted.chunks], first['chunk_ids'])
        self.assertEqual(len(self.store.jobs()), 1)

    def test_changed_source_replaces_current_revision_and_preserves_originals(self):
        path = self.source('旧版合成参数为 12 千瓦。')
        first = self.engine.ingest(IngestRequest(path=str(path)))
        old_doc = self.store.get_document(first['document_id'])
        original = Path(old_doc['local_path'])
        old_bytes = original.read_bytes()
        path.write_text('新版合成参数为 18 千瓦。', encoding='utf-8')
        second = self.engine.ingest(IngestRequest(path=str(path)))
        self.assertEqual(first['document_id'], second['document_id'])
        self.assertNotEqual(first['revision_id'], second['revision_id'])
        self.assertEqual(original.read_bytes(), old_bytes)
        self.assertFalse(set(first['chunk_ids']) & {c.chunk_id for c in self.engine.chunks})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 2)

    def test_parse_failure_retains_current_revision_and_records_failed_job(self):
        path = self.source(json.dumps({'messages':[{'role':'user','content':'合成问题'},{'role':'assistant','content':'合成答案'}]}), '.jsonl')
        first = self.engine.ingest(IngestRequest(path=str(path)))
        path.write_text('{invalid',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.engine.ingest(IngestRequest(path=str(path)))
        self.assertEqual(self.store.get_document(first['document_id'])['revision_id'], first['revision_id'])
        self.assertEqual(self.store.jobs()[0]['status'],'failed')
        self.assertEqual([c.chunk_id for c in self.engine.chunks],first['chunk_ids'])

    def test_legacy_upload_and_processing_use_entire_file(self):
        from backend.main import ProcessDocumentRequest
        content=('合成工艺说明。'*1800+'末尾证据TOKEN_END_12000').encode()
        doc=self.service.upload('large.md',content,'wind_oam','general')
        self.assertEqual(self.service.upload('large.md',content,'wind_oam','general')['document_id'],doc['document_id'])
        response=self.service.process(doc['document_id'],ProcessDocumentRequest())
        self.assertTrue(any('TOKEN_END_12000' in item['chunk_content'] for item in response['chunks']))
        self.assertTrue(any(c.parent_content for c in self.engine.chunks))
        self.assertEqual(digest(Path(doc['local_path']).read_bytes()),digest(content))

    def test_document_delete_does_not_resurrect_or_remove_original(self):
        from backend.main import ProcessDocumentRequest
        doc=self.service.upload('retained.md',b'synthetic retained evidence','wind_oam','general')
        self.service.process(doc['document_id'],ProcessDocumentRequest())
        self.service.delete(doc['document_id'])
        restarted=ConfigurableRagEngine(KnowledgeStore(self.store.directory))
        self.assertFalse(restarted.chunks)
        self.assertIsNone(self.store.get_document(doc['document_id']))
        self.assertTrue(Path(doc['local_path']).is_file())

    def test_metadata_hints_do_not_exclude_parent_child_variable_evidence(self):
        path=self.source('lag_96 表示前一日同一时刻负荷。')
        self.engine.ingest(IngestRequest(path=str(path),domain='区域负荷预测',splitter='parent_child'))
        trace=self.engine.query(QueryRequest(question='lag_96 在负荷预测中是什么意思？'))
        self.assertTrue(trace.reranked_docs)
        self.assertNotIn('chunk_type',trace.metadata_filter)
        self.assertIn('前一日',trace.answer)

    def test_explicit_filter_is_never_relaxed(self):
        self.engine.ingest(IngestRequest(path=str(self.source()),domain='wind_oam'))
        trace=self.engine.query(QueryRequest(question='负荷参数',options=QueryOptions(metadata_filter={'domain':'private_other_domain'})))
        self.assertEqual(trace.metadata_filter,{'domain':'private_other_domain'})
        self.assertFalse(trace.retrieved_docs)

    def test_disabled_router_does_not_apply_transformation_filter(self):
        self.engine.ingest(IngestRequest(path=str(self.source('SOC 是合成荷电状态。')),domain='wind_oam'))
        trace=self.engine.query(QueryRequest(question='SOC 是什么', options=QueryOptions(metadata_router=False,query_transformation=True)))
        self.assertEqual(trace.metadata_filter,{})
        self.assertTrue(trace.reranked_docs)

    def test_chatml_case_is_not_cut_into_multiple_chunks(self):
        content=json.dumps({'messages':[{'role':'assistant','content':'合成案例内容。'*2000}]})
        result=self.engine.ingest(IngestRequest(path=str(self.source(content,'.jsonl')),splitter='parent_child',chunk_size=200))
        self.assertEqual(result['chunk_count'],1)
        self.assertGreater(len(self.engine.chunks[0].content),12000)

    def test_real_mode_configuration_failure_is_saved_not_faked(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Exception) as error:
                self.engine.query(QueryRequest(question='合成问题',options=QueryOptions(runtime_mode='configured')))
        trace=self.engine.get_trace(error.exception.trace_id)
        self.assertEqual(trace.runtime['status'],'failed')
        self.assertEqual(trace.answer,'')

    def test_another_process_recovers_documents_and_trace(self):
        result=self.engine.ingest(IngestRequest(path=str(self.source())))
        trace=self.engine.query(QueryRequest(question='合成参数？'))
        code='''import json,sys
from app.storage.knowledge_store import KnowledgeStore
from app.core.engine import ConfigurableRagEngine
engine=ConfigurableRagEngine(KnowledgeStore(sys.argv[1]))
print(json.dumps({'documents':engine.stats()['documents'],'trace':engine.get_trace(sys.argv[2]).trace_id,'doc_ids':[c.doc_id for c in engine.chunks]}))'''
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONDONTWRITEBYTECODE':'1'}
        output=subprocess.check_output([sys.executable,'-B','-c',code,str(self.store.directory),trace.trace_id],env=env,cwd=self.root,text=True)
        recovered=json.loads(output)
        self.assertEqual(recovered['documents'],1)
        self.assertEqual(recovered['trace'],trace.trace_id)
        self.assertEqual(set(recovered['doc_ids']),{result['document_id']})

    def test_original_tamper_is_detected_before_reprocessing(self):
        from backend.main import ProcessDocumentRequest
        doc=self.service.upload('tamper.md',b'original synthetic','wind_oam','general')
        Path(doc['local_path']).write_bytes(b'changed synthetic test asset')
        with self.assertRaisesRegex(ValueError,'SHA256'):
            self.service.process(doc['document_id'],ProcessDocumentRequest())
