"""Strict OpenAI-compatible SSE reader; never fabricates streaming from a final answer."""
import json
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from http.client import HTTPException

from app.runtime.models import RuntimeAdapterError, _NoRedirect
from app.runtime.evidence_policy import EVIDENCE_PROMPT, prefix_table_response


def generate_stream(chain, question, contexts, sources, history, on_delta):
    if not contexts:
        return '当前知识库没有可用于回答的证据，请先上传相关资料或调整检索范围。'
    if len(contexts)!=len(sources):raise RuntimeAdapterError('source_context_mismatch','chat')
    grounded=prefix_table_response(question,contexts,sources)
    if grounded:
        chain.last_call={'stage':'chat','mode':'configured','called':False,'reason':grounded[1],'usage':None,'cost':None}
        on_delta(grounded[0])
        return grounded[0]
    evidence=[{'source':s,'content':c} for s,c in zip(sources,contexts)]
    messages=[{'role':'system','content':EVIDENCE_PROMPT}]
    messages += [{'role':m['role'],'content':m['content']} for m in history if m['role'] in {'user','assistant'}]
    messages += [{'role':'user','content':json.dumps({'question':question,'evidence':evidence},ensure_ascii=False)}]
    payload={'model':chain.model_name,'messages':messages,'stream':True,'temperature':0,'max_tokens':chain.max_output_tokens}
    headers={'Content-Type':'application/json','Accept':'text/event-stream'}
    if chain.endpoint.key:headers['Authorization']='Bearer '+chain.endpoint.key
    raw=json.dumps(payload,ensure_ascii=False).encode()
    if len(raw)>128*1024:raise RuntimeAdapterError('request_too_large','chat')
    request=Request(chain.endpoint.url,data=raw,headers=headers,method='POST')
    answer=[];finish=None;usage=None;count=0;done=False
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open(request,timeout=chain.endpoint.timeout) as response:
            if 'text/event-stream' not in response.headers.get('Content-Type',''):raise RuntimeAdapterError('stream_not_supported','chat')
            while True:
                line=response.readline(65537);count+=len(line)
                if len(line)>65536 or count>4*1024*1024:raise RuntimeAdapterError('stream_too_large','chat')
                if not line:break
                if not line.startswith(b'data:'):continue
                data=line[5:].strip()
                if data==b'[DONE]':done=True;break
                part=json.loads(data)
                if part.get('error'):raise RuntimeAdapterError('generation_failed','chat')
                if part.get('model')!=chain.endpoint.response_model:raise RuntimeAdapterError('model_mismatch','chat')
                if part.get('usage'):usage=part['usage']
                for choice in part.get('choices',[]):
                    delta=choice.get('delta',{})
                    if delta.get('tool_calls') or delta.get('function_call'):raise RuntimeAdapterError('unexpected_model_tool_call','chat')
                    text=delta.get('content','')
                    if not isinstance(text,str):raise RuntimeAdapterError('invalid_stream_delta','chat')
                    if text:answer.append(text);on_delta(text)
                    if choice.get('finish_reason'):finish=choice['finish_reason']
        if not done or finish!='stop' or not ''.join(answer).strip():raise RuntimeAdapterError('incomplete_generation','chat')
        chain._record({'model':chain.endpoint.response_model,'usage':usage})
        return ''.join(answer)
    except HTTPError as exc:
        status=exc.code;exc.close();raise RuntimeAdapterError('http_error','chat',status) from None
    except (URLError,OSError,TimeoutError,HTTPException):raise RuntimeAdapterError('transport_error','chat') from None
    except (ValueError,UnicodeError):raise RuntimeAdapterError('invalid_stream','chat') from None
