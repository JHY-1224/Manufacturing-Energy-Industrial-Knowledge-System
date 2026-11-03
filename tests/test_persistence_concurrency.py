"""Only synthetic assets in newly created temporary directories; no business DB."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.storage.knowledge_store import KnowledgeConflictError, KnowledgeStore, digest
from app.core.engine import ConfigurableRagEngine
from app.core.models import IngestRequest
from app.knowledge.service import KnowledgeService


class PersistenceConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='rag-persistence-concurrency-'))
        self.store = KnowledgeStore(self.root)

    def document(self, key='synthetic'):
        return self.store.register(key, {'document_id': key, 'title': key})

    def revision(self, document, text='synthetic revision'):
        revision_id = 'rev_' + uuid4().hex
        document = {**document, 'revision_id': revision_id, 'status': 'indexed', 'chunk_count': 1}
        chunks = [{'doc_id': document['document_id'], 'chunk_id': 'chunk_' + uuid4().hex,
                   'content': text, 'metadata': {'revision_id': revision_id}}]
        return document, chunks

    def test_interrupted_partial_write_never_publishes_final_asset(self):
        original = tempfile.NamedTemporaryFile

        class InterruptedWrite:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            @property
            def name(self): return self.handle.name
            def write(self, content):
                self.handle.write(content[:3])
                raise OSError('synthetic disk interruption')

        content = b'complete synthetic original'
        final = self.store.asset_dir / (digest(content) + '.txt')
        with patch('app.storage.knowledge_store.tempfile.NamedTemporaryFile', side_effect=lambda *a, **kw: InterruptedWrite(original(*a, **kw))):
            with self.assertRaises(OSError):
                self.store.save_asset(content, 'sample.txt')
        self.assertFalse(final.exists())
        self.assertEqual(len(list(self.store.asset_dir.glob('.staging-*'))), 1)
        _, path = self.store.save_asset(content, 'sample.txt')
        self.assertEqual(Path(path).read_bytes(), content)

    def test_concurrent_asset_publish_is_idempotent(self):
        content = b'synthetic concurrently published original' * 1000
        barrier = Barrier(2)
        def publish():
            barrier.wait()
            return self.store.save_asset(content, 'sample.txt')
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result() for future in [executor.submit(publish), executor.submit(publish)]]
        self.assertEqual(results[0], results[1])
        self.assertEqual(Path(results[0][1]).read_bytes(), content)

    def test_existing_corrupt_asset_is_never_overwritten(self):
        content = b'expected synthetic bytes'
        final = self.store.asset_dir / (digest(content) + '.txt')
        final.write_bytes(b'preserved corrupt evidence')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.store.save_asset(content, 'sample.txt')
        self.assertEqual(final.read_bytes(), b'preserved corrupt evidence')

    def test_competing_snapshot_writers_cannot_lose_an_update(self):
        initial = self.document()
        peer = KnowledgeStore(self.root)
        barrier = Barrier(2)
        def commit(store, label):
            document, chunks = self.revision(initial, label)
            barrier.wait()
            try:
                store.replace_document(document, chunks, {}, expected_version=initial['write_version'])
                return document['revision_id']
            except KnowledgeConflictError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(commit, self.store, 'A'), executor.submit(commit, peer, 'B')]
            results = [future.result() for future in futures]
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertIn(self.store.get_document(initial['document_id'])['revision_id'], results)
        self.assertEqual(len(self.store.snapshot()[1]), 1)

    def test_delete_recreate_aba_cannot_accept_old_writer(self):
        initial = self.document()
        job = self.store.start_job(initial['document_id'], {}, expected_version=initial['write_version'])
        document, chunks = self.revision(initial)
        self.store.remove_document(initial['document_id'])
        recreated = self.document()
        self.assertNotEqual(initial['write_version'], recreated['write_version'])
        with self.assertRaises(KnowledgeConflictError):
            self.store.replace_document(document, chunks, {}, job, expected_version=initial['write_version'])
        self.assertFalse(self.store.snapshot()[1])
        self.assertEqual(self.store.jobs()[0]['status'], 'cancelled')

    def test_same_document_has_one_active_job_across_store_instances(self):
        document = self.document()
        job = self.store.start_job(document['document_id'], {}, expected_version=document['write_version'])
        peer = KnowledgeStore(self.root)
        with self.assertRaises(KnowledgeConflictError):
            peer.start_job(document['document_id'], {}, expected_version=document['write_version'])
        self.assertEqual(peer.jobs()[0]['owner_pid'], os.getpid())
        self.assertEqual(peer.jobs()[0]['status'], 'running')
        updated, chunks = self.revision(document)
        self.store.replace_document(updated, chunks, {}, job, expected_version=document['write_version'])
        self.store.finish_job(job, 'failed', 'must not overwrite completed transaction')
        self.assertEqual(self.store.jobs()[0]['status'], 'completed')

    def test_live_owner_preserved_and_dead_owner_recovered_on_restart(self):
        document = self.document()
        worker = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        try:
            with patch('app.storage.knowledge_store.os.getpid', return_value=worker.pid):
                self.store.start_job(document['document_id'], {}, expected_version=document['write_version'])
            self.assertEqual(KnowledgeStore(self.root).jobs()[0]['status'], 'running')
        finally:
            worker.stdin.close()
            worker.wait(timeout=10)
        restarted = KnowledgeStore(self.root)
        self.assertEqual(restarted.jobs()[0]['status'], 'interrupted')
        self.assertEqual(restarted.get_document(document['document_id'])['write_version'], document['write_version'])

    def test_bootstrap_is_atomic_and_keeps_document_chunk_provenance_aligned(self):
        initial = {'document_id': 'seed', 'title': 'Synthetic seed'}
        document, chunks = self.revision(initial)
        peer = KnowledgeStore(self.root)
        barrier = Barrier(2)
        def bootstrap(store):
            barrier.wait()
            return store.bootstrap_documents([(document, chunks)])
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(bootstrap, self.store), executor.submit(bootstrap, peer)]
            self.assertEqual(sorted(f.result() for f in futures), [False, True])
        current = self.store.get_document('seed')
        chunk = self.store.snapshot()[1][0]
        self.assertEqual(chunk['metadata']['revision_id'], current['revision_id'])
        self.assertEqual(chunk['metadata']['data_source'], 'demo')
        self.store.remove_document('seed')
        self.assertFalse(peer.bootstrap_documents([(document, chunks)]))
        self.assertIsNone(peer.get_document('seed'))

    def test_bootstrap_failure_rolls_back_all_seeds_and_marker(self):
        first, chunks = self.revision({'document_id': 'seed1', 'title': 'Synthetic first'})
        second = {**first, 'document_id': 'seed2', 'revision_id': 'rev_other'}
        with self.assertRaises(Exception):
            self.store.bootstrap_documents([(first, chunks), (second, chunks)])
        self.assertEqual(self.store.documents(), [])
        self.assertIsNone(self.store.get_meta('initialized'))
        self.assertTrue(self.store.bootstrap_documents([(first, chunks)]))

    def test_ingest_parses_captured_original_when_source_changes_before_loader(self):
        from app.loaders import get_loader
        source = self.root / 'changing-source.md'
        captured = '合成参数为12千瓦。'
        changed = '已变更合成参数为99千瓦。'
        source.write_text(captured, encoding='utf-8')
        engine = ConfigurableRagEngine(self.store)
        parsed_paths = []
        def change_external_source(parse_path):
            source.write_text(changed, encoding='utf-8')
            parsed_paths.append(Path(parse_path))
            return get_loader(parse_path)
        with patch('app.core.engine.get_loader', side_effect=change_external_source):
            result = engine.ingest(IngestRequest(path=str(source)))
        document = self.store.get_document(result['document_id'])
        self.assertEqual(parsed_paths, [Path(document['local_path'])])
        self.assertNotEqual(parsed_paths[0], source)
        self.assertEqual(Path(document['local_path']).read_text(), captured)
        self.assertEqual(document['source_sha256'], digest(captured.encode()))
        self.assertTrue(all(chunk.metadata['source_sha256'] == document['source_sha256'] for chunk in engine.chunks))
        self.assertIn(captured, '\n'.join(chunk.content for chunk in engine.chunks))
        self.assertNotIn(changed, '\n'.join(chunk.content for chunk in engine.chunks))

    def test_reused_response_rejects_concurrent_new_revision(self):
        source = self.root / 'reused-source.md'
        source.write_text('合成旧版本参数12。', encoding='utf-8')
        engine = ConfigurableRagEngine(self.store)
        request = IngestRequest(path=str(source))
        first = engine.ingest(request)
        peer = ConfigurableRagEngine(KnowledgeStore(self.root))
        original_refresh = engine.refresh
        def commit_newer_before_refresh():
            source.write_text('合成新版本参数99。', encoding='utf-8')
            peer.ingest(request)
            original_refresh()
        with patch.object(engine, 'refresh', side_effect=commit_newer_before_refresh):
            with self.assertRaises(KnowledgeConflictError):
                engine.ingest(request)
        current = self.store.get_document(first['document_id'])
        self.assertNotEqual(current['revision_id'], first['revision_id'])
        self.assertTrue(all(chunk.metadata['revision_id'] == current['revision_id'] for chunk in engine.chunks))

    def test_process_response_rejects_deletion_after_commit(self):
        from backend.main import ProcessDocumentRequest
        engine = ConfigurableRagEngine(self.store)
        service = KnowledgeService(engine)
        document = service.upload('process-race.md', '合成并发删除资料。'.encode(), 'wind_oam', 'general')
        peer = ConfigurableRagEngine(KnowledgeStore(self.root))
        original_chunks = service.chunks
        def delete_before_response_snapshot():
            peer.remove_document(document['document_id'])
            return original_chunks()
        with patch.object(service, 'chunks', side_effect=delete_before_response_snapshot):
            with self.assertRaises(KnowledgeConflictError):
                service.process(document['document_id'], ProcessDocumentRequest())
        self.assertIsNone(self.store.get_document(document['document_id']))
        self.assertTrue(Path(document['local_path']).is_file())


if __name__ == '__main__':
    unittest.main()
