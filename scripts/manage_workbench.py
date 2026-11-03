"""Inspect, start and normally stop this project's localhost services. Never installs or trains."""
import argparse,importlib.util,json,os,platform,shutil,signal,socket,sqlite3,subprocess,sys,time
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def node_binary():
    found=shutil.which('node');fallback=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    return found or (str(fallback) if fallback.is_file() else None)

def probe(port,path='/'):
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}'+path,timeout=2) as response:
            if port==8000:return json.load(response).get('metadata_store',{}).get('name')=='SQLite'
            return '制造能源工业知识库系统' in response.read(100000).decode(errors='replace')
    except (OSError,ValueError):return False

def occupied(port):
    with socket.socket() as connection:return connection.connect_ex(('127.0.0.1',port))==0

def process_identity(pid):
    """Identity is checked before signals; stale manifests cannot authorize a reused PID."""
    if not isinstance(pid,int) or pid<=1:return None
    try:
        if sys.platform.startswith('linux'):
            directory=Path('/proc')/str(pid);command=(directory/'cmdline').read_bytes().replace(b'\0',b' ').decode();cwd=(directory/'cwd').resolve()
        elif sys.platform=='darwin':
            command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True,stderr=subprocess.DEVNULL).strip()
            values=subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True,stderr=subprocess.DEVNULL).splitlines();cwd=Path(next(v[1:] for v in values if v.startswith('n'))).resolve()
        else:return None
        if cwd!=ROOT.resolve():return None
        if not any(v in command for v in ['scripts/run_local_stack.py','uvicorn app.main:app','node_modules/vite/bin/vite.js','scripts.serve_local_vision_limited','scripts/serve_local_vision.py','scripts.serve_local_vision_batch']):return None
        return {'pid':pid,'command':command,'project':str(cwd)}
    except (OSError,StopIteration,subprocess.CalledProcessError):return None

def owned_processes():
    rows={};directory=ROOT/'data/runtime/service-logs'
    if not directory.is_dir():return []
    for pattern in ['stack-*.json','frontend-*.json','vision-*.json','backend-*.json']:
        for path in directory.glob(pattern):
            if path.is_symlink():continue
            try:
                value=json.loads(path.read_text());identity=process_identity(value.get('pid'))
                command=value.get('command')
                if identity and isinstance(command,list) and command and all(isinstance(v,str) for v in command) and ' '.join(command).strip()==identity['command'].strip():rows[identity['pid']]={**identity,'manifest':str(path)}
            except (OSError,ValueError):continue
    return list(rows.values())

def environment_check():
    modules={name:bool(importlib.util.find_spec(name)) for name in ['fastapi','uvicorn','pydantic','yaml']};optional={name:bool(importlib.util.find_spec(name)) for name in ['fitz','PIL','docx','openpyxl','langgraph']};node=node_binary();version=None;node_ok=False
    if node:
        try:
            version=subprocess.check_output([node,'--version'],text=True,timeout=10).strip();parts=[int(v) for v in version.lstrip('v').split('.')];node_ok=parts[0]>22 or parts[0]==22 and parts[1]>=12 or parts[0]==20 and parts[1]>=19
        except (OSError,ValueError,subprocess.SubprocessError):pass
    with sqlite3.connect(':memory:') as db:sqlite_json=db.execute("SELECT json_extract('{\"ok\":1}','$.ok')").fetchone()[0]==1
    from scripts.serve_local_retrieval import EMBEDDING_MODEL,EMBEDDING_REVISION,RERANK_MODEL,RERANK_REVISION
    local_models=all((ROOT/p).exists() for p in ['.venv-mlx/bin/python','.venv-retrieval/bin/python','data/runtime/models/qwen3.5-9b-mlx-4bit/conversion.json']) and any((ROOT/'data/runtime/models/qwen3.5-9b-mlx-4bit').glob('*.safetensors'))
    for name,revision in [(EMBEDDING_MODEL,EMBEDDING_REVISION),(RERANK_MODEL,RERANK_REVISION)]:
        local_models=local_models and all((ROOT/'data/runtime/models'/name.replace('/','--')/revision/file).is_file() for file in ['config.json','model.safetensors','tokenizer.json'])
    vision_models=all((ROOT/p).is_file() for p in ['.venv-vision/bin/python','data/runtime/models/qwen3-vl-8b-mlx-4bit/config.json','data/runtime/models/google--siglip2-base-patch16-224/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2/config.json']) and any((ROOT/'data/runtime/models/qwen3-vl-8b-mlx-4bit').glob('*.safetensors'))
    required={'python_3_11_or_newer':sys.version_info>=(3,11),'backend_dependencies':all(modules.values()),'node_supported':node_ok,'frontend_dependencies':(ROOT/'node_modules/vite/bin/vite.js').is_file(),'sqlite_json':sqlite_json}
    return {'ready':all(required.values()),'required':required,'python':platform.python_version(),'node':version,'backend_modules':modules,'optional_modules':optional,'local_model_profile_prepared':local_models,'vision_profile_prepared':vision_models,'no_install_or_download_performed':True,'no_clean_environment_or_restore_claimed':True}

def status():return {'backend_ready':probe(8000,'/api/v2/workbench/status'),'frontend_ready':probe(8080),'owned_processes':owned_processes(),'url':'http://127.0.0.1:8080/#/dashboard'}

def spawn(kind,command,environment=None):
    directory=ROOT/'data/runtime/service-logs';directory.mkdir(parents=True,exist_ok=True);identity=uuid4().hex;log=directory/(kind+'-'+identity+'.log')
    with log.open('x') as handle:child=subprocess.Popen(command,cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,stdout=handle,stderr=handle,start_new_session=True)
    value={'pid':child.pid,'command':command,'project':str(ROOT),'log':str(log)};(directory/(kind+'-'+identity+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2));return value

def start(mode):
    if os.name!='posix':raise RuntimeError('Automatic lifecycle commands currently target macOS/Linux. On Windows use python backend/main.py and npm run dev in separate terminals; Ctrl-C stops each terminal.')
    check=environment_check()
    if not check['ready']:raise RuntimeError('Environment is incomplete; run check and install the documented dependencies first')
    if mode=='auto':mode='local' if check['local_model_profile_prepared'] else 'offline'
    state=status()
    if state['backend_ready'] and mode=='offline':
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8000/api/v2/workbench/status',timeout=2) as response:backend=json.load(response)
        if any(m['status']=='configured' for m in backend.get('models',[])):raise RuntimeError('A configured backend is already running; stop it explicitly before switching to offline mode')
    if mode=='local':
        if not check['local_model_profile_prepared']:raise RuntimeError('Existing local model profile is not prepared; no model download or training was attempted')
        if not state['backend_ready']:
            if occupied(8000):raise RuntimeError('Port8000 belongs to an unrecognized service; no process was stopped')
            command=[str(ROOT/'.venv-retrieval/bin/python'),str(ROOT/'scripts/run_local_stack.py'),'--background']
            if check['vision_profile_prepared']:command.append('--with-vision')
            subprocess.run(command,cwd=ROOT,check=True,capture_output=True,text=True)
            deadline=time.monotonic()+120
            while not probe(8000,'/api/v2/workbench/status'):
                if time.monotonic()>deadline:raise RuntimeError('Local backend did not become ready; inspect retained service logs')
                time.sleep(.5)
        subprocess.run([str(ROOT/'.venv-retrieval/bin/python'),str(ROOT/'scripts/start_workbench.py'),'--no-open'],cwd=ROOT,check=True,capture_output=True,text=True)
    else:
        if not state['backend_ready']:
            if occupied(8000):raise RuntimeError('Port8000 belongs to an unrecognized service; no process was stopped')
            env={**os.environ,'RAG_CHAT_URL':'','RAG_EMBEDDING_URL':'','RAG_RERANK_URL':'','RAG_CHAT_API_KEY':'','RAG_EMBEDDING_API_KEY':'','RAG_RERANK_API_KEY':'','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
            spawn('backend',[sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],env)
        if not state['frontend_ready']:
            if occupied(8080):raise RuntimeError('Port8080 belongs to an unrecognized service; no process was stopped')
            spawn('frontend',[node_binary(),str(ROOT/'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','8080','--strictPort'])
    deadline=time.monotonic()+120
    while not (probe(8000,'/api/v2/workbench/status') and probe(8080)):
        if time.monotonic()>deadline:raise RuntimeError('Workbench did not become ready; service logs and data were retained')
        time.sleep(.5)
    return {**status(),'mode':mode,'reused_existing_backend':state['backend_ready']}

def stop():
    if os.name!='posix':raise RuntimeError('Automatic stop is available on macOS/Linux; use Ctrl-C in the Windows launch terminals')
    database=ROOT/'data/runtime/knowledge.sqlite3'
    if database.is_file():
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tasks=db.execute("SELECT count(*) FROM background_tasks WHERE status IN ('queued','running','pause_requested','cancel_requested')").fetchone()[0] if 'background_tasks' in tables else 0
            answers=db.execute("SELECT count(*) FROM messages WHERE json_extract(payload,'$.status')='running'").fetchone()[0] if 'messages' in tables else 0
            if tasks or answers:raise RuntimeError('Pause active tasks and finish answers before stopping the workbench')
    processes=owned_processes();stopped=[]
    for row in processes:
        if process_identity(row['pid']) is None:continue
        os.kill(row['pid'],signal.SIGINT);stopped.append(row['pid'])
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        remaining=[pid for pid in stopped if process_identity(pid) is not None]
        if not remaining:break
        time.sleep(.25)
    if remaining:raise RuntimeError('Some verified services did not stop normally; no forced kill was used')
    return {'stopped_pids':stopped,'normal_signal':'SIGINT','forced_kill':False,'unrecognized_services_untouched':True,'data_or_files_deleted':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['check','status','start','stop']);parser.add_argument('--mode',choices=['auto','offline','local'],default='auto');parser.add_argument('--json',action='store_true');args=parser.parse_args()
    try:value=environment_check() if args.action=='check' else status() if args.action=='status' else start(args.mode) if args.action=='start' else stop()
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:value={'error':str(exc),'action':args.action};print(json.dumps(value,ensure_ascii=False,indent=2));return 1
    print(json.dumps(value,ensure_ascii=False,indent=2));return 1 if args.action=='check' and not value['ready'] else 0

if __name__=='__main__':raise SystemExit(main())
