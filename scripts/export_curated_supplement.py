"""Export only explicitly visually selected supplements; preserve source captions and reject main-set duplicates."""
import argparse,hashlib,json,shutil,sqlite3
from pathlib import Path


def export(root,review_file):
 root=Path(root).resolve();review=json.loads(Path(review_file).read_text());main=sqlite3.connect('file:'+str(root/'collection.sqlite3')+'?mode=ro',uri=True)
 seen_bytes={r[0] for r in main.execute('SELECT sha256 FROM images')};seen_pixels={r[0] for r in main.execute('SELECT pixel_sha256 FROM images')};main.close()
 candidates={}
 base_candidates=root/'精选补充/候选清单.jsonl'
 if base_candidates.exists():
  for line in base_candidates.read_text().splitlines():
   r=json.loads(line);candidates[r['candidate_id']]={**r,'source_file':r['download_file']}
 secondary=root/'精选候选采集/collection.sqlite3'
 if secondary.exists():
  db=sqlite3.connect('file:'+str(secondary)+'?mode=ro',uri=True)
  for raw, in db.execute('SELECT payload FROM images'):
   r=json.loads(raw);candidates[r['id']]={**r,'source_file':str(Path('精选候选采集')/r['file'])}
  db.close()
 records=[];omitted=[]
 for decision in review['decisions']:
  if decision['decision'] not in ('select','provisional_select'):continue
  identity=decision['candidate_id'];r=candidates[identity]
  if r['sha256'] in seen_bytes or r['pixel_sha256'] in seen_pixels:
   omitted.append({'candidate_id':identity,'reason':'already_in_main_collection_or_selected_supplement'});continue
  source=(root/r['source_file']).resolve()
  if not source.is_relative_to(root):raise ValueError('Candidate file is outside collection directory')
  if hashlib.sha256(source.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('Candidate original checksum mismatch')
  safe=''.join(c if c.isalnum() or c in '-_ ' else '_' for c in r['caption'])[:60]
  folder=root/'精选补充/图文资料'/r['industry_id'];folder.mkdir(parents=True,exist_ok=True)
  stem=f'{len(records)+1:03d}_'+safe+'_'+r['sha256'][:8];image=folder/(stem+source.suffix);caption=folder/(stem+'.txt');metadata=folder/(stem+'.json')
  if not image.exists():shutil.copyfile(source,image)
  if hashlib.sha256(image.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('Supplement checksum conflict')
  if caption.exists() and caption.read_text()!=r['caption']+'\n':raise ValueError('Existing caption conflict')
  value={**r,'file':str(image.relative_to(root)),'caption_file':str(caption.relative_to(root)),'metadata_file':str(metadata.relative_to(root)),
   'collection_track':'curated_supplement','source_search_category_id':r['category_id'],'source_search_category':r['category'],'category_id':'curated_'+r['industry_id'],'category':'精选技术图','selection_status':'assistant_visually_selected','selection_reason':decision['reason'],
   'intended_use_cases':decision.get('use_cases',[]),'label_status':'source_caption_unverified','technical_fact_review_status':'unreviewed',
   'license_status':'not_verified','allow_training':False,'allow_public_redistribution':False,'allow_cloud_upload':False}
  caption.write_text(r['caption']+'\n');metadata.write_text(json.dumps(value,ensure_ascii=False,indent=2));records.append(value);seen_bytes.add(r['sha256']);seen_pixels.add(r['pixel_sha256'])
 output=root/'精选补充';output.mkdir(exist_ok=True);(output/'总清单.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
 result={'status':'completed','selected_images':len(records),'omitted':omitted,'review_file':str(review_file),'review_scope':'visible_image_relevance_not_technical_fact_or_license_approval'};(output/'精选结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/runtime/industry-images-20261102-v1');p.add_argument('--review-file',type=Path,required=True);args=p.parse_args();print(json.dumps(export(args.output,args.review_file),ensure_ascii=False))
