"""Run/resume local visual descriptions with durable batch tasks and frozen sources."""
import argparse,json,os,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore
from app.knowledge.multimodal import LocalVisionClient
from app.tasks.service import BackgroundTasks
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--store',type=Path,required=True);p.add_argument('--source-assets',type=Path,required=True);p.add_argument('--vision-url',default='http://127.0.0.1:8083');p.add_argument('--report',type=Path,required=True);p.add_argument('--limit',type=int,default=0);a=p.parse_args()
os.environ['RAG_VISION_URL']=a.vision_url;store=KnowledgeStore(a.store);store.asset_dir=a.source_assets.resolve();worker=BackgroundTasks(store)
ids=[]
if a.limit:
 with store.connection() as db:ids=[r[0] for r in db.execute("SELECT document_id FROM documents WHERE deleted=0 AND json_extract(payload,'$.image_sha256') IS NOT NULL ORDER BY document_id LIMIT ?",(a.limit,))]
task=worker.create('vision_describe',document_ids=ids);worker.start();started=time.monotonic()
try:
 while True:
  task=worker.tasks.get(task['task_id']);task['elapsed_seconds']=round(time.monotonic()-started,2);a.report.write_text(json.dumps(task,ensure_ascii=False,indent=2));print(task['status'],task['processed'],task['total'],task['counts'],task['elapsed_seconds'],flush=True)
  if task['status'] not in {'queued','running'}:break
  time.sleep(10)
finally:worker.stop()
if task['status']!='completed':raise SystemExit(1)
