from __future__ import annotations

import json
from queue import Queue, Empty
from threading import Event, Thread
from time import perf_counter
from typing import TypedDict
from uuid import uuid4

from app.core.models import QueryOptions, QueryTrace
from app.runtime import RuntimeSession, RuntimeAdapterError
from app.runtime.streaming import generate_stream
from app.storage.knowledge_store import KnowledgeConflictError


class AgentState(TypedDict, total=False):
    question: str
    history: list
    trace: object
    answer: str
    sources: list
    route: str


class AgentService:
    def __init__(self, workbench):
        self.workbench=workbench;self.records=workbench.records

    def stream(self,session_id,request_id,question,options):
        # Import before accepting a turn: a missing optional runtime is not a running job.
        try:
            from langgraph.graph import StateGraph, START, END
            from langchain_core.tools import StructuredTool
            from langsmith import tracing_context
        except ImportError as exc:
            raise RuntimeAdapterError('agent_runtime_not_installed','agent') from exc
        session=self.records.session(session_id)
        queue=Queue();cancelled=Event();started=perf_counter();partial=[]

        def emit(kind,data,persist=True):
            if cancelled.is_set():raise InterruptedError('Client disconnected')
            event={'type':kind,**data}
            if persist:event=self.records.append_event(session_id,request_id,event)
            queue.put(event)

        def delta(text):
            partial.append(text);emit('token',{'text':text},False)

        def load_history(state):
            history=[{'role':m['role'],'content':m['content']} for m in session['messages'] if m['status']=='completed' and m['request_id']!=request_id][-6:]
            # A declared bounded history view, never hidden loss of source evidence.
            history=[{**m,'content':m['content'][-1200:]} for m in history]
            emit('stage',{'stage':'history','status':'completed','messages_used':len(history),'history_window':6,'max_chars_per_message':1200})
            return {'history':history}

        def search_knowledge(query: str):
            """Retrieve current-version evidence from this session's knowledge base."""
            settings=options.model_copy(deep=True)
            settings.metadata_filter={**settings.metadata_filter,'knowledge_base_id':session['knowledge_base_id']}
            settings.metadata_router=False;settings.query_transformation=False
            return self.workbench.retrieve(query,settings)

        search_tool=StructuredTool.from_function(search_knowledge)

        def prefetch(state):
            if any(phrase in question for phrase in ('我刚问','刚才问了','上一条问题','上一个问题')):
                emit('stage',{'stage':'prefetch','status':'skipped','reason':'conversation_memory_question'})
                return {'route':'conversation_memory','sources':[]}
            prior=next((m['content'] for m in reversed(state['history']) if m['role']=='user'),'')
            query=question
            if prior and any(word in question for word in ('这个','那个','它','上述','继续')):query=prior+'\n追问：'+question
            emit('tool_start',{'tool':'knowledge_search','stage':'prefetch','query':query,'knowledge_base_id':session['knowledge_base_id']})
            trace=search_tool.invoke({'query':query})
            sources=[{**hit.model_dump(),'source_url':f'/api/v2/workbench/documents/{hit.doc_id}/source?revision_id={hit.metadata.get("revision_id","")}'} for hit in trace.reranked_docs]
            emit('tool_end',{'tool':'knowledge_search','stage':'prefetch','trace_id':trace.trace_id,'source_count':len(sources)})
            emit('sources',{'items':sources})
            return {'trace':trace,'sources':sources,'route':'agent_rag'}

        def generate(state):
            generation_started=perf_counter()
            emit('stage',{'stage':'generation','status':'running','mode':options.runtime_mode})
            if state['route']=='conversation_memory':
                previous=next((m['content'] for m in reversed(state['history']) if m['role']=='user'),None)
                answer='你上一条问题是：'+previous if previous else '当前会话还没有上一条用户问题。'
                emit('answer',{'text':answer,'streaming':'memory_response'},False)
                trace=QueryTrace(original_query=question,rewritten_query=question,retrieval_mode='conversation_memory',
                                 options=options.model_dump(),answer=answer,runtime={'mode':options.runtime_mode,'status':'completed','generation':'conversation_history'},corpus_version=self.workbench.engine._generation)
            else:
                source_trace=state['trace']
                self.workbench.records.assert_sources_current(source_trace.reranked_docs)
                runtime=RuntimeSession.from_env(options.runtime_mode)
                from app.runtime.privacy import ensure_local_only
                ensure_local_only(runtime,source_trace.reranked_docs)
                contexts=source_trace.compressed_context
                ids=[hit.chunk_id for hit in source_trace.reranked_docs]
                if options.runtime_mode=='configured':
                    answer=generate_stream(runtime.chain,question,contexts,ids,state['history'],delta)
                else:
                    answer=runtime.chain.generate(question,contexts,ids)
                    emit('answer',{'text':answer,'streaming':'buffered_offline'},False)
                trace=source_trace.model_copy(deep=True,update={'trace_id':'trace_'+uuid4().hex[:12],'original_query':question,'answer':answer})
                trace.runtime={**source_trace.runtime,**runtime.metadata(),'prefetch_trace_id':source_trace.trace_id,'agent_runtime':'langgraph','route':state['route'],'status':'completed',
                               'calls':source_trace.runtime.get('calls',[])+runtime.calls}
                if getattr(runtime.chain,'last_call',None) and runtime.chain.last_call.get('reason') in {'incomplete_named_table_evidence','source_table_lookup'}:
                    trace.runtime['generation_evidence_check']=runtime.chain.last_call['reason']
                if runtime.calls:
                    usage=runtime.calls[-1].get('usage') or {}
                    trace.token_usage={k:v for k,v in {'input_tokens':usage.get('prompt_tokens'),'output_tokens':usage.get('completion_tokens')}.items() if v is not None}
                trace.runtime['corpus_version_at_validation']=self.workbench.records.assert_sources_current(source_trace.reranked_docs)
            trace.latency_ms['agent_total']=round((perf_counter()-started)*1000,3)
            trace.latency_ms['agent_generation']=round((perf_counter()-generation_started)*1000,3)
            self.workbench.engine.save_trace(trace)
            return {'answer':answer,'trace':trace}

        def persist(state):
            self.records.finish_turn(session_id,request_id,state['answer'],'completed',state['trace'].trace_id,state['sources'])
            emit('done',{'answer':state['answer'],'trace_id':state['trace'].trace_id,'route':state['route'],'sources':state['sources'],
                         'latency_ms':round((perf_counter()-started)*1000,3),'streaming':options.runtime_mode=='configured'})
            return {}

        graph=StateGraph(AgentState)
        for name,node in [('history',load_history),('prefetch',prefetch),('generate',generate),('persist',persist)]:graph.add_node(name,node)
        graph.add_edge(START,'history');graph.add_edge('history','prefetch');graph.add_edge('prefetch','generate');graph.add_edge('generate','persist');graph.add_edge('persist',END)
        compiled=graph.compile()
        self.records.begin_turn(session_id,request_id,question)
        session=self.records.session(session_id)

        def worker():
            try:
                # Never export private messages/evidence through ambient LangSmith settings.
                with tracing_context(enabled=False):
                    emit('session',{'session_id':session_id,'request_id':request_id,'runtime':'langgraph'})
                    compiled.invoke({'question':question},config={'recursion_limit':8})
            except Exception as exc:
                error=str(exc) if isinstance(exc,(RuntimeAdapterError,KnowledgeConflictError)) else type(exc).__name__
                status='cancelled' if isinstance(exc,InterruptedError) else 'failed'
                trace_id=getattr(exc,'trace_id',None)
                if not trace_id:
                    failure=QueryTrace(original_query=question,rewritten_query=question,retrieval_mode=options.retrieval_mode,
                        options=options.model_dump(),runtime={'mode':options.runtime_mode,'status':status,'agent_runtime':'langgraph','error_type':type(exc).__name__,'code':getattr(exc,'code',None)},
                        warnings=['本轮未完成；部分输出仅保留在消息记录，不作为完成答案。'],
                        latency_ms={'agent_total':round((perf_counter()-started)*1000,3)})
                    self.workbench.engine.save_trace(failure);trace_id=failure.trace_id
                self.records.finish_turn(session_id,request_id,''.join(partial),status,trace_id=trace_id,error=error)
                event={'type':'error','message':error,'status':status,'partial':bool(partial),'trace_id':trace_id}
                self.records.append_event(session_id,request_id,event);queue.put(event)
            finally:queue.put(None)

        def events():
            thread=Thread(target=worker,daemon=True);thread.start()
            try:
                while True:
                    try:event=queue.get(timeout=10)
                    except Empty:
                        yield ': heartbeat\n\n';continue
                    if event is None:break
                    yield 'event: '+event['type']+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            finally:cancelled.set()
        return events()
