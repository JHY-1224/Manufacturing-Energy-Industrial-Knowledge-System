"""Start/reuse verified localhost services and the existing Vue workbench."""
import argparse,json,os,shutil,socket,subprocess,sys,time,webbrowser
from pathlib import Path
from urllib.request import ProxyHandler,build_opener
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_local_stack import require_free,healthy

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-open',action='store_true');args=parser.parse_args()
    opener=build_opener(ProxyHandler({}))
    def ready(port):
        try:
            with opener.open(f'http://127.0.0.1:{port}'+('/api/v2/workbench/status' if port==8000 else '/'),timeout=2) as response:
                if port==8000:return json.load(response).get('metadata_store',{}).get('name')=='SQLite'
                return '制造能源工业知识库系统' in response.read().decode()
        except (OSError,ValueError):return False
    # Optional prepared vision models are local assets, not mandatory downloads.
    visual_python=ROOT/'.venv-vision/bin/python'
    visual_model=ROOT/'data/runtime/models/qwen3-vl-8b-mlx-4bit'
    cross_model=ROOT/'data/runtime/models/google--siglip2-base-patch16-224/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2'
    if visual_python.is_file() and (visual_model/'config.json').is_file() and (cross_model/'config.json').is_file() and not healthy('vision',8083):
        try:
            require_free(8083)
            logs=ROOT/'data/runtime/service-logs';logs.mkdir(parents=True,exist_ok=True);identity=uuid4().hex
            command=[str(visual_python),'-m','scripts.serve_local_vision_limited','--vision',str(visual_model),'--embedding',str(cross_model)]
            with (logs/('vision-'+identity+'.log')).open('x') as log:
                worker=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            (logs/('vision-'+identity+'.json')).write_text(json.dumps({'pid':worker.pid,'port':8083,'command':command,'project':str(ROOT)},ensure_ascii=False,indent=2))
            deadline=time.monotonic()+90
            while not healthy('vision',8083):
                if worker.poll() is not None or time.monotonic()>deadline:raise RuntimeError('图片语义服务未就绪，请查看保留的服务日志')
                time.sleep(.5)
        except (RuntimeError,OSError) as exc:
            print('可选图片语义服务未启动，继续启动文本工作台：'+str(exc),flush=True)
    if not ready(8000):
        require_free(8000)
        subprocess.run([str(ROOT/'.venv-retrieval/bin/python'),str(ROOT/'scripts/run_local_stack.py'),'--background'],cwd=ROOT,check=True)
    if not ready(8080):
        require_free(8080)
        node=shutil.which('node')
        fallback=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        if not node and fallback.is_file():node=str(fallback)
        if not node or not (ROOT/'node_modules/vite/bin/vite.js').is_file():raise RuntimeError('Node/Vite 未准备；请按 AGENTS.md 安装前端依赖')
        directory=ROOT/'data/runtime/service-logs';directory.mkdir(parents=True,exist_ok=True);identity=uuid4().hex
        log=directory/('frontend-'+identity+'.log')
        command=[node,str(ROOT/'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','8080','--strictPort']
        with log.open('x') as handle:process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=handle,stderr=handle,start_new_session=True)
        (directory/('frontend-'+identity+'.json')).write_text(json.dumps({'pid':process.pid,'command':command,'log':str(log)},ensure_ascii=False,indent=2))
    deadline=time.monotonic()+120
    while not (ready(8000) and ready(8080)):
        if time.monotonic()>deadline:raise RuntimeError('服务未在 120 秒内就绪，请查看 data/runtime/service-logs 日志；未删除或停止任何其他服务')
        time.sleep(.5)
    print('制造能源工业知识库系统已就绪：http://127.0.0.1:8080/#/dashboard')
    if not args.no_open:webbrowser.open('http://127.0.0.1:8080/#/dashboard')

if __name__=='__main__':main()
