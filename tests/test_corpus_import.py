import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryOptions,QueryRequest,IngestRequest
from app.knowledge.corpus_import import COLLECTION,BASE_ID,CosmeticsImporter,preflight
from app.runtime.models import RuntimeAdapterError,_HTTPAdapter
from app.storage.knowledge_store import KnowledgeStore,digest


class CorpusImportTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='cosmetics-import-test-'))
        self.source=self.root/'source';self.collection=self.source/COLLECTION;self.collection.mkdir(parents=True)
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6CEAAAAASUVORK5CYII=')
        (self.collection/'one.png').write_bytes(raw)
        (self.collection/'one.txt').write_text('COSMETICS-ONLY 合成玻璃瓶来源介绍')
        self.record={'id':'one','file':'one.png','caption_file':'one.txt','metadata_file':'one.json','sha256':digest(raw),'bytes':len(raw),
                     'assigned_category':'玻璃瓶','width':1,'height':1,'format':'PNG','allow_cloud_upload':False}
        (self.collection/'one.json').write_text(json.dumps(self.record))
        self.catalog=self.collection/'总清单.jsonl';self.catalog.write_text(json.dumps(self.record)+'\n')
        self.train=self.source/'data/text-training/synthetic/train.messages.jsonl';self.train.parent.mkdir(parents=True)
        sample={'split':'train','id':'sample1','messages':[{'role':'system','content':'DO-NOT-INDEX-SYSTEM-POLICY'},
                {'role':'user','content':'SYNTHETIC-QA 怎么检查？'},{'role':'assistant','content':'这是合成训练参考回答。'}]}
        self.train.write_text(json.dumps(sample,ensure_ascii=False)+'\n')
        duplicate=self.source/'local_training/qwen-text-duplicate/train.messages.jsonl';duplicate.parent.mkdir(parents=True);duplicate.write_bytes(self.train.read_bytes())
        self.store=KnowledgeStore(self.root/'store');self.store.set_meta('initialized','1')
        self.importer=CosmeticsImporter(self.store,self.root/'reports')

    def query(self,base=None,configured=False):
        opts=QueryOptions(runtime_mode='configured' if configured else 'offline',retrieval_mode='bm25',metadata_router=False,
                          query_transformation=False,query_expansion=False,query_rewrite=False,rerank=False,compression=False,parent_recovery=False,
                          metadata_filter={'knowledge_base_id':base} if base else {})
        return ConfigurableRagEngine(self.store).query(QueryRequest(question='COSMETICS-ONLY',options=opts,generate_answer=False))

    def test_idempotent_resume_preserves_raw_and_exclusive_metadata(self):
        plan=preflight(self.source)
        self.assertEqual(len(plan['training']),1);self.assertEqual(len(plan['training'][0]['aliases']),2)
        first=self.importer.execute(plan,1);self.assertEqual(first['status'],'partial')
        result=self.importer.execute(plan);self.assertEqual(result['status'],'completed');self.assertEqual(result['reused'],1)
        self.assertEqual(len(self.store.documents()),2)
        before=self.store.snapshot()
        result=self.importer.execute(plan);self.assertEqual(result['reused'],2)
        self.assertEqual(before,self.store.snapshot())
        for doc in self.store.documents():
            self.assertEqual(doc['knowledge_base_id'],BASE_ID);self.assertFalse(doc['allow_cloud_upload'])
            self.assertEqual(digest(Path(doc['local_path']).read_bytes()),doc['source_sha256'])
        raw_training=next(d for d in self.store.documents() if d['doc_type']=='text_training_reference')
        self.assertIn('DO-NOT-INDEX',Path(raw_training['local_path']).read_text())
        self.assertFalse(any('DO-NOT-INDEX' in c['content'] for c in self.store.snapshot()[1]))

    def test_dedicated_collection_is_opt_in(self):
        self.importer.execute(preflight(self.source))
        self.assertEqual(self.query().reranked_docs,[])
        self.assertEqual(self.query('automotive').reranked_docs,[])
        self.assertTrue(self.query(BASE_ID).reranked_docs)

    def test_generic_reprocessing_cannot_remove_import_policy_or_index_system_messages(self):
        self.importer.execute(preflight(self.source))
        doc=self.store.documents()[0];before=self.store.snapshot()
        with self.assertRaisesRegex(ValueError,'dedicated importer'):
            ConfigurableRagEngine(self.store).ingest(IngestRequest(path=doc['local_path'],document_id=doc['document_id'],knowledge_base_id='manufacturing'))
        self.assertEqual(self.store.snapshot(),before)

    def test_changed_caption_is_a_conflict_without_overwriting_current_revision(self):
        self.importer.execute(preflight(self.source))
        before=self.store.snapshot()
        (self.collection/'one.txt').write_text('Changed source caption with the same image')
        result=self.importer.execute(preflight(self.source))
        self.assertEqual(len(result['failures']),1)
        self.assertEqual(result['failures'][0]['error_type'],'KnowledgeConflictError')
        self.assertEqual(self.store.snapshot(),before)

    def test_ordinary_upload_and_cli_inherit_private_space_policy(self):
        from app.knowledge.service import KnowledgeService
        from app.knowledge.workbench import WorkbenchService
        engine=ConfigurableRagEngine(self.store);knowledge=KnowledgeService(engine)
        document=knowledge.upload('private.md',b'COSMETICS-ONLY synthetic private upload',BASE_ID,'general',BASE_ID)
        self.assertFalse(document['allow_cloud_upload'])
        workbench=WorkbenchService(knowledge)
        workbench.process(document['document_id'],parser_mode='native')
        direct=self.root/'direct.md';direct.write_text('COSMETICS-ONLY direct synthetic CLI ingestion')
        engine.ingest(IngestRequest(path=str(direct),knowledge_base_id=BASE_ID))
        for chunk in self.store.snapshot()[1]:
            self.assertFalse(chunk['metadata']['allow_cloud_upload'])
            self.assertTrue(chunk['metadata']['exclusive_to_system'])
        self.assertEqual(self.query().reranked_docs,[])
        self.assertTrue(self.query(BASE_ID).reranked_docs)
        env={'RAG_CHAT_URL':'https://example.invalid/chat','RAG_CHAT_MODEL':'test-chat','RAG_CHAT_API_KEY':'unit-test',
             'RAG_EMBEDDING_URL':'https://example.invalid/embed','RAG_EMBEDDING_MODEL':'test-embed','RAG_EMBEDDING_API_KEY':'unit-test','RAG_EMBEDDING_DIMENSIONS':'512',
             'RAG_RERANK_URL':'https://example.invalid/rerank','RAG_RERANK_MODEL':'test-rerank','RAG_RERANK_API_KEY':'unit-test'}
        with patch.dict('os.environ',env,clear=True),patch.object(_HTTPAdapter,'_post',side_effect=AssertionError('Must not call network')) as outbound:
            with self.assertRaisesRegex(RuntimeAdapterError,'local_only_material'):
                workbench.rebuild(document['document_id'],'configured')
            outbound.assert_not_called()

    def test_changed_original_and_path_escape_fail_before_import(self):
        (self.collection/'one.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'):preflight(self.source)
        self.assertEqual(self.store.documents(),[])
        self.record['file']='../outside.png';self.catalog.write_text(json.dumps(self.record)+'\n')
        with self.assertRaisesRegex(ValueError,'escapes'):preflight(self.source)

    def test_evaluation_rows_and_deleted_documents_cannot_return(self):
        plan=preflight(self.source);self.importer.execute(plan)
        doc=self.store.documents()[0];self.store.remove_document(doc['document_id'])
        result=self.importer.execute(plan);self.assertEqual(result['status'],'failed')
        self.assertIsNone(self.store.get_document(doc['document_id']))
        data=json.loads(self.train.read_text());data['split']='test';self.train.write_text(json.dumps(data)+'\n')
        with self.assertRaisesRegex(ValueError,'Non-training split'):preflight(self.source)

    def test_remote_models_block_before_any_request_even_for_empty_collection(self):
        env={'RAG_CHAT_URL':'https://example.invalid/chat','RAG_CHAT_MODEL':'test-chat','RAG_CHAT_API_KEY':'unit-test',
             'RAG_EMBEDDING_URL':'https://example.invalid/embed','RAG_EMBEDDING_MODEL':'test-embed','RAG_EMBEDDING_API_KEY':'unit-test','RAG_EMBEDDING_DIMENSIONS':'512',
             'RAG_RERANK_URL':'https://example.invalid/rerank','RAG_RERANK_MODEL':'test-rerank','RAG_RERANK_API_KEY':'unit-test'}
        with patch.dict('os.environ',env,clear=True),patch.object(_HTTPAdapter,'_post',side_effect=AssertionError('Network request not allowed')) as outbound:
            with self.assertRaisesRegex(RuntimeAdapterError,'local_only_material'):self.query(BASE_ID,True)
            self.importer.execute(preflight(self.source))
            with self.assertRaisesRegex(RuntimeAdapterError,'local_only_material'):self.query(BASE_ID,True)
            outbound.assert_not_called()


if __name__=='__main__':unittest.main()
