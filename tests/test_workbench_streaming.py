from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import unittest

from app.runtime.models import CompatibleRagChain, Endpoint, RuntimeAdapterError
from app.runtime.streaming import generate_stream


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mode='normal'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.payload=payload
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                parts=[{'model':'synthetic-model','choices':[{'delta':{'content':'合成结论 '},'finish_reason':None}]},
                       {'model':'synthetic-model','choices':[{'delta':{'content':'[source_1]'},'finish_reason':None}]},
                       {'model':'synthetic-model','choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':20,'completion_tokens':6}}]
                if cls.mode=='wrong_model':parts[0]['model']='wrong-model'
                if cls.mode=='length':parts[-1]['choices'][0]['finish_reason']='length'
                if cls.mode=='tool':parts[0]['choices'][0]['delta']={'tool_calls':[{'name':'unsafe'}]}
                if cls.mode=='truncated':parts=parts[:1]
                try:
                    for part in parts:self.wfile.write(b'data: '+json.dumps(part,ensure_ascii=False).encode()+b'\n\n');self.wfile.flush()
                    if cls.mode!='truncated':self.wfile.write(b'data: [DONE]\n\n')
                except (BrokenPipeError,ConnectionResetError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def chain(self):
        return CompatibleRagChain(Endpoint('chat',f'http://127.0.0.1:{self.server.server_port}/chat','synthetic-model','','synthetic-model',5),[],128)

    def test_real_deltas_preserve_usage_and_evidence(self):
        type(self).mode='normal';chain=self.chain();deltas=[]
        result=generate_stream(chain,'合成问题',['合成证据'],['source_1'],[],deltas.append)
        self.assertEqual(deltas,['合成结论 ','[source_1]'])
        self.assertEqual(result,''.join(deltas))
        self.assertTrue(self.payload['stream'])
        self.assertEqual(chain.calls[0]['usage']['completion_tokens'],6)
        self.assertIn('source_1',self.payload['messages'][-1]['content'])

    def test_partial_wrong_model_and_tool_output_never_publish_success(self):
        for mode in ('wrong_model','length','tool','truncated'):
            with self.subTest(mode=mode):
                type(self).mode=mode;chain=self.chain()
                with self.assertRaises(RuntimeAdapterError):generate_stream(chain,'合成问题',['证据'],['source_1'],[],lambda _:None)
                self.assertEqual(chain.calls,[])

    def test_empty_evidence_does_not_call_model(self):
        chain=self.chain();answer=generate_stream(chain,'问题',[],[],[],lambda _:None)
        self.assertIn('没有',answer);self.assertEqual(chain.calls,[])


if __name__=='__main__':unittest.main()
