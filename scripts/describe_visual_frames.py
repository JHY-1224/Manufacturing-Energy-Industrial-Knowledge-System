"""Resume explicit first/middle/last animation observations from frozen source PNGs."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore
from app.knowledge.multimodal import MultimodalService,LocalVisionClient
from app.knowledge.visual_frames import VisualFrames

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--vision-url',default='http://127.0.0.1:8083');a=p.parse_args();r=a.root;manifest=json.loads((r/'animation-frames-manifest.json').read_text());store=KnowledgeStore(r/'snapshot');store.asset_dir=(Path.cwd()/'data/runtime/originals').resolve();mm=MultimodalService(store,LocalVisionClient(a.vision_url));frames=VisualFrames(store);fp=mm.client.health()['vision_fingerprint'];xs=manifest['items'];parents={}
 for offset in range(0,len(xs),8):
  values=mm.process_many([dict(key=d['document_id'],document_id=d['document_id'],expected_write_version=d['write_version']) for d in xs[offset:offset+8]],fp)
  for identity,value in values.items():
   if isinstance(value,Exception):raise value
   parents[identity]=value
  print('animation parents ready',len(parents),flush=True)
 pending=[]
 for parent in xs:
  existing={v['frame_index']:v for v in frames.current(parent['document_id'],fp)}
  for frame in parent['frames']:
   if frame['frame_index'] in existing:continue
   if frame['frame_index']==0:frames.publish(parent,frame,parents[parent['document_id']],fp,True)
   else:pending.append((parent,frame))
 for offset in range(0,len(pending),8):
  batch=pending[offset:offset+8];result=mm.client.describe_many([Path(f['path']).read_bytes() for _,f in batch])
  if len(result)!=len(batch):raise ValueError('Animation batch count changed')
  for (parent,frame),value in zip(batch,result):frames.publish(parent,frame,value,fp)
  print('new animation frames',min(offset+8,len(pending)),len(pending),flush=True)
 result={'animations':len(xs),'sampled_semantic_frames':sum(len(frames.current(d['document_id'],fp)) for d in xs),'all_frames_semantically_observed':False,'sampling':'first_middle_last_decodable_v1','model_fingerprint':fp,'source_manifest':{k:v for k,v in manifest.items() if k!='items'},'human_approved':False,'training_allowed':False}
 (r/'animation-semantics-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result,flush=True)
if __name__=='__main__':main()
