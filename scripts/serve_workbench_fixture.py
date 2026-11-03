"""Explicit synthetic UI fixture. Loopback only, no real corpus or model inference."""
import argparse,base64,hashlib,json,os,struct,sys,threading,zlib
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def example_png():
    width,height=620,360;pixels=bytearray([255])*(width*height*3)
    def rectangle(x,y,w,h,color):
        for row in range(y,y+h):pixels[(row*width+x)*3:(row*width+x+w)*3]=bytes(color)*w
    rectangle(100,240,400,50,(30,110,200));rectangle(300,100,40,140,(240,130,20));rectangle(300,100,140,30,(240,130,20))
    for offset,letter in [(0,['01110','10001','10001','10001','10101','10010','01101']),(24,['01110','10001','10001','11111','10001','10001','10001'])]:
        for y,row in enumerate(letter):
            for x,value in enumerate(row):
                if value=='1':rectangle(30+offset+x*4,25+y*4,4,4,(0,0,0))
    def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
    scan=b''.join(b'\x00'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b'')

def seed(directory):
    from app.storage.knowledge_store import KnowledgeStore,digest,now
    from app.storage.task_store import TaskStore
    from app.knowledge.service import KnowledgeService
    from app.knowledge.workbench import WorkbenchService
    from app.core.engine import ConfigurableRagEngine
    from app.core.models import IngestRequest,RagChunk,QueryTrace
    from app.knowledge.multimodal import MultimodalService
    store=KnowledgeStore(directory);marker=directory/'synthetic-fixture.json'
    if marker.exists():return json.loads(marker.read_text())
    if store.documents():raise ValueError('Fixture refuses to modify an existing knowledge corpus')
    engine=ConfigurableRagEngine(store);mm=MultimodalService(store)
    png=example_png();gif=base64.b64decode('R0lGODlhDAAIAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAADAAIAAAIEgABCBxIsKDBgwgTKlzIsGHBgAAh+QQBCgABACwAAAAADAAIAIEAAP8AAAAAAAAAAAAIEgABCBxIsKDBgwgTKlzIsGHBgAA7')
    with store.connection() as db:db.execute('CREATE TABLE IF NOT EXISTS image_analysis_cache (cache_key TEXT PRIMARY KEY,source_sha256 TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL)')
    for identity,name,raw in [('qa_image','qa-regions.png',png),('qa_gif','qa-two-frames.gif',gif)]:
        sha,path=store.save_asset(raw,name);revision=identity+'_revision';ocr=identity+'_ocr';d=store.register(identity,{'document_id':identity,'title':name,'source_name':name,'source_caption':'QA 合成图文资料','local_path':path,'source_sha256':sha,'image_sha256':sha,'image_width':620 if identity=='qa_image' else 12,'image_height':360 if identity=='qa_image' else 8,'knowledge_base_id':'automotive','status':'indexed','revision_id':revision,'chunk_count':1,'image_analysis_key':ocr,'ocr_status':'completed' if identity=='qa_image' else 'no_text','data_source':'synthetic_ui_fixture','allow_training':False})
        c=RagChunk(chunk_id=identity+'_caption',doc_id=identity,content='QA 合成资料，仅用于软件交互验收。橙色竖条与蓝色横条。',metadata={'revision_id':revision,'knowledge_base_id':'automotive','enabled':True,'chunk_kind':'source_caption'});store.replace_document(d,[c.model_dump()],{'operation':'synthetic_fixture'})
        ob={'visible_description':'合成测试观察：蓝色竖条位于橙色横条上。','visible_text':[],'regions':[],'unknowns':['这是合成验收记录，未调用预训练模型','不用于工业结论']};value={'document_id':identity,'revision_id':revision,'source_sha256':sha,'fingerprint':'synthetic-ui-vision','kind':'observation','observation':ob,'raw_text':json.dumps(ob,ensure_ascii=False),'model_input_sha256':sha,'ai_screening':{'source_revision_verified':True,'retrieval_allowed':True,'retrieval_text':ob['visible_description'],'flags':[]},'human_approved':False,'created_at':now()}
        with store.connection() as db:
            db.execute('INSERT INTO multimodal_assets VALUES (?,?,?,?,?,?)',(identity,revision,'synthetic-ui-vision','observation',sha,store.encode(value)));db.execute('INSERT INTO multimodal_assets VALUES (?,?,?,?,?,?)',(identity,revision,'synthetic-ui-embedding','embedding',sha,store.encode({'vector':[1.,0.],'dimension':2,'fingerprint':'synthetic-ui-embedding'})))
            lines=[{'text':'QA','confidence':None,'bbox':[30/620,25/360,44/620,28/360]}] if identity=='qa_image' else []
            db.execute('INSERT INTO image_analysis_cache VALUES (?,?,?,?)',(ocr,sha,'synthetic-ui-ocr',store.encode({'source_sha256':sha,'fingerprint':'synthetic-ui-ocr','provider':'synthetic_fixture','lines':lines,'width':620,'height':360})))
    text='# QA 版本差异样例\n合成数值为10，不用于工业判断。\n'.encode();sha,path=store.save_asset(text,'qa-text.md');store.register('qa_text',{'document_id':'qa_text','title':'QA 版本差异样例','source_name':'qa-text.md','source_sha256':sha,'local_path':path,'knowledge_base_id':'automotive','status':'uploaded','data_source':'synthetic_ui_fixture','allow_training':False})
    engine.ingest(IngestRequest(path=str(path),document_id='qa_text',knowledge_base_id='automotive'))
    workbench=WorkbenchService(KnowledgeService(engine));d=store.get_document('qa_text')
    with store.connection() as db:chunk=json.loads(db.execute("SELECT payload FROM chunks WHERE document_id='qa_text' LIMIT 1").fetchone()[0])
    workbench.mutate_chunk(chunk['chunk_id'],d['revision_id'],content=chunk['content'].replace('10','12'))
    task=TaskStore(store).create('index',{},[{'key':'synthetic-failure','document_id':'qa_text'}])
    with store.connection() as db:
        row=db.execute('SELECT payload FROM background_tasks WHERE task_id=?',(task['task_id'],)).fetchone();payload=json.loads(row[0]);payload['error']='Synthetic parser failure for UI navigation';db.execute("UPDATE background_tasks SET status='failed',payload=? WHERE task_id=?",(store.encode(payload),task['task_id']));db.execute("UPDATE background_items SET status='failed',error='synthetic fixture' WHERE task_id=?",(task['task_id'],))
    trace=QueryTrace(original_query='QA 合成检索失败',rewritten_query='QA 合成检索失败',runtime={'status':'failed','stage':'retrieval','code':'synthetic_fixture'},retrieval_mode='fixture');store.save_trace(trace.model_dump())
    value={'synthetic_fixture':True,'no_model_inference':True,'image_document':'qa_image','text_document':'qa_text','gif_document':'qa_gif','task_id':task['task_id'],'trace_id':trace.trace_id};marker.write_text(json.dumps(value,ensure_ascii=False,indent=2));return value

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--port',type=int,default=18071);parser.add_argument('--vision-port',type=int,default=18073);args=parser.parse_args();directory=args.directory.resolve()
    if directory==ROOT/'data/runtime' or args.port==args.vision_port or args.port in {8000,8080,8082,8083,15101} or args.vision_port in {8000,8080,8082,8083,15101} or not all(1024<=p<=65535 for p in [args.port,args.vision_port]):raise ValueError('Use separate synthetic fixture data and non-production ports')
    if (directory/'knowledge.sqlite3').exists() and not (directory/'synthetic-fixture.json').exists():raise ValueError('Refusing a non-fixture database')
    os.environ.update(RAG_DATA_DIR=str(directory),RAG_CHAT_URL='',RAG_EMBEDDING_URL='',RAG_RERANK_URL='',RAG_VISION_URL=f'http://127.0.0.1:{args.vision_port}',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1');print(seed(directory),flush=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def respond(self,value):
            raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):self.respond({'status':'ready','local_only':True,'training':False,'embedding_fingerprint':'synthetic-ui-embedding','vision_fingerprint':'synthetic-ui-vision','vision_model':'SYNTHETIC_UI_FIXTURE','embedding_model':'SYNTHETIC_UI_FIXTURE'})
        def do_POST(self):
            self.rfile.read(min(int(self.headers.get('Content-Length','0')),12_000_000));self.respond({'vector':[1.,0.],'dimension':2,'fingerprint':'synthetic-ui-embedding','synthetic_fixture':True})
    server=HTTPServer(('127.0.0.1',args.vision_port),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    from app.main import app
    import uvicorn
    try:uvicorn.run(app,host='127.0.0.1',port=args.port,lifespan='off')
    finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
