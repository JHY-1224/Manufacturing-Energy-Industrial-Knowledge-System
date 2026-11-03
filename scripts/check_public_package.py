"""Check an explicit public source allowlist; never read .env or runtime assets."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOP={'.env.example','.gitignore','.npmrc','.prettierrc','AGENTS.md','README.md','LICENSE','THIRD_PARTY_NOTICES.md','package.json','package-lock.json','vite.config.ts','tsconfig.json','tsconfig.app.json','tsconfig.node.json','design-qa.md','启动知识库.command'}
DIRECTORIES={'app','backend','frontend','scripts','tests','.github','docs'}
EXTENSIONS={'.py','.swift','.vue','.js','.mjs','.ts','.css','.html','.json','.jsonl','.yaml','.yml','.txt','.md','.gitkeep'}
DENIED_PARTS={'node_modules','dist','build','__pycache__','uploaded_documents','.git','.npm-cache'}
PATTERNS=[('private_key',re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{40,}')),('github_token',re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b')),('github_pat',re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b')),('provider_key',re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b')),('private_home',re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'))]

def allowed(path):
    p=Path(path)
    if p.is_absolute() or '..' in p.parts or any(v in DENIED_PARTS or v.startswith('.venv') for v in p.parts):return False
    if path in TOP:return True
    if path in {'data/graph/.gitkeep','data/raw/.gitkeep','data/processed/.gitkeep','data/eval_sets/energy_rag_eval.jsonl','reports/eval_reports/sample_report.md','indexes/bm25/.gitkeep','indexes/chroma/.gitkeep','indexes/parent_child/.gitkeep','indexes/summary/.gitkeep'}:return True
    return bool(p.parts and p.parts[0] in DIRECTORIES and p.suffix in EXTENSIONS and not p.name.startswith('.env'))

def check(root=ROOT,paths=None):
    if paths is None:paths=sorted(set(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().strip('\0').split('\0')))
    errors=[];files=[]
    for name in paths:
        if not name:continue
        p=root/name
        if not allowed(name):errors.append({'path':name,'problem':'outside_public_allowlist'});continue
        if p.is_symlink() or not p.is_file():errors.append({'path':name,'problem':'not_regular_file'});continue
        raw=p.read_bytes()
        if len(raw)>2_000_000:errors.append({'path':name,'problem':'file_too_large'});continue
        try:content=raw.decode('utf-8')
        except UnicodeDecodeError:errors.append({'path':name,'problem':'non_text_asset'});continue
        if '\0' in content:errors.append({'path':name,'problem':'binary_content'});continue
        for kind,pattern in PATTERNS:
            if pattern.search(content):errors.append({'path':name,'problem':kind})
        files.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    return {'passed':not errors,'files':files,'errors':errors,'private_runtime_read':False,'scope':'current source tree only; manual content and licensing review also required'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path);args=parser.parse_args();result=check()
    if args.manifest:args.manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'files':len(result['files']),'bytes':sum(v['bytes'] for v in result['files']),'errors':result['errors']},ensure_ascii=False,indent=2));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
