"""Exercise real urllib POSTs against a loopback fixture, never external models."""
import json
import math
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from app.core.models import RetrievalHit
from app.runtime import RuntimeAdapterError, RuntimeSession


class ModelAdaptersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.received.append((self.path, request, self.headers.get('Authorization')))
                status, payload, headers = self.server.responses.pop(0)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.responses = []
        self.server.received = []
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.env = {'RAG_EMBEDDING_DIMENSIONS': '3', 'RAG_EMBEDDING_SEND_DIMENSIONS': 'true'}
        for stage, path in [('EMBEDDING', '/embeddings'), ('CHAT', '/chat/completions'), ('RERANK', '/rerank')]:
            self.env.update({f'RAG_{stage}_URL': origin + path,
                             f'RAG_{stage}_MODEL': stage.lower() + '-test',
                             f'RAG_{stage}_API_KEY': 'private-test-key'})

    def session(self):
        return RuntimeSession.from_env('configured', environ=self.env)

    def response(self, body, status=200, headers=None):
        self.server.responses.append((status, body, headers or {}))

    def embedding_response(self, vector=None, **extra):
        return {'model': 'embedding-test', 'data': [{'index': 0, 'embedding': [3, 4, 0] if vector is None else vector}],
                'usage': {'prompt_tokens': 4, 'total_tokens': 4}, **extra}

    def chat_response(self, finish='stop', **message):
        return {'model': 'chat-test', 'choices': [{'index': 0, 'finish_reason': finish,
                'message': {'role': 'assistant', 'content': '依据 [c1]，合成阈值为 12。', **message}}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}

    def test_embedding_http_contract_normalization_usage_and_fingerprint(self):
        self.response(self.embedding_response())
        session = self.session()
        vector = session.embedding.embed('合成测试')
        self.assertEqual(vector, [.6, .8, 0])
        self.assertAlmostEqual(math.hypot(*vector), 1)
        path, request, authorization = self.server.received[0]
        self.assertEqual(path, '/embeddings')
        self.assertEqual(request, {'input': ['合成测试'], 'model': 'embedding-test', 'encoding_format': 'float', 'dimensions': 3})
        self.assertEqual(authorization, 'Bearer private-test-key')
        self.assertEqual(session.calls[0]['usage']['prompt_tokens'], 4)
        self.assertIsNone(session.calls[0]['cost'])
        old = session.embedding.fingerprint
        self.env['RAG_EMBEDDING_API_KEY'] = 'changed-key'
        self.assertEqual(self.session().embedding.fingerprint, old)
        self.env['RAG_EMBEDDING_MODEL'] = 'different-model'
        self.assertNotEqual(self.session().embedding.fingerprint, old)
        self.assertEqual(session.embedding.model_name, 'embedding-test')
        self.assertNotIn('private-test-key', repr(session))
        self.assertNotIn('private-test-key', json.dumps(session.metadata()))

    def test_batch_embedding_reorders_by_index_and_rejects_duplicate_indices(self):
        self.response({'model':'embedding-test','data':[{'index':1,'embedding':[0,3,4]},{'index':0,'embedding':[3,4,0]}]})
        session=self.session();self.assertEqual(session.embedding.embed_many(['first','second']),[[.6,.8,0],[0,.6,.8]])
        self.response({'model':'embedding-test','data':[{'index':0,'embedding':[0,3,4]},{'index':0,'embedding':[3,4,0]}]})
        session=self.session()
        with self.assertRaisesRegex(RuntimeAdapterError,'invalid_embedding_rows'):session.embedding.embed_many(['first','second'])
        self.assertEqual(session.calls,[])

    def test_bad_embedding_vectors_rejected_without_fallback(self):
        for vector in ([1, 2], [0, 0, 0], [True, 0, 1], [float('nan'), 0, 1], [float('inf'), 1, 0]):
            with self.subTest(vector=vector):
                self.response(self.embedding_response(vector))
                session = self.session()
                with self.assertRaises(RuntimeAdapterError):
                    session.embedding.embed('合成测试')
                self.assertEqual(session.calls, [])
        self.assertEqual(len(self.server.received), 5)

    def test_embedding_revision_changes_cache_identity(self):
        first = self.session()
        self.env['RAG_EMBEDDING_REVISION'] = 'fixed-weight:token_chunks_weighted_mean_l2_v1'
        changed = self.session()
        self.assertNotEqual(first.embedding.fingerprint, changed.embedding.fingerprint)
        self.assertEqual(changed.metadata()['embedding_revision'], self.env['RAG_EMBEDDING_REVISION'])
        self.env['RAG_EMBEDDING_REVISION'] = 'next-algorithm'
        self.assertNotEqual(changed.embedding.fingerprint, self.session().embedding.fingerprint)

    def test_known_processing_metadata_is_recorded_without_arbitrary_fields(self):
        processing = {'strategy': 'token_chunks_weighted_mean_l2_v1', 'segments_per_input': [3],
                      'source_tokens_per_input': [1395], 'max_model_tokens': 512,
                      'segment_token_limit': 510, 'truncated': False, 'secret': 'do-not-reflect'}
        self.response(self.embedding_response(processing=processing))
        session = self.session()
        session.embedding.embed('合成长文本')
        self.assertEqual(session.calls[0]['processing'], {k: v for k, v in processing.items() if k != 'secret'})
        self.assertNotIn('do-not-reflect', json.dumps(session.metadata()))
        processing = {'strategy': 'document_token_chunks_max_v1', 'segments_per_input': [2],
                      'matched_segments': [{'start_token': 500, 'end_token': 901, 'secret': 'do-not-reflect'}],
                      'source_tokens_per_input': [901], 'max_model_tokens': 512,
                      'query_tokens': 8, 'segment_token_limit': 501, 'truncated': False}
        self.response({'output': {'results': [{'index': 0, 'relevance_score': .9}]}, 'processing': processing})
        session.reranker.rerank('问题', [RetrievalHit(doc_id='d', chunk_id='c', content='合成', score=.5)], 1)
        self.assertEqual(session.calls[-1]['processing']['matched_segments'], [{'start_token': 500, 'end_token': 901}])
        self.assertNotIn('do-not-reflect', json.dumps(session.metadata()))

    def test_embedding_identity_and_row_index_are_checked(self):
        for body in (self.embedding_response(model='wrong-model'),
                     {'model': 'embedding-test', 'data': [{'index': 1, 'embedding': [1, 0, 0]}]}):
            self.response(body)
            with self.assertRaises(RuntimeAdapterError):
                self.session().embedding.embed('合成测试')

    def test_chat_real_request_uses_cited_context_and_reports_usage(self):
        self.response(self.chat_response())
        session = self.session()
        result = session.chain.generate('阈值？', ['合成阈值 12。'], ['c1'])
        self.assertIn('[c1]', result)
        request = self.server.received[0][1]
        self.assertFalse(request['stream'])
        self.assertEqual(json.loads(request['messages'][1]['content'])['evidence'][0]['source'], 'c1')
        self.assertEqual(session.calls[0]['usage']['completion_tokens'], 8)

    def test_partial_refusal_tools_or_empty_generation_rejected(self):
        for body in (self.chat_response('length'), self.chat_response('content_filter'),
                     self.chat_response(content=''), self.chat_response(tool_calls=[{'id': 'tool'}]),
                     self.chat_response(refusal='refused')):
            self.response(body)
            with self.assertRaises(RuntimeAdapterError):
                self.session().chain.generate('问题', ['证据'], ['c1'])
        self.assertEqual(len(self.server.received), 5)

    def test_missing_evidence_does_not_call_remote(self):
        session = self.session()
        self.assertIn('无法', session.chain.generate('问题', [], []))
        self.assertEqual(self.server.received, [])
        self.assertFalse(session.chain.last_call['called'])

    def test_rerank_mapping_is_checked_and_input_hits_remain_intact(self):
        self.response({'output': {'results': [{'index': 1, 'relevance_score': .9}, {'index': 0, 'relevance_score': .2}]}, 'usage': {'total_tokens': 10}})
        hits = [RetrievalHit(doc_id='d1', chunk_id='c1', content='无关', score=.8),
                RetrievalHit(doc_id='d2', chunk_id='c2', content='合成相关证据', score=.5)]
        result = self.session().reranker.rerank('证据', hits, 2)
        self.assertEqual([h.chunk_id for h in result], ['c2', 'c1'])
        self.assertEqual([h.final_rank for h in result], [1, 2])
        self.assertIsNone(hits[0].rerank_score)
        request = self.server.received[0][1]
        self.assertEqual(request['input']['documents'], ['无关', '合成相关证据'])
        self.assertEqual(request['parameters']['top_n'], 2)

    def test_invalid_rerank_result_is_not_adopted(self):
        hits = [RetrievalHit(doc_id='d', chunk_id='c', content='证据', score=.5)]
        for rows in ([], [{'index': 2, 'relevance_score': .5}], [{'index': 0, 'relevance_score': 1.2}],
                     [{'index': True, 'relevance_score': .5}]):
            self.response({'output': {'results': rows}})
            with self.assertRaises(RuntimeAdapterError):
                self.session().reranker.rerank('问题', hits, 1)
        self.response({'output': {'results': [{'index': 0, 'relevance_score': .9}, {'index': 0, 'relevance_score': .8}]}})
        with self.assertRaises(RuntimeAdapterError):
            self.session().reranker.rerank('问题', hits * 2, 2)

    def test_http_failure_is_redacted_and_never_retried(self):
        self.response({'error': 'private-test-key secret upstream body'}, 429)
        with self.assertRaises(RuntimeAdapterError) as caught:
            self.session().embedding.embed('合成测试')
        self.assertEqual(caught.exception.status, 429)
        self.assertNotIn('private-test-key', str(caught.exception))
        self.assertNotIn('upstream', str(caught.exception))
        self.assertEqual(len(self.server.received), 1)

    def test_redirect_and_malformed_json_are_rejected(self):
        self.response({}, 302, {'Location': self.env['RAG_EMBEDDING_URL'] + '/other'})
        with self.assertRaises(RuntimeAdapterError):
            self.session().embedding.embed('合成测试')
        self.assertEqual(len(self.server.received), 1)
        self.response(b'{broken')
        with self.assertRaises(RuntimeAdapterError) as caught:
            self.session().embedding.embed('合成测试')
        self.assertEqual(caught.exception.code, 'invalid_json')

    def test_configured_requires_safe_endpoint_and_explicit_settings(self):
        for value in ('http://example.com/embeddings', 'https://user:private@example.com/api',
                      'https://example.com/api?key=private', 'file:///tmp/x', 'https://example.com/#fragment'):
            with self.subTest(value=value):
                env = dict(self.env, RAG_EMBEDDING_URL=value)
                with self.assertRaises(RuntimeAdapterError):
                    RuntimeSession.from_env('configured', environ=env)
        with self.assertRaises(RuntimeAdapterError):
            RuntimeSession.from_env('configured', environ={})
        self.assertEqual(self.server.received, [])

    def test_offline_never_reads_remote_configuration(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('unexpected network')):
            session = RuntimeSession.from_env('offline', environ=self.env)
            self.assertEqual(len(session.embedding.embed('离线')), 256)
            self.assertIn('fallback', session.chain.generate('问题', ['证据'], ['c1']))
            self.assertEqual(session.metadata()['mode'], 'offline')
            self.assertEqual(session.calls, [])


if __name__ == '__main__':
    unittest.main()
