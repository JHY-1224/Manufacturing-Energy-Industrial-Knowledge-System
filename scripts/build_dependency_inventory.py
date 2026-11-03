"""Collect declared Python profiles and npm lock licenses from installed metadata; no installs."""
import argparse,importlib.metadata,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def build():
    distributions={}
    for env in ['.venv-retrieval','.venv-mlx','.venv-vision','.venv']:
        for site in (ROOT/env/'lib').glob('python*/site-packages'):
            for dist in importlib.metadata.distributions(path=[str(site)]):
                key=(re.sub(r'[-_.]+','-',dist.metadata['Name']).lower(),dist.version)
                if key in distributions:continue
                license_name=dist.metadata.get('License-Expression') or dist.metadata.get('License') or '; '.join(v for v in dist.metadata.get_all('Classifier',[]) if v.startswith('License ::')) or 'not declared in installed metadata'
                # Some projects put a complete license in License; preserve an identifier/summary only.
                if len(license_name)>500:license_name=license_name.splitlines()[0][:200]+' (full text in distribution metadata)'
                distributions[key]={'name':dist.metadata['Name'],'version':dist.version,'license_metadata':license_name,'project_urls':dist.metadata.get_all('Project-URL',[]),'homepage':dist.metadata.get('Home-page'),'source':'installed distribution metadata; packages not bundled'}
    profiles={};missing=[];used={}
    paths=sorted([*ROOT.glob('backend/requirements*.txt'),*ROOT.glob('scripts/requirements*.txt'),*ROOT.glob('scripts/local_model/requirements*.txt')])
    for path in paths:
        entries=[]
        for line in path.read_text().splitlines():
            value=line.split('#',1)[0].strip()
            if not value:continue
            match=re.match(r'^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?(==|>=)([^; ]+)',value)
            if not match:raise ValueError('Unsupported requirement: '+value)
            name,op,version=match.groups();norm=re.sub(r'[-_.]+','-',name).lower();matches=[k for k in distributions if k[0]==norm and (op!='==' or k[1]==version)]
            if not matches:missing.append({'profile':path.relative_to(ROOT).as_posix(),'requirement':value})
            for key in matches:used[key]=distributions[key]
            entries.append({'requirement':value,'recorded_versions':sorted({k[1] for k in matches})})
        profiles[path.relative_to(ROOT).as_posix()]=entries
    lock=json.loads((ROOT/'package-lock.json').read_text());npm=[]
    for path,item in sorted(lock.get('packages',{}).items()):
        if not path:continue
        npm.append({'package_path':path,'name':item.get('name') or path.rsplit('node_modules/',1)[-1],'version':item.get('version'),'license':item.get('license','not declared in lockfile'),'dev':item.get('dev',False),'optional':item.get('optional',False),'resolved':item.get('resolved'),'integrity':item.get('integrity')})
    return {'schema_version':1,'generated_on':'2026-11-03','project_license':'AGPL-3.0-only','scope':'declared Python profiles and complete npm lockfile; binaries and model weights not distributed','python_profiles':profiles,'python_distributions':sorted(used.values(),key=lambda v:(v['name'].lower(),v['version'])),'python_metadata_unavailable':missing,'npm_packages':npm,'metadata_is_not_a_license_compatibility_opinion':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'docs/dependency-inventory.json');args=parser.parse_args();result=build();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'python_distributions':len(result['python_distributions']),'npm_packages':len(result['npm_packages']),'missing':result['python_metadata_unavailable']},ensure_ascii=False));return bool(result['python_metadata_unavailable'])
if __name__=='__main__':raise SystemExit(main())
