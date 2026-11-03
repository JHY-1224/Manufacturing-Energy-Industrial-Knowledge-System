"""Resumable finalized industry image import. No remote calls or source changes."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.knowledge.industry_import import IndustryImporter,preflight
from app.storage.knowledge_store import KnowledgeStore

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-runtime',default=str(ROOT/'data/runtime'))
    parser.add_argument('--store',default=str(ROOT/'data/runtime'))
    parser.add_argument('--run-dir',required=True)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args();plan=preflight(args.source_runtime)
    run=Path(args.run_dir);run.mkdir(parents=True,exist_ok=True)
    manifest=run/('import-plan-'+uuid4().hex+'.json');manifest.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    print(json.dumps({'total':len(plan['items']),'counts':plan['counts'],'plan':str(manifest)},ensure_ascii=False),flush=True)
    if args.preflight_only:return
    importer=IndustryImporter(KnowledgeStore(args.store),run);results=[]
    for item in plan['items'][:args.limit]:
        try:result={'sha256':item['row']['sha256'],**importer.import_item(item)}
        except Exception as exc:result={'sha256':item['row']['sha256'],'status':'failed','error':str(exc)}
        results.append(result)
        with (run/'import-progress.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
        if len(results)%100==0:print('Imported '+str(len(results)),flush=True)
    result={'total':len(results),'imported':sum(r['status']=='imported' for r in results),'reused':sum(r['status']=='reused' for r in results),
            'failures':[r for r in results if r['status']=='failed'],'plan':str(manifest)}
    (run/('import-result-'+uuid4().hex+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result['failures']:raise SystemExit(1)
if __name__=='__main__':main()
