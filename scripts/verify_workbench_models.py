"""Local-only synthetic OCR and real MLX SSE acceptance; retains every fixture."""
import json
from pathlib import Path
import time
from uuid import uuid4
from urllib.request import Request, build_opener, ProxyHandler

import fitz

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'data/runtime/workbench-model-validation'/uuid4().hex
BASE='http://127.0.0.1:8000/api/v2/workbench'
opener=build_opener(ProxyHandler({}))


def call(path,body=None,raw=False):
    data=body if raw else json.dumps(body).encode() if body is not None else None
    with opener.open(Request(BASE+path,data=data,headers={'Content-Type':'application/octet-stream' if raw else 'application/json'}),timeout=180) as response:return json.load(response)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    pdf=fitz.open();page=pdf.new_page(width=700,height=450)
    page.insert_text((50,100),'SYNTHETIC OCR VALIDATION',fontsize=28)
    page.insert_text((50,170),'TEST-GAMMA temperature: 48 C',fontsize=25)
    page.insert_text((50,230),'Hold time: 15 minutes',fontsize=25)
    page.insert_text((50,290),'Fixture only. Not a process standard.',fontsize=20)
    image=OUTPUT/'synthetic-ocr.png';page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(image)
    document=call('/documents/upload?filename=synthetic-ocr.png&knowledge_base_id=manufacturing',image.read_bytes(),True)['document']
    ingestion=call('/documents/'+document['document_id']+'/process',{'parser_mode':'auto'})
    chunks=call('/chunks?document_id='+document['document_id'])['items']
    assert chunks and any('48' in c['content'] and '15' in c['content'] for c in chunks),chunks
    assert all(c['metadata']['parse_route']=='ocr' for c in chunks)
    print('Real Apple Vision OCR passed',flush=True)
    session=call('/sessions',{'knowledge_base_id':'manufacturing'})
    options={'runtime_mode':'configured','fusion_method':'rrf','metadata_router':False,'query_transformation':False,'query_rewrite':False,'query_expansion':False,'parent_recovery':False,'compression':False,'final_top_k':2}
    body={'question':'合成资料 TEST-GAMMA 的温度和保持时间是多少？请引用来源 ID。','request_id':'real_sse_'+str(time.time_ns()),'options':options}
    started=time.perf_counter();events=[];times=[]
    with opener.open(Request(BASE+'/sessions/'+session['session_id']+'/stream',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=180) as response:
        for line in response:
            if line.startswith(b'data:'):
                event=json.loads(line[5:]);events.append(event)
                if event['type']=='token':times.append(round(time.perf_counter()-started,3))
                if event['type']=='error':raise AssertionError(event)
    assert events[-1]['type']=='done',events[-1]
    assert len(times)>3 and times[0]<times[-1],times
    answer=events[-1]['answer'];assert '48' in answer and '15' in answer,answer
    ids=[s['chunk_id'] for s in events[-1]['sources']]
    assert any(id in answer for id in ids),(answer,ids)
    saved=call('/sessions/'+session['session_id'])
    assert saved['messages'][-1]['status']=='completed'
    result={'status':'passed','ocr_ingestion':ingestion,'session_id':session['session_id'],'events':events,'token_events':len(times),'first_token_seconds':times[0],'last_token_seconds':times[-1],'synthetic_only':True}
    (OUTPUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('Evidence: '+str(OUTPUT/'result.json'),flush=True)
    print(json.dumps({k:result[k] for k in ['status','token_events','first_token_seconds','last_token_seconds','session_id']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
