from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies import service
from app.agent.service import AgentService
from app.core.models import QueryOptions
from app.knowledge.catalog import validate_base
from app.knowledge.workbench import WorkbenchService

router=APIRouter(prefix='/api/v2/workbench',tags=['Knowledge Workbench'])
workbench=WorkbenchService(service)


class ProcessRequest(BaseModel):
    parser_mode:Literal['auto','native','ocr']='auto'
    splitter:Literal['recursive','parent_child','markdown_header','business_object']='parent_child'
    chunk_size:int=Field(default=700,ge=200,le=16000)
    chunk_overlap:int=Field(default=120,ge=0,le=8000)


class ChunkEdit(BaseModel):
    expected_revision:str
    content:str|None=Field(default=None,max_length=32000)
    enabled:bool|None=None


class RetrievalRequest(BaseModel):
    question:str=Field(min_length=1,max_length=16000)
    knowledge_base_id:str|None=None
    document_id:str|None=None
    options:QueryOptions=Field(default_factory=lambda:QueryOptions(fusion_method='rrf',query_expansion=False,metadata_router=False,query_transformation=False,table_retrieval=True,evidence_retrieval=True,cross_language_query=True))


class SessionRequest(BaseModel):
    knowledge_base_id:str='manufacturing'


class TurnRequest(BaseModel):
    question:str=Field(min_length=1,max_length=8000)
    request_id:str=Field(default_factory=lambda:uuid4().hex,pattern=r'^[a-zA-Z0-9_-]{8,100}$')
    options:QueryOptions=Field(default_factory=lambda:QueryOptions(fusion_method='rrf',query_expansion=False,metadata_router=False,query_transformation=False,final_top_k=3,table_retrieval=True,evidence_retrieval=True,cross_language_query=True))


class TextImport(BaseModel):
    title:str=Field(min_length=1,max_length=180)
    content:str=Field(min_length=1,max_length=200000)
    knowledge_base_id:str='manufacturing'


class ReviewRequest(BaseModel):
    state:Literal['draft','published','withdrawn']
    expected_write_version:str=Field(min_length=1,max_length=100)
    expected_review_version:int=Field(ge=0,strict=True)
    reviewer:str=Field(min_length=1,max_length=100)
    note:str=Field(min_length=1,max_length=3000)
    applicability:str=Field(default='',max_length=2000)


def checked(call,*args,**kwargs):
    from app.storage.knowledge_store import KnowledgeConflictError
    try:return call(*args,**kwargs)
    except FileNotFoundError as exc:raise HTTPException(404,str(exc)) from exc
    except KnowledgeConflictError:raise
    except ValueError as exc:
        trace_id=getattr(exc,'trace_id',None)
        if trace_id:return JSONResponse(status_code=422,content={'detail':str(exc),'trace_id':trace_id,'stage':getattr(exc,'stage',None)})
        raise HTTPException(422,str(exc)) from exc


@router.get('/summary')
def summary():return workbench.summary()


@router.get('/knowledge-bases')
def bases():return {'items':workbench.bases()}


@router.get('/status')
def status():return workbench.status()


@router.get('/reviews/summary')
def review_summary():return workbench.reviews.summary()


@router.get('/quality/summary')
def quality_summary(knowledge_base_id:str|None=None):
    from app.knowledge.quality_dashboard import QualityDashboard
    if knowledge_base_id:checked(validate_base,knowledge_base_id)
    return checked(QualityDashboard(workbench.store).summary,knowledge_base_id)


@router.get('/diagnostics')
def diagnostics(check_services:bool=False):
    from app.knowledge.quality_dashboard import QualityDashboard
    return checked(QualityDashboard(workbench.store).diagnostics,check_services)


@router.get('/diagnostics/traces/{trace_id}')
def diagnostic_trace(trace_id:str):
    value=workbench.store.get_trace(trace_id)
    if not value:raise HTTPException(404,'Trace not found')
    return value


@router.get('/documents/{document_id}/review')
def document_review(document_id:str):return checked(workbench.reviews.get,document_id)


@router.get('/documents/{document_id}/review/history')
def review_history(document_id:str,page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100)):
    return checked(workbench.reviews.history,document_id,page,page_size)


@router.post('/documents/{document_id}/review')
def change_review(document_id:str,payload:ReviewRequest):
    return checked(workbench.reviews.transition,document_id,**payload.model_dump())


@router.get('/documents')
def documents(page:int|None=Query(default=None,ge=1),page_size:int=Query(default=50,ge=1,le=100),
              knowledge_base_id:str|None=None,q:str=Query(default='',max_length=1000),compact:bool=False,category:str|None=Query(default=None,max_length=200),
              review_state:Literal['unreviewed','draft','published','withdrawn','needs_review']|None=None):
    if knowledge_base_id:checked(validate_base,knowledge_base_id)
    if page is None and not knowledge_base_id and not q and not compact and not category and not review_state:return {'items':workbench.documents()}
    result=workbench.reader.documents(page or 1,page_size,knowledge_base_id,q,compact,category,review_state)
    if not compact:result['items']=[service.document_view(d) for d in result['items']]
    return result


@router.get('/documents/{document_id}')
def document(document_id:str):
    item=service.store.get_document(document_id)
    if not item:raise HTTPException(404,'Document not found')
    return service.document_view(item)


@router.post('/documents/upload')
async def upload(request:Request,filename:str=Query(...,max_length=180),knowledge_base_id:str='manufacturing'):
    checked(validate_base,knowledge_base_id)
    parts=[];size=0
    async for part in request.stream():
        size+=len(part)
        if size>64*1024*1024:raise HTTPException(413,'File exceeds 64 MiB')
        parts.append(part)
    document=checked(service.upload,filename,b''.join(parts),knowledge_base_id,'general',knowledge_base_id)
    return {'document':document}


@router.post('/documents/text')
def text_import(payload:TextImport):
    checked(validate_base,payload.knowledge_base_id)
    doc=checked(service.upload,payload.title+'.md',payload.content.encode(),payload.knowledge_base_id,'general',payload.knowledge_base_id)
    result=checked(workbench.process,doc['document_id'])
    return {'document':service.document_view(service.store.get_document(doc['document_id'])),'ingestion':result}


@router.post('/documents/{document_id}/process')
def process(document_id:str,payload:ProcessRequest):return checked(workbench.process,document_id,**payload.model_dump())


@router.post('/documents/{document_id}/rebuild')
def rebuild(document_id:str,runtime_mode:Literal['offline','configured']='offline'):return checked(workbench.rebuild,document_id,runtime_mode)


@router.delete('/documents/{document_id}')
def remove(document_id:str):return checked(service.delete,document_id)


@router.get('/documents/{document_id}/sections')
def sections(document_id:str):return {'items':workbench.records.sections(document_id)}


@router.get('/documents/{document_id}/similar-images')
def similar_images(document_id:str,knowledge_base_id:str,top_k:int=Query(default=10,ge=1,le=50),review_mode:Literal['reference','published_only']='reference'):
    from app.knowledge.image_analysis import ImageAnalysisService
    checked(validate_base,knowledge_base_id)
    return checked(ImageAnalysisService(service.store).similar,document_id,knowledge_base_id,top_k,review_mode)


@router.get('/documents/{document_id}/revisions')
def revisions(document_id:str):return {'items':workbench.revisions(document_id)}


@router.get('/documents/{document_id}/diff')
def revision_diff(document_id:str,from_revision:str|None=None,to_revision:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=50)):
    from app.knowledge.version_diff import RevisionDiff
    return checked(RevisionDiff(workbench.store).compare,document_id,from_revision,to_revision,page,page_size)


@router.get('/documents/{document_id}/evidence-comparison')
def evidence_comparison(document_id:str,region_page:int=Query(1,ge=1),region_page_size:int=Query(100,ge=1,le=200)):
    from app.knowledge.evidence_workbench import EvidenceWorkbench
    return checked(EvidenceWorkbench(workbench.store).view,document_id,region_page,region_page_size)


@router.get('/documents/{document_id}/evidence-preview')
def evidence_preview(document_id:str,revision_id:str|None=None):
    from fastapi.responses import Response
    from app.knowledge.image_preview import source_preview
    return Response(checked(source_preview,workbench.store,document_id,revision_id),media_type='image/png',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=60'})


class VisualReviewRequest(BaseModel):
    expected_revision:str=Field(min_length=1,max_length=100)
    expected_write_version:str=Field(min_length=1,max_length=100)
    expected_source_sha256:str=Field(min_length=1,max_length=128)
    expected_model_fingerprint:str=Field(min_length=1,max_length=512)
    expected_label_review_id:str|None=Field(default=None,max_length=100)
    source_visible_description:str=Field(default='',max_length=600)
    source_inspected:bool=False
    reviewer:str=Field(min_length=1,max_length=100)
    note:str=Field(min_length=1,max_length=3000)
    decision:Literal['accepted','corrected','rejected']='corrected'


@router.post('/documents/{document_id}/visual-review')
def visual_review(document_id:str,payload:VisualReviewRequest):
    from app.knowledge.evidence_workbench import EvidenceWorkbench
    return checked(EvidenceWorkbench(workbench.store).review,document_id,**payload.model_dump())


@router.get('/documents/{document_id}/source')
def source(document_id:str,revision_id:str|None=None):
    path,name=checked(workbench.source,document_id,revision_id)
    return FileResponse(path,filename=name,headers={'X-Content-Type-Options':'nosniff'})


@router.get('/documents/{document_id}/preview')
def preview(document_id:str,revision_id:str|None=None):
    path,_=checked(workbench.source,document_id,revision_id)
    types={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.gif':'image/gif','.avif':'image/avif'}
    media=types.get(path.suffix.lower())
    if not media:raise HTTPException(415,'This original is available for download, not inline preview')
    return FileResponse(path,media_type=media,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=300',
        'Content-Security-Policy':"default-src 'none'; sandbox"})


@router.get('/chunks')
def chunks(document_id:str|None=None,q:str=Query(default='',max_length=1000),page:int|None=Query(default=None,ge=1),
           page_size:int=Query(default=50,ge=1,le=100),knowledge_base_id:str|None=None):
    if knowledge_base_id:checked(validate_base,knowledge_base_id)
    if page is None and not knowledge_base_id:return {'items':workbench.chunks(document_id,q)}
    return workbench.reader.chunks(page or 1,page_size,document_id,q,knowledge_base_id)


@router.get('/chunks/{chunk_id}')
def chunk(chunk_id:str):
    result=workbench.reader.chunks(chunk_id=chunk_id,page_size=1)['items']
    if not result:raise HTTPException(404,'Chunk not found')
    return result[0]


@router.patch('/chunks/{chunk_id}')
def edit_chunk(chunk_id:str,payload:ChunkEdit):return checked(workbench.mutate_chunk,chunk_id,**payload.model_dump())


@router.delete('/chunks/{chunk_id}')
def delete_chunk(chunk_id:str,expected_revision:str):return checked(workbench.mutate_chunk,chunk_id,expected_revision,delete=True)


@router.post('/retrieve')
def retrieve(payload:RetrievalRequest):
    options=payload.options.model_copy(deep=True)
    if payload.knowledge_base_id:
        checked(validate_base,payload.knowledge_base_id);options.metadata_filter['knowledge_base_id']=payload.knowledge_base_id
    if payload.document_id:options.metadata_filter['document_id']=payload.document_id
    return checked(workbench.retrieve,payload.question,options).model_dump()


@router.get('/sessions')
def sessions(page:int|None=Query(default=None,ge=1),page_size:int=Query(default=30,ge=1,le=100),knowledge_base_id:str|None=None):
    if page is None:return {'items':workbench.records.sessions()}
    if knowledge_base_id:validate_base(knowledge_base_id)
    return workbench.records.sessions_page(page,page_size,knowledge_base_id)


@router.post('/sessions')
def create_session(payload:SessionRequest):
    checked(validate_base,payload.knowledge_base_id)
    return workbench.records.create_session(payload.knowledge_base_id)


@router.get('/sessions/{session_id}')
def session(session_id:str):return checked(workbench.records.session,session_id)


@router.delete('/sessions/{session_id}')
def archive_session(session_id:str):
    checked(workbench.records.archive_session,session_id);return {'archived':session_id,'history_retained':True}


@router.post('/sessions/{session_id}/stream')
def stream(session_id:str,payload:TurnRequest):
    events=checked(AgentService(workbench).stream,session_id,payload.request_id,payload.question,payload.options)
    return StreamingResponse(events,media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


class MultimodalQuery(BaseModel):
    cross_language_query:bool=True
    description_retrieval:bool=True
    knowledge_base_id:str
    text:str=Field(default='',max_length=4000)
    image_base64:str=Field(default='',max_length=12_000_000)
    top_k:int=Field(default=8,ge=1,le=30)
    review_mode:Literal['reference','published_only']='reference'


@router.get('/multimodal/status')
def multimodal_status():
    from app.knowledge.multimodal import MultimodalService
    mm=MultimodalService(workbench.store)
    result=checked(mm.client.health)
    with workbench.store.connection() as db:
        counts=dict(db.execute("SELECT json_extract(d.payload,'$.knowledge_base_id'),count(*) FROM multimodal_assets a JOIN documents d ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE a.kind='embedding' AND a.fingerprint=? AND d.deleted=0 GROUP BY 1",(result['embedding_fingerprint'],)))
        descriptions=dict(db.execute("SELECT json_extract(d.payload,'$.knowledge_base_id'),count(*) FROM multimodal_assets a JOIN documents d ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE a.kind='observation' AND a.fingerprint=? AND d.deleted=0 GROUP BY 1",(result['vision_fingerprint'],)))
        screened=dict(db.execute("SELECT json_extract(d.payload,'$.knowledge_base_id'),count(*) FROM multimodal_assets a JOIN documents d ON a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE a.kind='observation' AND a.fingerprint=? AND json_extract(a.payload,'$.ai_screening.source_revision_verified')=1 AND d.deleted=0 GROUP BY 1",(result['vision_fingerprint'],)))
    return {**result,'indexed_by_base':counts,'described_by_base':descriptions,'screened_by_base':screened}


@router.post('/multimodal/search')
def multimodal_search(payload:MultimodalQuery):
    import base64,binascii
    from app.knowledge.multimodal import MultimodalService
    try:image=base64.b64decode(payload.image_base64,validate=True) if payload.image_base64 else None
    except (ValueError,binascii.Error) as exc:raise HTTPException(422,'Invalid image encoding') from exc
    return checked(MultimodalService(workbench.store).search,payload.knowledge_base_id,text=payload.text,image=image,top_k=payload.top_k,review_mode=payload.review_mode,cross_language_query=payload.cross_language_query,description_retrieval=payload.description_retrieval)


@router.get('/documents/{document_id}/visual-observations')
def visual_observations(document_id:str):
    from app.knowledge.multimodal import MultimodalService
    return checked(MultimodalService(workbench.store).observations,document_id)


@router.get('/documents/{document_id}/visual-frames/{frame_index}/preview')
def visual_frame_preview(document_id:str,frame_index:int,fingerprint:str=Query(max_length=512)):
    from app.knowledge.visual_frames import VisualFrames
    path=checked(VisualFrames(workbench.store).preview,document_id,frame_index,fingerprint)
    return FileResponse(path,media_type='image/png',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=60'})


@router.get('/documents/{document_id}/structures')
def structure_resolutions(document_id:str):
    from app.knowledge.structure_resolution import StructureResolutions
    return {'items':checked(StructureResolutions(workbench.store).current,document_id),'review_kind':'ai_source_review'}


@router.get('/documents/{document_id}/structures/{candidate_id}/preview')
def structure_preview(document_id:str,candidate_id:str):
    from fastapi.responses import Response
    from app.knowledge.structure_resolution import StructureResolutions
    return Response(checked(StructureResolutions(workbench.store).preview,document_id,candidate_id),media_type='image/png',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=60'})


@router.get('/documents/{document_id}/quality-profile')
def document_quality_profile(document_id:str):
    from app.knowledge.quality_profile import quality_profile
    from app.storage.evidence_review_store import EvidenceReviewStore
    document=workbench.store.get_document(document_id)
    if not document:raise HTTPException(404,'Document not found')
    return {'profile':quality_profile(document),'reviews':EvidenceReviewStore(workbench.store).current(document_id)}
