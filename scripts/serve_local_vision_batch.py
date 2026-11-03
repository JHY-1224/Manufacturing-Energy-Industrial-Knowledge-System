"""Local persistent pretrained visual observer; the existing encoder is unchanged."""
import argparse,hashlib,threading,time,gc,json
from pathlib import Path
from pydantic import BaseModel,Field
from fastapi import HTTPException
from scripts.serve_local_vision import application,image_bytes,Describe

PROMPT='请用一句不超过45字的中文描述图片中直接可见的对象、布局或图形类型。不要使用背景知识，不猜型号、材质、具体工艺、缺陷原因、质量结论或数值。不确定的操作只描述可见形状和接触关系。不转录小字。看不清时直接说明。只输出这句描述，不输出解释、JSON或标题。'
class Batch(BaseModel):
 images:list[str]=Field(min_length=1,max_length=8)

def observer_application(embedding_path,vision_path):
 app=application(embedding_path,vision_path)
 legacy_health=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/health')
 app.router.routes=[r for r in app.routes if getattr(r,'path',None) not in {'/describe','/health'}]
 identity=legacy_health();fp=identity['vision_fingerprint']+':observe2:'+hashlib.sha256((PROMPT+Path(__file__).read_text()).encode()).hexdigest()[:16]
 lock=threading.Lock();model=None;processor=None
 @app.get('/health')
 def health():return {**legacy_health(),'vision_fingerprint':fp,'observer_loaded':model is not None,'observation_policy':'visible_sentence_v2','batch_limit':8}
 def describe_many(values):
  nonlocal model,processor
  from PIL import Image,ImageOps
  data=[];images=[];counts=[]
  for value in values:
   original,img,frames=image_bytes(value);img=ImageOps.contain(img,(448,448));square=Image.new('RGB',(448,448),'white');square.paste(img,((448-img.width)//2,(448-img.height)//2));images.append(square);data.append(original);counts.append(frames)
  started=time.monotonic()
  with lock:
   from mlx_vlm import load
   from mlx_vlm.generate import batch_generate
   from mlx_vlm.prompt_utils import apply_chat_template
   import mlx.core as mx
   if model is None:model,processor=load(str(vision_path),trust_remote_code=False)
   prompt=apply_chat_template(processor,model.config,PROMPT,num_images=1)
   result=batch_generate(model,processor,images=images,prompts=[prompt]*len(images),max_tokens=80,greedy_sampling=True,verbose=False,group_by_shape=True)
   texts=result.texts
   mx.clear_cache()
  elapsed=round((time.monotonic()-started)*1000,2);out=[]
  for original,text,frames in zip(data,texts,counts):
   text=text.strip();valid=bool(text) and len(text)<=600 and not text.startswith(('```','{'))
   out.append({'fingerprint':fp,'raw_text':text,'observation':{'visible_description':text,'visible_text':[],'regions':[],'unknowns':['具体设备型号、工况和材料未由本图确认','缺陷原因及质量结论未确认']} if valid else None,'parse_error':None if valid else 'invalid_visible_sentence','source_sha256':hashlib.sha256(original).hexdigest(),'frame_index':0,'frame_count':frames,'review_status':'model_observation_pending_ai_review','technical_fact_approved':False,'model_text_is_not_ocr':True,'elapsed_ms':elapsed/len(values),'batch_elapsed_ms':elapsed,'batch_size':len(values),'image_preprocessing':'aspect_preserving_white_letterbox448','prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest()})
  if len(out)!=len(values):raise HTTPException(500,'Visual output count mismatch')
  return {'items':out,'elapsed_ms':elapsed}
 @app.post('/describe')
 def describe(req:Describe):return describe_many([req.image_base64])['items'][0]
 @app.post('/describe-batch')
 def batch(req:Batch):return describe_many(req.images)
 return app

if __name__=='__main__':
 import sys
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--embedding',type=Path,required=True);p.add_argument('--vision',type=Path,required=True);p.add_argument('--port',type=int,default=8083);a=p.parse_args()
 import uvicorn
 uvicorn.run(observer_application(a.embedding.resolve(),a.vision.resolve()),host='127.0.0.1',port=a.port,workers=1)
