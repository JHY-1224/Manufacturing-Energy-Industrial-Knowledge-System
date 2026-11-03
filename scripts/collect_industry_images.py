"""Collect 50 image/caption pairs per frozen, observed Google Images filter; all material stays local."""
import argparse,json,sys,time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from collection.chrome_capture import ChromeCapture,BrowserBlocked,save_snapshot
from collection.image_store import ImageStore
from collection.relevance import reject_reason


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--tab-id',required=True,type=int);parser.add_argument('--output',type=Path,default=ROOT/'data/runtime/industry-images-20261102-v1');parser.add_argument('--limit-categories',type=int);parser.add_argument('--max-scrolls',type=int,default=22);parser.add_argument('--retry-incomplete',action='store_true');args=parser.parse_args()
 root=args.output;plan=json.loads((root/'plan.json').read_text())
 if plan.get('status')!='frozen':raise ValueError('Collection plan is closed or superseded; use a new explicitly frozen plan for a new collection task')
 store=ImageStore(root);browser=ChromeCapture(args.tab_id);handled=0
 try:
  for domain in plan['domains']:
   for category in domain['categories']:
    if store.counts().get(category['id'],0)>=category['target']:continue
    old=store.state(category['id'])
    if old and old.get('status')=='needs_more_results' and not args.retry_incomplete:continue
    if args.limit_categories and handled>=args.limit_categories:return
    handled+=1;browser.navigate(category['url']);seen=set();last_count=0;stagnant=0
    store.set_state(category['id'],{'status':'running','started_at':datetime.now(timezone.utc).isoformat()})
    for scroll in range(args.max_scrolls+1):
     snapshot=browser.capture(category['url']);save_snapshot(root/'observations'/category['id'],snapshot)
     candidates=[]
     for item in snapshot['images']:
      if item['original_url'] in seen:continue
      seen.add(item['original_url'])
      rejection=reject_reason(domain,category,item)
      if rejection:
       store.log(category['id'],{'kind':'scope_rejected','caption':item['caption'],'original_url':item['original_url'],'reason':rejection});continue
      candidates.append({**item,'collected_at':snapshot['collected_at'],**({'relevance_status':'caption_screened_pending_visual_review'} if category.get('relevance_required') else {})})
     # Only the file transfers are parallel; the real Chrome tab is controlled serially.
     for start in range(0,len(candidates),12):
      batch=candidates[start:start+12]
      with ThreadPoolExecutor(max_workers=4) as pool:
       results=list(pool.map(store.obtain,batch))
      for item,result in zip(batch,results):
       if store.counts().get(category['id'],0)>=category['target']:break
       store.accept(domain,category,item,result)
      report=store.export(plan)
      print(json.dumps({'category':category['id'],'name':category['name'],'count':store.counts().get(category['id'],0),'total_images':report['images'],'completed_categories':report['completed_categories'],'target_images':report['target_images']},ensure_ascii=False),flush=True)
      if store.counts().get(category['id'],0)>=category['target']:break
     count=store.counts().get(category['id'],0)
     if count>=category['target']:break
     stagnant=stagnant+1 if len(seen)==last_count else 0;last_count=len(seen)
     if stagnant>=5:break
     browser.scroll();time.sleep(1.4)
    count=store.counts().get(category['id'],0);store.set_state(category['id'],{'status':'completed' if count>=category['target'] else 'needs_more_results','count':count,'observed_candidates':len(seen),'finished_at':datetime.now(timezone.utc).isoformat()});store.export(plan);time.sleep(3)
 except BrowserBlocked as exc:
  status={'status':'browser_blocked','message':str(exc),'time':datetime.now(timezone.utc).isoformat()};(root/'browser-blocked-'+str(int(time.time()))+'.json').write_text(json.dumps(status,ensure_ascii=False,indent=2));print(json.dumps(status,ensure_ascii=False),flush=True);raise
 finally:
  report=store.export(plan);print(json.dumps({k:report[k] for k in ['status','images','completed_categories','planned_categories','target_images']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
