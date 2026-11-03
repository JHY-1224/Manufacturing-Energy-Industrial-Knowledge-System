from contextlib import asynccontextmanager
import asyncio
from typing import Literal
from fastapi import APIRouter,Query
from pydantic import BaseModel,Field
from app.api.dependencies import engine
from app.api.routes_workbench import checked,ProcessRequest
from app.tasks.service import BackgroundTasks

worker=BackgroundTasks(engine.store)


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    try:yield
    finally:await asyncio.to_thread(worker.stop)


router=APIRouter(prefix='/api/v2/workbench/tasks',tags=['Background Tasks'],lifespan=lifespan)


class TaskRequest(BaseModel):
    kind:Literal['index','process','image','image_frames','multimodal_index','vision_describe']='index'
    knowledge_base_id:str|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=500)
    runtime_mode:Literal['offline','configured']='configured'
    process_options:ProcessRequest=Field(default_factory=ProcessRequest)


@router.get('')
def tasks(page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100)):
    return {**worker.tasks.page(page,page_size),'worker':worker.health()}


@router.post('',status_code=202)
def create_task(payload:TaskRequest):
    return checked(worker.create,payload.kind,payload.knowledge_base_id,payload.document_ids,payload.runtime_mode,payload.process_options.model_dump())


@router.get('/{task_id}')
def task(task_id:str):return checked(worker.tasks.get,task_id)


@router.get('/{task_id}/items')
def items(task_id:str,page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100),status:Literal['pending','running','completed','failed','stale']|None=None):
    return checked(worker.tasks.items,task_id,page,page_size,status)


@router.post('/{task_id}/{action}')
def control(task_id:str,action:Literal['pause','resume','cancel','retry']):
    result=checked(worker.tasks.control,task_id,action);worker.wake.set();return result
