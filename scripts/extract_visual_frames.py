"""Extract three explicitly labeled observation frames; keep original GIFs untouched."""
import argparse,json,sqlite3,hashlib,shutil
from io import BytesIO
from pathlib import Path
from PIL import Image

def main():
 p=argparse.ArgumentParser();p.add_argument('--store',type=Path,required=True);p.add_argument('--source-assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise FileExistsError('Preserve the frozen frame manifest')
 with sqlite3.connect(a.store/'knowledge.sqlite3') as db:docs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM documents WHERE deleted=0 AND json_extract(payload,'$.frame_count')>1")]
 recoveries={}
 for receipt in a.output.parent.glob('gif-recovery/*-range-receipt.json'):
  value=json.loads(receipt.read_text());recoveries[value['document_id']]=value
 out=[]
 for d in docs:
  path=Path(d['local_path']).resolve()
  if path.parent!=a.source_assets.resolve() or hashlib.sha256(path.read_bytes()).hexdigest()!=d['source_sha256']:raise ValueError('Animation source changed')
  recovered=recoveries.get(d['document_id']);decode_path=path
  if recovered:
   recovered_path=Path(recovered['file']);data=recovered_path.read_bytes()
   if hashlib.sha256(data).hexdigest()!=recovered['recovered_sha256'] or not data.startswith(path.read_bytes()):raise ValueError('Source recovery proof changed')
   folder=a.store/'visual-source-recoveries';folder.mkdir(exist_ok=True);target=folder/(recovered['recovered_sha256']+'.gif')
   if not target.exists():shutil.copyfile(recovered_path,target)
   if target.read_bytes()!=data:raise ValueError('Recovered asset changed')
   decode_path=target
  with Image.open(decode_path) as image:
   count=image.n_frames
   if count>2000:raise ValueError('Animation exceeds frame budget')
   valid=[];failures=[];times={};timestamp=0
   for index in range(count):
    try:image.seek(index);image.load();valid.append(index);times[index]=[timestamp,image.info.get('duration',0)];timestamp+=image.info.get('duration',0)
    except OSError as exc:failures.append({'frame_index':index,'error':str(exc)})
   if not valid:raise ValueError('Animation has no decodable source frame')
   selected=sorted({valid[0],valid[len(valid)//2],valid[-1]});frames=[]
   folder=a.store/'visual-frame-inputs'/d['source_sha256'];folder.mkdir(parents=True,exist_ok=True)
   for index in selected:
    image.seek(index);stream=BytesIO();image.convert('RGB').save(stream,format='PNG');raw=stream.getvalue();output=folder/f'frame-{index:05d}.png'
    if not output.exists():output.write_bytes(raw)
    if output.read_bytes()!=raw:raise ValueError('Frozen frame bytes changed')
    frames.append({'frame_index':index,'timestamp_ms':times[index][0],'duration_ms':times[index][1],'path':str(output.resolve()),'sha256':hashlib.sha256(raw).hexdigest()})
  out.append({'document_id':d['document_id'],'revision_id':d['revision_id'],'write_version':d['write_version'],'source_sha256':d['source_sha256'],'frame_count':count,'decodable_frame_count':len(valid),'original_declared_frame_count':d['frame_count'],'decode_failures':failures,'source_recovery':recovered,'sampling':'first_middle_last_decodable_v1','all_frames_semantically_observed':False,'frames':frames})
 a.output.write_text(json.dumps({'animations':len(out),'container_frames':sum(x['frame_count'] for x in out),'decodable_frames':sum(x['decodable_frame_count'] for x in out),'original_declared_frames':sum(x['original_declared_frame_count'] for x in out),'sampled_frames':sum(len(x['frames']) for x in out),'items':out},ensure_ascii=False,indent=2));print('animations',len(out),'samples',sum(len(x['frames']) for x in out))
if __name__=='__main__':main()
