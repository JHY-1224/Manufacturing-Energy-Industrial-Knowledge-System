"""Build a local case-review sheet; decisions require explicit user input and export."""
import argparse
from html import escape
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def build(cases_path, store, output, source_previews=False):
    from app.evaluation.quality_benchmark import load_quality_cases, validate_quality_sources
    from app.core.models import RagChunk
    from types import SimpleNamespace
    from app.storage.knowledge_store import digest
    cases=load_quality_cases(cases_path);output=Path(output)
    if output.exists():raise FileExistsError('Choose a new review sheet path')
    database=(Path(store)/'knowledge.sqlite3').resolve()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        chunks={r[0]:json.loads(r[1]) for r in db.execute('SELECT chunk_id,payload FROM chunks')}
        originals={}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():
            originals={r[0]:json.loads(r[1]).get('local_path') for r in db.execute('SELECT document_id,payload FROM documents')}
    validate_quality_sources(SimpleNamespace(refresh=lambda:None,chunks=[RagChunk.model_validate(c) for c in chunks.values()]),cases)
    assets={}
    if source_previews:
        from scripts.quality_review_assets import build_assets
        assets=build_assets(cases,chunks,output.with_name(output.stem+'-assets'),ROOT,originals)
    cards=[]
    for case in cases:
        refs=[chunks[i] for i in case.expected_chunk_ids if i in chunks]
        body=''.join('<details><summary>'+escape(c['chunk_id'])+' · Page '+escape(str(c['metadata'].get('page') or '—'))+'</summary><pre>'+escape(c['content'])+'</pre><a target="_blank" rel="noopener noreferrer" href="http://127.0.0.1:8000/api/v2/workbench/documents/'+escape(c['doc_id'],quote=True)+'/source?revision_id='+escape(c['metadata'].get('revision_id',''),quote=True)+'">查看该修订来源原件</a></details>' for c in refs)
        scope=escape('知识库='+case.knowledge_base_id+'；文档限定='+(case.document_id or '无')+'；审核范围='+case.review_mode)
        body=assets.get(case.query_id,'')+body
        cards.append('<article data-id="'+escape(case.query_id,quote=True)+'"><small>'+escape(case.query_id+' · '+case.knowledge_base_id+' · '+case.category)+'</small><h2>'+escape(case.question)+'</h2><p>'+scope+'</p><p>预期：'+('无证据并拒答' if case.expected_empty else escape(' / '.join(case.required_evidence)))+'</p><p>'+escape(case.limitation)+'</p>'+body+'<label>决定 <select><option value="pending">待人工核对</option><option value="approved">通过此评测用例</option><option value="rejected">退回修改</option></select></label><label><input type="checkbox"> 已打开原件，核对页码/图片及冻结修订</label><label><input type="checkbox"> 问题与知识库、文档、审核过滤范围一致</label><label><input type="checkbox"> 答案、行列、数值、单位、大小写和上下标一致</label><label><input type="checkbox"> 脚注、适用条件及未知项已核对；未将模板当实际参数</label><textarea placeholder="本次核对说明；通过或退回时必填" rows="2"></textarea></article>')
    payload=json.dumps({'dataset_sha256':digest(Path(cases_path).read_bytes())},ensure_ascii=False).replace('<','\\u003c')
    script='''<script>const identity=PAYLOAD;document.querySelector('#export').onclick=()=>{try{const reviewer=document.querySelector('#reviewer').value.trim();if(!reviewer)throw Error('请填写审核人');const decisions=[];for(const card of document.querySelectorAll('article')){const decision=card.querySelector('select').value;if(decision==='pending')continue;const note=card.querySelector('textarea').value.trim(),verified=[...card.querySelectorAll('input[type=checkbox]')].every(x=>x.checked);if(!note||(decision==='approved'&&!verified))throw Error('请完成所选用例的核对勾选与说明');decisions.push({query_id:card.dataset.id,decision,note,verified_source:verified})}if(!decisions.length)throw Error('尚未明确审核任何用例');const value={...identity,reviewer,reviewed_at:new Date().toISOString(),decisions};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.download='quality-review-decisions.json';a.click();document.querySelector('#status').textContent='已导出 '+decisions.length+' 条人工决定；原候选集保持不变。'}catch(e){document.querySelector('#status').textContent=e.message}};</script>'''.replace('PAYLOAD',payload)
    draft_script='''<script>const key='kb-quality-review:'+identity.dataset_sha256;const collect=()=>({dataset_sha256:identity.dataset_sha256,reviewer:document.querySelector('#reviewer').value,cards:[...document.querySelectorAll('article')].map(c=>({id:c.dataset.id,decision:c.querySelector('select').value,note:c.querySelector('textarea').value,checks:[...c.querySelectorAll('input')].map(x=>x.checked)}))});document.querySelector('#save').onclick=()=>{try{localStorage.setItem(key,JSON.stringify(collect()));document.querySelector('#status').textContent='草稿已保存在本机浏览器；未导出决定或发布资料。'}catch(e){document.querySelector('#status').textContent='浏览器不允许本地保存，请导出明确决定。'}};document.querySelector('#restore').onclick=()=>{try{const v=JSON.parse(localStorage.getItem(key)||'null');if(!v||v.dataset_sha256!==identity.dataset_sha256)throw Error('没有匹配此冻结集的草稿');document.querySelector('#reviewer').value=v.reviewer||'';for(const c of document.querySelectorAll('article')){const d=v.cards.find(x=>x.id===c.dataset.id);if(!d)continue;c.querySelector('select').value=['pending','approved','rejected'].includes(d.decision)?d.decision:'pending';c.querySelector('textarea').value=d.note||'';[...c.querySelectorAll('input')].forEach((x,i)=>x.checked=d.checks?.[i]===true)}document.querySelector('#status').textContent='已恢复本机人工草稿；尚未提交或发布。'}catch(e){document.querySelector('#status').textContent=e.message}};document.querySelector('#search').oninput=e=>document.querySelectorAll('article').forEach(c=>c.hidden=!c.textContent.toLowerCase().includes(e.target.value.toLowerCase()));</script>'''
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知识质量评测用例审核</title><style>body{font:15px/1.7 system-ui;background:#f4f7fb;color:#18273b;margin:0}main{max-width:1080px;margin:auto;padding:24px}header,article{background:white;padding:20px;border:1px solid #d8e2ef;border-radius:14px;margin-bottom:16px}h2{font-size:18px}pre{white-space:pre-wrap;overflow-wrap:anywhere}label{display:block;margin:12px 0}textarea{width:100%;box-sizing:border-box}input,select,textarea,button{font:inherit}button{padding:10px}small,p{overflow-wrap:anywhere}img{max-width:100%;max-height:700px;object-fit:contain}.table-scroll{overflow:auto}table{border-collapse:collapse}td{border:1px solid #aab;padding:8px;min-width:90px}input:not([type=checkbox]){max-width:90%}</style><main><header><h1>知识质量评测用例审核</h1><p>本页不调用模型、不发布知识资料。逐项查看来源并记录人工决定；导出后用 apply_quality_review.py 生成新的已审核测试集。来源资料的发布审核与评测用例审核分别处理。</p><label>审核人 <input id="reviewer"></label><label>搜索案例 <input id="search" placeholder="问题、行业或案例编号"></label><button id="save">保存审核草稿</button> <button id="restore">恢复本机草稿</button> <button id="export">导出已明确的人工决定</button><p id="status" role="status"></p></header>'+''.join(cards)+'</main>'+script+draft_script+'</html>'
    page=page.replace('</style>','summary,article,header,h2,code{overflow-wrap:anywhere;min-width:0}button{max-width:100%}</style>')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as handle:handle.write(page)
    return {'cases':len(cases),'output':str(output.resolve()),'automatic_approval':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--store',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source-previews',action='store_true')
    args=parser.parse_args();print(json.dumps(build(args.cases,args.store,args.output,args.source_previews),ensure_ascii=False))
