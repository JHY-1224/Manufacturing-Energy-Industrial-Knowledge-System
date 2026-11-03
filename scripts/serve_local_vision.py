"""Loopback-only pretrained image/text encoding and Qwen3-VL observations. No training."""
import argparse,base64,gc,hashlib,io,json,os,threading,time
from pathlib import Path
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field
from PIL import Image,ImageOps
import torch
from transformers import AutoModel,AutoProcessor

Image.MAX_IMAGE_PIXELS=40_000_000
class Encode(BaseModel):
 text:str=Field(default='',max_length=4000)
 image_base64:str=Field(default='',max_length=12_000_000)
class Describe(BaseModel):
 image_base64:str=Field(min_length=1,max_length=12_000_000)

PROMPT='''请只根据这张图片中实际可见内容作观察记录，不使用网页标题或其他背景知识。不要推断不可见的故障根因、工艺参数、合格与否、安全放行或材料牌号。不确定或看不清的内容填入 unknowns。不要把猜测当作OCR文字。visible_description不超过120字，visible_text最多6项且不得重复，regions最多3项，unknowns最多3项。难以辨认的屏幕数字不要抄录。返回一个JSON对象：{"visible_description":"简明可见内容","visible_text":["能确实看清的原文"],"regions":[{"label":"可见区域","bbox":[0.0,0.0,1.0,1.0],"observation":"该区域可见内容"}],"unknowns":["无法据此判断的事项"]}。bbox为整图左上原点的归一化坐标[x0,y0,x1,y1]，仅作模型预测，不能准确定位时regions为空。'''

def image_bytes(value):
 try:
  data=base64.b64decode(value,validate=True)
  if len(data)>8*1024*1024:raise ValueError('Image exceeds 8 MiB')
  with Image.open(io.BytesIO(data)) as image:
   if image.width*image.height>40_000_000:raise ValueError('Image exceeds pixel budget')
   frames=getattr(image,'n_frames',1);image.seek(0);image=ImageOps.exif_transpose(image).convert('RGB');image.load()
  return data,image,frames
 except Exception as e:raise HTTPException(422,'Invalid or oversized image') from e

def application(embedding_path,vision_path):
 app=FastAPI(title='Local pretrained vision inference');lock=threading.Lock();siglip=None;processor=None
 identity=lambda p:hashlib.sha256((Path(p)/'config.json').read_bytes()+(Path(p)/'model.safetensors.index.json').read_bytes() if (Path(p)/'model.safetensors.index.json').exists() else (Path(p)/'config.json').read_bytes()+(Path(p)/'download-receipt.json').read_bytes()).hexdigest()
 code=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
 embedding_id='siglip2:'+identity(embedding_path)+':'+code;vision_id='qwen3-vl-8b-mlx4:'+identity(vision_path)+':'+code
 @app.get('/health')
 def health():return {'status':'ready','local_only':True,'training':False,'embedding_model':'google/siglip2-base-patch16-224','embedding_fingerprint':embedding_id,'vision_model':'Qwen3-VL-8B-Instruct/MLX4','vision_fingerprint':vision_id,'embedding_loaded':siglip is not None}
 @app.post('/encode')
 def encode(req:Encode):
  nonlocal siglip,processor
  if bool(req.text)==bool(req.image_base64):raise HTTPException(422,'Provide exactly one text or image')
  started=time.monotonic()
  with lock:
   if siglip is None:
    torch.set_num_threads(4);siglip=AutoModel.from_pretrained(embedding_path,local_files_only=True,trust_remote_code=False).eval();processor=AutoProcessor.from_pretrained(embedding_path,local_files_only=True,trust_remote_code=False)
   with torch.inference_mode():
    if req.text:
     inputs=processor(text=[req.text],padding='max_length',max_length=64,truncation=True,return_tensors='pt');vector=siglip.get_text_features(**inputs)
    else:
     _,image,_=image_bytes(req.image_base64);inputs=processor(images=image,return_tensors='pt');vector=siglip.get_image_features(**inputs)
    if not isinstance(vector,torch.Tensor):vector=vector.pooler_output
    vector=torch.nn.functional.normalize(vector.float(),dim=-1)[0].tolist()
  return {'vector':vector,'dimension':len(vector),'fingerprint':embedding_id,'elapsed_ms':round((time.monotonic()-started)*1000,2),'text_max_tokens':64,'frame_index':0 if req.image_base64 else None}
 @app.post('/describe')
 def describe(req:Describe):
  started=time.monotonic();data,image,frames=image_bytes(req.image_base64);image.thumbnail((768,768))
  with lock:
   from mlx_vlm import load,generate
   from mlx_vlm.prompt_utils import apply_chat_template
   import mlx.core as mx
   model,proc=load(str(vision_path),trust_remote_code=False)
   prompt=apply_chat_template(proc,model.config,PROMPT,num_images=1)
   result=generate(model,proc,prompt,image=[image],max_tokens=640,temperature=0.0,repetition_penalty=1.15,repetition_context_size=100,verbose=False)
   raw=result.text;del model,proc;gc.collect();mx.clear_cache()
  observation=None;error=None
  try:
   text=raw.strip();start=text.find('{');end=text.rfind('}');observation=json.loads(text[start:end+1])
   if not isinstance(observation,dict) or not isinstance(observation.get('visible_description'),str):raise ValueError('Missing visible description')
   if not isinstance(observation.get('unknowns'),list):raise ValueError('Missing unknowns')
   regions=[]
   for region in observation.get('regions',[]):
    b=region.get('bbox',[])
    if len(b)==4 and all(type(x) in (int,float) and 0<=x<=1 for x in b) and b[0]<b[2] and b[1]<b[3]:regions.append({**region,'location_status':'model_predicted_pending_human'})
   observation['regions']=regions
  except Exception as e:observation=None;error='unstructured_model_output'
  return {'fingerprint':vision_id,'raw_text':raw,'observation':observation,'parse_error':error,'source_sha256':hashlib.sha256(data).hexdigest(),'frame_index':0,'frame_count':frames,'review_status':'source_checked_pending_human','technical_fact_approved':False,'model_text_is_not_ocr':True,'elapsed_ms':round((time.monotonic()-started)*1000,2),'prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest()}
 return app

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--embedding',type=Path,required=True);p.add_argument('--vision',type=Path,required=True);p.add_argument('--port',type=int,default=8083);a=p.parse_args()
 if not a.embedding.is_dir() or not a.vision.is_dir():raise ValueError('Local model directories required')
 import uvicorn
 uvicorn.run(application(a.embedding.resolve(),a.vision.resolve()),host='127.0.0.1',port=a.port,workers=1)
