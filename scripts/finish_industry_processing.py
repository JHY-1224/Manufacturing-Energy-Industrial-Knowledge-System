"""Wait for a durable image task, then freeze and prebuild all current text vectors."""
import argparse,json,time
from pathlib import Path
from urllib.request import Request,ProxyHandler,build_opener

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image-task',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    opener=build_opener(ProxyHandler({}));root='http://127.0.0.1:8000/api/v2/workbench/tasks'
    def call(path='',body=None):
        req=Request(root+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=60) as response:return json.load(response)
    def wait(identity,kind):
        previous=None
        while True:
            task=call('/'+identity);status=(task['status'],task['processed'])
            if status!=previous:
                (output/(kind+'-progress.json')).write_text(json.dumps(task,ensure_ascii=False,indent=2));previous=status
            if task['status']=='completed':print(kind+' completed: '+str(task['counts']),flush=True);return task
            if task['status'] in {'failed','partial','cancelled','paused'}:raise RuntimeError(kind+' requires attention: '+str(task['counts']))
            if task['processed']%100<8:print(kind+' '+str(task['processed'])+'/'+str(task['total']),flush=True)
            time.sleep(2)
    image=wait(args.image_task,'images')
    target=output/'index-created.json'
    if target.exists():index=json.loads(target.read_text())
    else:
        index=call(body={'kind':'index','runtime_mode':'configured'});target.write_text(json.dumps(index,ensure_ascii=False,indent=2))
    final=wait(index['task_id'],'index')
    (output/'completed.json').write_text(json.dumps({'image_task':image,'index_task':final},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
