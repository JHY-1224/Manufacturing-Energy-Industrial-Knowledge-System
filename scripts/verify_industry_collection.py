"""Verify every selected image/caption/source triple and all frozen category quotas locally."""
import argparse,collections,json,re,sqlite3,sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from collection.image_store import describe,public_url


def validate_reference_urls(row):
 public_url(row['source_page'])
 try:public_url(row['original_url']);return 'public_http_url'
 except ValueError:
  original=urlsplit(row['original_url'])
  # Google can identify an embedded document figure by an opaque, non-fetchable
  # x-raw-image reference. Only its clearly marked Google preview was downloaded.
  if row['saved_kind']!='google_preview' or original.scheme!='x-raw-image' or original.netloc or not re.fullmatch(r'/[a-fA-F0-9]{32,128}',original.path):raise
  saved=row.get('saved_from','')
  if saved!='inline Google thumbnail':
   public_url(saved)
   host=urlsplit(saved).hostname or ''
   if not(host.endswith('.gstatic.com') or host.endswith('.google.com') or host.endswith('.googleusercontent.com')):raise ValueError('Non-HTTP reference must have an identifiable Google preview')
  return 'non_http_google_reference_preview_only'


def verify(root,require_complete=False):
 root=Path(root).resolve();plan=json.loads((root/'plan.json').read_text());db=sqlite3.connect('file:'+str(root/'collection.sqlite3')+'?mode=ro',uri=True)
 db.execute('BEGIN');rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM images ORDER BY rowid')];integrity=db.execute('PRAGMA integrity_check').fetchone()[0];db.close()
 assert integrity=='ok'
 expected={c['id']:c for d in plan['domains'] for c in d['categories']};counts=collections.Counter(r['category_id'] for r in rows)
 assert not(set(counts)-set(expected))
 for identity,category in expected.items():
  assert counts[identity]<=category['target'],identity
  if require_complete:assert counts[identity]==category['target'],identity
 supplement_path=root/'精选补充/总清单.jsonl';extras=[json.loads(line) for line in supplement_path.read_text().splitlines()] if supplement_path.exists() else []
 byte_hashes=set();pixel_hashes=set();source_kind=collections.Counter();formats=collections.Counter();references=collections.Counter();total_bytes=0
 for row in rows+extras:
  def asset(key):
   value=(root/row[key]).resolve();assert value.is_relative_to(root),key;assert value.is_file(),str(value);return value
  image,caption,metadata=asset('file'),asset('caption_file'),asset('metadata_file')
  result=describe(image.read_bytes())
  for key in ['sha256','pixel_sha256','width','height','format','bytes']:assert result[key]==row[key],(row['file'],key)
  assert caption.read_text()==row['caption']+'\n'
  saved=json.loads(metadata.read_text())
  for key in ['file','caption','source_page','original_url','sha256','pixel_sha256']:assert saved[key]==row[key],(row['file'],key)
  assert row['sha256'] not in byte_hashes and row['pixel_sha256'] not in pixel_hashes
  byte_hashes.add(row['sha256']);pixel_hashes.add(row['pixel_sha256']);source_kind[row['saved_kind']]+=1;formats[row['format']]+=1;total_bytes+=row['bytes']
  assert row['label_status']=='source_caption_unverified'
  assert not row['allow_training'] and not row['allow_public_redistribution'] and not row['allow_cloud_upload']
  references[validate_reference_urls(row)]+=1
 result={'status':'passed','main_images':len(rows),'supplement_images':len(extras),'total_images':len(rows)+len(extras),
  'categories':len(expected),'complete_categories':sum(counts[c['id']]==c['target'] for c in expected.values()),'category_counts':dict(counts),
  'require_complete':require_complete,'source_kind_counts':dict(source_kind),'source_reference_types':dict(references),'formats':dict(formats),'bytes':total_bytes,
  'all_images_decoded':True,'all_hashes_verified':True,'all_source_pairs_verified':True,'exact_byte_and_pixel_duplicates':0,'sqlite_integrity':integrity,
  'technical_fact_or_license_review_completed':False,'image_vlm_or_training_run':False}
 out=root/('verification-'+uuid4().hex+'.json');out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'report':str(out),**{k:result[k] for k in ['status','main_images','supplement_images','total_images','categories','complete_categories']}},ensure_ascii=False),flush=True);return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'data/runtime/industry-images-20261102-v1');p.add_argument('--require-complete',action='store_true');a=p.parse_args();verify(a.output,a.require_complete)
