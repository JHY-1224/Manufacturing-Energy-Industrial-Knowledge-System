"""Save observed public image/caption pairs, keeping immutable receipts and exact duplicate checks."""
import base64
import hashlib
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import shutil
import sqlite3
import urllib.request
from urllib.parse import urlsplit
import warnings
from PIL import Image,ImageOps
try:from .http_read import read_complete
except ImportError:from http_read import read_complete

MAX_BYTES=32*1024*1024
EXT={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','GIF':'.gif','BMP':'.bmp','TIFF':'.tif','AVIF':'.avif'}

def sha(raw):return hashlib.sha256(raw).hexdigest()

def public_url(url):
 p=urlsplit(url)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise ValueError('Invalid public image URL')
 host=p.hostname.lower()
 if host in ('localhost','localhost.localdomain') or host.endswith(('.local','.internal')):raise ValueError('Local hosts are not collection sources')
 try:address=ipaddress.ip_address(host)
 except ValueError:return url
 if not address.is_global:raise ValueError('Private literal address rejected')
 return url

class PublicRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  public_url(newurl);return super().redirect_request(req,fp,code,msg,headers,newurl)

def describe(raw):
 if len(raw)>MAX_BYTES:raise ValueError('Image too large')
 with warnings.catch_warnings():
  warnings.simplefilter('error',Image.DecompressionBombWarning)
  with Image.open(BytesIO(raw)) as image:
   fmt=image.format;size=image.size;image.verify()
  if fmt not in EXT or min(size)<80:raise ValueError('Image is too small or format unsupported')
  with Image.open(BytesIO(raw)) as image:
   frames=getattr(image,'n_frames',1);pixels=0
   if frames>2000:raise ValueError('Animation exceeds source verification frame budget')
   for index in range(frames):
    image.seek(index);pixels+=image.width*image.height
    if frames>1 and pixels>100_000_000:raise ValueError('Animation exceeds source verification pixel budget')
    image.load()
   image.seek(0)
   rgb=ImageOps.exif_transpose(image).convert('RGB')
   pixel_sha=sha(str(rgb.size).encode()+rgb.tobytes())
 return {'sha256':sha(raw),'pixel_sha256':pixel_sha,'width':size[0],'height':size[1],'format':fmt,'bytes':len(raw),'verified_frame_count':frames,'all_frames_decoded':True}

class ImageStore:
 def __init__(self,root):
  self.root=Path(root);self.downloads=self.root/'downloads';self.downloads.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.root/'collection.sqlite3');self.db.row_factory=sqlite3.Row
  self.db.executescript('''CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,category_id TEXT NOT NULL,sha256 TEXT UNIQUE NOT NULL,pixel_sha256 TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS failures(event_id INTEGER PRIMARY KEY,category_id TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS category_state(category_id TEXT PRIMARY KEY,payload TEXT NOT NULL);''');self.db.commit()

 def records(self):return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM images ORDER BY rowid')]
 def counts(self):return dict(self.db.execute('SELECT category_id,count(*) FROM images GROUP BY category_id'))
 def state(self,category):
  r=self.db.execute('SELECT payload FROM category_state WHERE category_id=?',(category,)).fetchone();return json.loads(r[0]) if r else None
 def set_state(self,category,data):
  with self.db:self.db.execute('INSERT OR REPLACE INTO category_state VALUES (?,?)',(category,json.dumps(data,ensure_ascii=False)))
 def log(self,category,data):
  with self.db:self.db.execute('INSERT INTO failures(category_id,payload) VALUES (?,?)',(category,json.dumps(data,ensure_ascii=False)))

 def obtain(self,item):
  key=sha(item['original_url'].encode());receipt=self.downloads/(key+'.json')
  if receipt.exists():return json.loads(receipt.read_text())
  errors=[]
  for url,kind in [(item['original_url'],'source_image'),(item.get('thumbnail_url',''),'google_preview')]:
   try:
    if url.startswith('data:image/'):
     raw=base64.b64decode(url.split(',',1)[1],validate=True);saved_from='inline Google thumbnail'
    else:
     public_url(url);opener=urllib.request.build_opener(PublicRedirect())
     request=urllib.request.Request(url,headers={'User-Agent':'IndustrialKnowledgeImageCollection/1.0 (user-requested public image reference)'})
     with opener.open(request,timeout=9) as response:
      public_url(response.geturl())
      if int(response.headers.get('Content-Length','0') or 0)>MAX_BYTES:raise ValueError('Image too large')
      raw=read_complete(response,MAX_BYTES);saved_from=response.geturl()
    info=describe(raw);file=self.downloads/(key+EXT[info['format']])
    if not file.exists():file.write_bytes(raw)
    result={**info,'download_file':str(file.relative_to(self.root)),'saved_kind':kind,'saved_from':saved_from,'download_failures':errors}
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
   except Exception as exc:errors.append({'url':url[:1800] if not url.startswith('data:') else 'inline thumbnail','error':f'{type(exc).__name__}: {str(exc)[:180]}'})
  result={'failed':True,'download_failures':errors};receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

 def accept(self,domain,category,item,result):
  if result.get('failed'):self.log(category['id'],{'kind':'download_failed','caption':item['caption'],'original_url':item['original_url'],**result});return False
  old=self.db.execute('SELECT id,category_id FROM images WHERE sha256=? OR pixel_sha256=?',(result['sha256'],result['pixel_sha256'])).fetchone()
  if old:self.log(category['id'],{'kind':'exact_duplicate','duplicate_of':old['id'],'duplicate_category':old['category_id'],'caption':item['caption'],'original_url':item['original_url']});return False
  count=self.counts().get(category['id'],0);identity=category['id']+'_'+result['sha256'][:16]
  safe=lambda v:''.join(c if c.isalnum() or c in '-_ ' else '_' for c in v).strip()[:65] or 'image'
  folder=self.root/'图文资料'/domain['id']/(category['id']+'_'+safe(category['name']));folder.mkdir(parents=True,exist_ok=True)
  stem=f'{count+1:03d}_'+safe(item['caption'])+'_'+result['sha256'][:8]
  file=folder/(stem+EXT[result['format']]);caption=folder/(stem+'.txt');metadata=folder/(stem+'.json')
  record={**item,**{k:v for k,v in result.items() if k!='download_file'},'id':identity,'industry_id':domain['id'],'manufacturing_type':domain['manufacturing_type'],
   'category_id':category['id'],'category':category['name'],'file':str(file.relative_to(self.root)),'caption_file':str(caption.relative_to(self.root)),'metadata_file':str(metadata.relative_to(self.root)),
   'label_status':'source_caption_unverified','license_status':'not_verified','image_analysis_status':'not_run','allow_training':False,'allow_public_redistribution':False,'allow_cloud_upload':False}
  if not file.exists():shutil.copyfile(self.root/result['download_file'],file)
  if sha(file.read_bytes())!=result['sha256']:raise ValueError('Saved file checksum mismatch')
  if caption.exists() and caption.read_text()!=item['caption']+'\n':raise ValueError('Existing caption conflict')
  caption.write_text(item['caption']+'\n');metadata.write_text(json.dumps(record,ensure_ascii=False,indent=2))
  with self.db:self.db.execute('INSERT INTO images VALUES (?,?,?,?,?)',(identity,category['id'],record['sha256'],record['pixel_sha256'],json.dumps(record,ensure_ascii=False)))
  return True

 def export(self,plan):
  rows=self.records();(self.root/'总清单.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
  counts=self.counts();categories=[{**c,'industry_id':d['id'],'collected':counts.get(c['id'],0)} for d in plan['domains'] for c in d['categories']]
  report={'status':'completed' if all(c['collected']>=c['target'] for c in categories) else 'in_progress','images':len(rows),'categories':categories,
    'planned_categories':len(categories),'target_images':sum(c['target'] for c in categories),'completed_categories':sum(c['collected']>=c['target'] for c in categories),
    'source_images':sum(r['saved_kind']=='source_image' for r in rows),'google_previews':sum(r['saved_kind']=='google_preview' for r in rows),'label_status':'source_caption_unverified','exact_byte_pixel_duplicates_in_selected_set':0}
  (self.root/'进度.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
