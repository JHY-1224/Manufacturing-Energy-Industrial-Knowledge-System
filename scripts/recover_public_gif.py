"""Recover a source using verified HTTP byte ranges into a NEW derived file."""
import argparse,concurrent.futures,hashlib,json,sqlite3,time
from pathlib import Path
from urllib.request import Request,urlopen
from scripts.collection.http_read import read_complete

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();r=a.root;out=r/'gif-recovery';old=json.loads((out/'retrieval-receipts.json').read_text());receipts=[]
 with sqlite3.connect(r/'snapshot/knowledge.sqlite3') as db:
  for prior in old:
   d=json.loads(db.execute('SELECT payload FROM documents WHERE document_id=?',(prior['document_id'],)).fetchone()[0]);total=int(prior['content_length']);url=prior['original_url'];original=Path(d['local_path']).read_bytes()
   if not url.startswith('https://i2.kknews.cc/') or total>8*1024*1024:raise ValueError('Source recovery outside frozen scope')
   folder=out/'ranges'/d['document_id'];folder.mkdir(parents=True,exist_ok=True)
   def part_once(bounds):
    start,end=bounds
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0','Range':f'bytes={start}-{end}','Accept-Encoding':'identity'}),timeout=20) as response:
     if response.url!=url or response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{total}':raise ValueError('Range identity changed')
     data=read_complete(response,16384)
     if len(data)!=end-start+1:raise ValueError('Range length mismatch')
     return data
   def part(bounds):
    start,end=bounds;cached=folder/f'{start}-{end}.bin'
    if cached.exists():
     raw=cached.read_bytes()
     if len(raw)!=end-start+1:raise ValueError('Cached range length changed')
     return raw
    for attempt in range(3):
     try:
      raw=part_once(bounds);cached.write_bytes(raw);return raw
     except Exception as exc:
      (folder/f'{start}-{end}-attempt{attempt+1}.json').write_text(json.dumps({'start':start,'end':end,'error':str(exc)}))
      if attempt==2:raise
      time.sleep(.3)
   bounds=[(i,min(total-1,i+16383)) for i in range(0,total,16384)]
   target=out/(d['document_id']+'-ranged-full.gif')
   if target.exists():data=target.read_bytes()
   else:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:data=b''.join(pool.map(part,bounds))
   if len(data)!=total or not data.startswith(original):raise ValueError('Full source does not extend the immutable original prefix')
   target=out/(d['document_id']+'-ranged-full.gif')
   if not target.exists():target.write_bytes(data)
   receipt={'document_id':d['document_id'],'revision_id':d['revision_id'],'original_sha256':d['source_sha256'],'original_bytes':len(original),'source_url':url,'file':str(target.resolve()),'recovered_sha256':hashlib.sha256(data).hexdigest(),'recovered_bytes':len(data),'verified_ranges':len(bounds),'original_is_exact_prefix':True,'original_replaced':False,'status':'complete_bytes_pending_full_frame_decode'};receipts.append(receipt);(out/(d['document_id']+'-range-receipt.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(d['document_id'],len(data),len(bounds),flush=True)
 (out/'ranged-recovery-receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
