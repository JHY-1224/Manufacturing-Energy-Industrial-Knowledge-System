"""Find unused original images from observed/downloaded pairs for subsequent visual review."""
import argparse,hashlib,json,re,sqlite3
from pathlib import Path

TECHNICAL=re.compile(r'流程|工艺|工藝|结构|結構|示意|装配|組裝|参数|參數|原理|检测|檢測|缺陷|相图|性能|电路|電路|接线|接線|process|diagram|schematic|assembly|inspection|defect|structure',re.I)

def prepare(root):
 root=Path(root);db=sqlite3.connect('file:'+str(root/'collection.sqlite3')+'?mode=ro',uri=True);used_sha={r[0] for r in db.execute('select sha256 from images')};used_pixel={r[0] for r in db.execute('select pixel_sha256 from images')};plan=json.loads((root/'plan.json').read_text());categories={c['id']:(d,c) for d in plan['domains'] for c in d['categories']};seen=set();candidates=[]
 for directory in sorted((root/'observations').iterdir()):
  if directory.name not in categories:continue
  domain,category=categories[directory.name]
  for file in sorted(directory.glob('*.json')):
   snapshot=json.loads(file.read_text())
   for item in snapshot['images']:
    key=hashlib.sha256(item['original_url'].encode()).hexdigest();receipt=root/'downloads'/(key+'.json')
    if key in seen or not receipt.exists():continue
    seen.add(key);result=json.loads(receipt.read_text())
    if result.get('failed') or result['saved_kind']!='source_image' or result['sha256'] in used_sha or result['pixel_sha256'] in used_pixel:continue
    if max(result['width'],result['height'])<700 or min(result['width'],result['height'])<300 or not TECHNICAL.search(item['caption']):continue
    candidates.append({**item,**result,'industry_id':domain['id'],'category_id':category['id'],'category':category['name'],'observation_file':str(file.relative_to(root)),
      'candidate_id':'candidate_'+result['sha256'][:16],'selection_status':'pending_visual_review','label_status':'source_caption_unverified','fact_review_status':'not_reviewed'})
 target=root/'精选补充';target.mkdir(exist_ok=True);(target/'候选清单.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in candidates));return candidates

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/runtime/industry-images-20261102-v1');a=p.parse_args();rows=prepare(a.output);print(json.dumps({'candidate_images':len(rows),'status':'pending_visual_review'},ensure_ascii=False))
