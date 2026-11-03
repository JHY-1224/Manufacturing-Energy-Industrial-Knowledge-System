import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryOptions,QueryRequest,IngestRequest,RagChunk
from app.knowledge.service import KnowledgeService
from app.knowledge.workbench import WorkbenchService
from app.runtime import RuntimeSession,RuntimeAdapterError
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError,digest
from app.tasks.service import BackgroundTasks


class BackgroundTaskTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='p2-background-'));self.store=KnowledgeStore(self.root)
        self.engine=ConfigurableRagEngine(self.store);self.knowledge=KnowledgeService(self.engine);self.workbench=WorkbenchService(self.knowledge)
        self.worker=BackgroundTasks(self.store);self.tasks=self.worker.tasks

    def seed(self,count=3):
        for i in range(count):
            doc=self.knowledge.upload(f'fixture-{i}.md',f'SYNTH-{i} 合成检查编号 {i}。'.encode(),'manufacturing','general','manufacturing')
            self.workbench.process(doc['document_id'],parser_mode='native')

    def create(self,kind='index',**kw):return self.worker.create(kind,runtime_mode='offline',**kw)
    def run_task(self):
        task=self.tasks.claim(self.worker.token);self.assertIsNotNone(task);self.worker._run(task);return self.tasks.get(task['task_id'])

    def test_invalid_cached_vector_fails_without_publishing_ready_replica(self):
        self.seed(1);job=self.create()
        item=self.tasks.items(job['task_id'])['items'][0]
        with self.store.connection() as db:
            db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?)',(item['cache_key'],json.dumps([0.0]*256)))
        result=self.run_task()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['counts'],{'failed':1})
        self.assertIn('invalid_cached_vector',result['error'])

    def test_dedupe_and_cross_worker_exclusion_and_resume(self):
        self.seed(3);job=self.create();same=self.create();self.assertEqual(job['task_id'],same['task_id']);self.assertTrue(same['reused'])
        claimed=self.tasks.claim(self.worker.token);self.assertIsNone(BackgroundTasks(self.store).tasks.claim('other'))
        item=self.tasks.take(job['task_id'],self.worker.token,1)
        self.worker._index(claimed,item,RuntimeSession.from_env('offline'))
        self.tasks.control(job['task_id'],'pause');self.assertTrue(self.tasks.release(job['task_id'],self.worker.token))
        self.assertEqual(self.tasks.get(job['task_id'])['counts'],{'completed':1,'pending':2})
        self.tasks.control(job['task_id'],'resume');done=self.run_task();self.assertEqual(done['status'],'completed');self.assertEqual(done['counts'],{'completed':3})
        with self.assertRaises(KnowledgeConflictError):self.tasks.control(job['task_id'],'resume')

    def test_process_exit_recovers_current_item_without_duplicate_records(self):
        self.seed(2);job=self.create();self.tasks.claim('dead');self.tasks.take(job['task_id'],'dead',1)
        with patch.object(self.store,'_owner_is_alive',return_value=False):claimed=self.tasks.claim(self.worker.token)
        self.worker._run(claimed);done=self.tasks.get(job['task_id']);self.assertEqual(done['status'],'completed')
        self.assertEqual(len(self.store.snapshot()[1]),2)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM vector_records').fetchone()[0],2)
        self.assertEqual(self.tasks.items(job['task_id'])['items'][0]['attempts'],2)

    def test_cancel_retains_completed_items_and_all_originals(self):
        self.seed(2);before=self.store.snapshot();job=self.create();self.tasks.control(job['task_id'],'cancel')
        self.assertIsNone(self.tasks.claim(self.worker.token));self.assertEqual(self.tasks.get(job['task_id'])['status'],'cancelled')
        self.assertEqual(self.store.snapshot(),before)
        self.assertTrue(all(Path(d['local_path']).exists() for d in self.store.documents()))
        self.assertFalse(self.create()['reused'])

    def test_source_change_during_embedding_does_not_publish_stale_replica(self):
        self.seed(1);job=self.create();claimed=self.tasks.claim(self.worker.token);items=self.tasks.take(job['task_id'],self.worker.token)
        chunk=self.store.snapshot()[1][0]
        self.workbench.mutate_chunk(chunk['chunk_id'],chunk['metadata']['revision_id'],content='SYNTH-NEW 合成修订后的文本')
        self.worker._index(claimed,items,RuntimeSession.from_env('offline'));self.tasks.release(job['task_id'],self.worker.token)
        self.assertEqual(self.tasks.get(job['task_id'])['status'],'partial')
        self.assertEqual(self.tasks.items(job['task_id'])['items'][0]['status'],'stale')
        records=self.workbench.records.vector_records(chunk['chunk_id']);self.assertTrue(all(r['revision_id']!=chunk['metadata']['revision_id'] for r in records))
        newer=self.create();self.assertNotEqual(newer['task_id'],job['task_id']);self.assertEqual(self.run_task()['status'],'completed')

    def test_edit_in_flight_and_changed_model_do_not_publish_old_vector(self):
        self.seed(1);job=self.create();claimed=self.tasks.claim(self.worker.token);items=self.tasks.take(job['task_id'],self.worker.token)
        runtime=RuntimeSession.from_env('offline');original=runtime.embedding.embed
        chunk=self.store.snapshot()[1][0]
        def mutate_during_call(text):
            vector=original(text)
            self.workbench.mutate_chunk(chunk['chunk_id'],chunk['metadata']['revision_id'],enabled=False)
            return vector
        with patch.object(runtime.embedding,'embed',side_effect=mutate_during_call):self.worker._index(claimed,items,runtime)
        self.tasks.release(job['task_id'],self.worker.token)
        self.assertEqual(self.tasks.get(job['task_id'])['counts'],{'stale':1})
        self.assertEqual(self.workbench.records.vector_records(chunk['chunk_id']),[])

    def test_failed_batch_stops_and_retry_only_runs_remaining_items(self):
        self.seed(10);job=self.create();claimed=self.tasks.claim(self.worker.token);items=self.tasks.take(job['task_id'],self.worker.token,1)
        runtime=RuntimeSession.from_env('offline');self.worker._index(claimed,items,runtime)
        # Separate fingerprint makes the remaining 9 items uncached for this fixture.
        with patch.object(self.worker,'_index',side_effect=RuntimeAdapterError('transport_error','embedding')):self.worker._run(claimed)
        failed=self.tasks.get(job['task_id']);self.assertEqual(failed['status'],'failed');self.assertEqual(failed['counts']['completed'],1)
        self.tasks.control(job['task_id'],'retry');self.assertEqual(self.run_task()['counts'],{'completed':10})

    def test_parse_queue_recovery_after_commit_keeps_revision(self):
        doc=self.knowledge.upload('async.md',b'SYNTHETIC QUEUE process document','manufacturing','general','manufacturing')
        job=self.create('process',document_ids=[doc['document_id']],process_options={'parser_mode':'native'})
        claimed=self.tasks.claim(self.worker.token);item=self.tasks.take(job['task_id'],self.worker.token,1)[0]
        with patch.object(self.tasks,'publish',side_effect=RuntimeError('simulate exit after commit')):
            with self.assertRaises(RuntimeError):self.worker._process(claimed,item)
        after=self.store.get_document(doc['document_id']);self.assertEqual(after['status'],'indexed')
        with patch.object(self.store,'_owner_is_alive',return_value=False):recovered=self.tasks.claim('recovered')
        self.worker.token='recovered';self.worker._run(recovered)
        self.assertEqual(self.store.get_document(doc['document_id'])['revision_id'],after['revision_id'])
        self.assertTrue(self.tasks.items(job['task_id'])['items'][0]['output']['recovered_committed_result'])

    def test_parse_checks_snapshot_version_and_original_hash(self):
        doc=self.knowledge.upload('tampered.md',b'SYNTHETIC original','manufacturing','general','manufacturing')
        job=self.create('process',document_ids=[doc['document_id']],process_options={'parser_mode':'native'})
        Path(doc['local_path']).write_bytes(b'SYNTHETIC tampered fixture')
        done=self.run_task();self.assertEqual(done['status'],'partial');self.assertEqual(done['counts'],{'failed':1})
        self.assertEqual(self.store.snapshot()[1],[])

    def test_model_identity_change_fails_before_embedding(self):
        self.seed(1);job=self.create();runtime=RuntimeSession.from_env('offline');runtime.embedding.fingerprint='changed'
        with patch.object(self.worker,'runtime_factory',return_value=runtime),patch.object(runtime.embedding,'embed',side_effect=AssertionError('must not call')):
            self.assertEqual(self.run_task()['status'],'failed')
        self.assertEqual(self.tasks.get(job['task_id'])['counts'],{'pending':1})

    def test_page_counts_and_preview_avoid_full_snapshot(self):
        self.seed(12)
        with patch.object(self.store,'snapshot',side_effect=AssertionError('no full snapshot')),patch.object(self.store,'documents',side_effect=AssertionError('no full documents')):
            summary=self.workbench.summary();self.assertEqual(summary['document_count'],12)
            a=self.workbench.reader.documents(1,5);b=self.workbench.reader.documents(2,5)
            self.assertEqual(a['total'],12);self.assertEqual(len(a['items']),5)
            self.assertFalse({d['document_id'] for d in a['items']} & {d['document_id'] for d in b['items']})
            self.assertEqual(self.workbench.reader.documents(q="%' OR 1=1 --")['total'],0)
            c=self.workbench.reader.chunks(2,5);self.assertEqual(c['total'],12);self.assertEqual(len(c['items']),5)
            self.assertEqual(self.workbench.reader.chunks(knowledge_base_id='automotive')['total'],0)

    def test_query_reuses_index_and_only_changed_content_reembeds(self):
        self.seed(3);options=QueryOptions(retrieval_mode='hybrid',metadata_router=False,query_transformation=False,query_expansion=False,query_rewrite=False,rerank=False,compression=False,parent_recovery=False,metadata_filter={'knowledge_base_id':'manufacturing'})
        first=self.engine.query(QueryRequest(question='SYNTH',options=options,generate_answer=False))
        second=self.engine.query(QueryRequest(question='SYNTH',options=options,generate_answer=False))
        self.assertEqual(second.runtime['index_cache']['updated_chunks'],0);self.assertEqual(second.runtime['index_cache']['reused_chunks'],3)
        c=self.store.snapshot()[1][0];self.workbench.mutate_chunk(c['chunk_id'],c['metadata']['revision_id'],content='CHANGED-ONLY synthetic')
        third=self.engine.query(QueryRequest(question='CHANGED-ONLY',options=options,generate_answer=False))
        self.assertEqual(third.runtime['index_cache']['updated_chunks'],1);self.assertEqual(third.runtime['index_cache']['reused_chunks'],2)
        self.assertEqual(self.engine.query(QueryRequest(question='CHANGED-ONLY',options=options,generate_answer=False)).runtime['index_cache']['updated_chunks'],0)

if __name__=='__main__':unittest.main()
