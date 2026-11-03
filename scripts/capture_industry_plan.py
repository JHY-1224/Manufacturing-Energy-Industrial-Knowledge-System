"""Freeze the six root-query Google chip lists before downloading any collection."""
import argparse,json,sys,time
from pathlib import Path
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from collection.chrome_capture import ChromeCapture,save_snapshot

DOMAINS=[('automotive','汽车制造','discrete'),('high_end_equipment','高端装备制造','discrete'),('electronics','电子制造','discrete'),('consumer_goods','消费品制造','discrete'),('materials','材料工业','process'),('energy','能源工业','process')]


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tab-id',type=int,required=True);p.add_argument('--output',type=Path,default=ROOT/'data/runtime/industry-images-20261102-v1');a=p.parse_args()
 output=a.output;output.mkdir(parents=True,exist_ok=True);plan_path=output/'plan.json'
 plan=json.loads(plan_path.read_text()) if plan_path.exists() else {'date':'2026-11-02','target_per_category':50,'domains':[],'chip_scope':'root_query_related_filters_one_level','status':'discovering'}
 browser=ChromeCapture(a.tab_id)
 for domain,query,group in DOMAINS:
  if any(d['id']==domain for d in plan['domains']):continue
  if domain!='automotive':browser.navigate('https://www.google.com/search?'+urlencode({'q':query,'udm':2,'hl':'zh-CN'}))
  data=browser.capture();assert data['chips'],f'No observed chips for {query}'
  snapshot=save_snapshot(output/'root-observations'/domain,data)
  seen=set();categories=[]
  for c in data['chips']:
   if c['name'] in seen:continue
   seen.add(c['name']);categories.append({**c,'id':f'{domain}_{len(categories)+1:02d}','target':50})
  plan['domains'].append({'id':domain,'query':query,'manufacturing_type':group,'search_url':data['search_url'],'observation':str(snapshot.relative_to(output)),'categories':categories})
  plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2));print(json.dumps({'domain':domain,'query':query,'categories':[c['name'] for c in categories],'planned_images':len(categories)*50},ensure_ascii=False),flush=True);time.sleep(3)
 plan['status']='frozen';plan['category_count']=sum(len(d['categories']) for d in plan['domains']);plan['target_images']=50*plan['category_count'];plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2));print(json.dumps({'status':'frozen','categories':plan['category_count'],'target_images':plan['target_images']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
