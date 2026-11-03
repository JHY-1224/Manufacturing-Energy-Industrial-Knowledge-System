"""Audit every frozen structure candidate into NEW local evidence; never publish it."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.preprocessors.structured_tables import html_table, office_table, pdf_table, to_html, continuation_links, PENDING


def confined(root, relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Source path outside collection')
    return path


def write_json(path, value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def render_entry(entry, output):
    table=entry['layout'];preview=entry.get('preview');w,h=entry.get('page_size') or [1,1]
    image=''
    if preview:
        image=f'<svg id="page" viewBox="0 0 {w} {h}" role="img" aria-label="来源原页及单元格定位"><image href="../{escape(preview,quote=True)}" width="{w}" height="{h}"/><rect id="focus" fill="#ffd54d66" stroke="#d43b24" stroke-width="1.5"/></svg>'
    else:image='<p>此来源无固定 PDF 页码；按下方 DOM/OOXML 行列定位。不得将逻辑行号当页码。</p>'
    old='<table>'+''.join('<tr>'+''.join('<td>'+escape(str(v) if v is not None else '〔合并/未知〕')+'</td>' for v in row)+'</tr>' for row in entry['previous_rows'])+'</table>'
    locators=''.join('<tr><td>'+escape(c['cell_id'])+'</td><td>'+escape(c['text'])+'</td><td><code>'+escape(json.dumps(c['locator'],ensure_ascii=False))+'</code></td></tr>' for c in table['cells'])
    payload=json.dumps(table,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:15px/1.6 system-ui;color:#16293c;margin:20px}main{max-width:1500px;margin:auto}.compare{display:grid;grid-template-columns:1fr 1fr;gap:20px}.panel{min-width:0;overflow:auto}svg{width:100%;border:1px solid #ccd}td,th{border:1px solid #aab;padding:7px;vertical-align:top;white-space:pre-wrap}table{border-collapse:collapse}pre,code,p{overflow-wrap:anywhere;white-space:pre-wrap}td[data-cell]{cursor:pointer}td[data-cell]:hover{background:#fff1ad}@media(max-width:750px){.compare{display:block}} </style><main>'''
    page+=f'<h1>{escape(entry["candidate_id"])}</h1><p>自动核对完成，仍待人工审核。结论：{escape(entry["automatic_classification"])}；异常：{escape(", ".join(entry["flags"]) or "未触发结构规则异常")}</p>'
    page+='<p>点击新表单元格在原页标记位置。坐标、上下标与合并关系是来源解析证据；不构成人工批准或公式求值。</p>'
    page+=f'<p><a href="{escape(entry["original_uri"],quote=True)}">来源原件</a> · SHA256 {entry["source_sha256"]}</p><div class="compare"><div class="panel">'+image+'</div><div class="panel"><h2>本轮结构</h2>'+to_html(table)+'</div></div>'
    page+='<details><summary>上一版提取（保留差异）</summary>'+old+'</details><details><summary>单元格、公式原始表示与定位</summary><table>'+locators+'</table><pre>'+escape(json.dumps(table,ensure_ascii=False,indent=2))+'</pre></details>'
    page+='<script>const t='+payload+''';document.querySelectorAll('td[data-cell]').forEach(el=>el.onclick=()=>{const c=t.cells.find(x=>x.cell_id===el.dataset.cell);const r=document.querySelector('#focus');if(r&&c.bbox){const [x,y,x1,y1]=c.bbox;Object.entries({x,y,width:x1-x,height:y1-y}).forEach(([k,v])=>r.setAttribute(k,v))}});</script></main></html>'''
    page=page.replace('</style>','h1{font-size:24px;overflow-wrap:anywhere}details{max-width:100%;overflow:auto}</style>')
    with (output/'entries'/(entry['candidate_id']+'.html')).open('x',encoding='utf-8') as f:f.write(page)


def audit(collection, output):
    import fitz
    root=Path(collection).resolve();out=Path(output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    for folder in ('entries','previews','records'):(out/folder).mkdir()
    index=json.loads((root/'table-index.json').read_text());candidates=[v for v in index if not v['checked']]
    manifest={v['id']:v for v in (json.loads(line) for line in (root/'manifest.jsonl').read_text().splitlines())}
    verified={};pdfs={};page_cache={};entries=[]
    try:
        for item in candidates:
            source=manifest[item['source_id']];original=confined(root,source['file'])
            if source['id'] not in verified:
                checksum=sha256(original.read_bytes()).hexdigest()
                if checksum!=source['sha256']:raise ValueError('Source checksum changed: '+source['id'])
                verified[source['id']]=checksum
            artifact=confined(root,item['json']);old=json.loads(artifact.read_text());identity=item['source_id']+'__'+item['table_id']
            flags=[];preview=None;page_size=None
            if original.suffix=='.pdf':
                if source['id'] not in pdfs:pdfs[source['id']]=fitz.open(original)
                pdf=pdfs[source['id']];page=pdf[old['page']-1];key=(source['id'],old['page'])
                if key not in page_cache:
                    page_cache[key]=[pdf_table(page,t,f'p{old["page"]:04d}_table{i:02d}') for i,t in enumerate(page.find_tables().tables,1)]
                    preview=f'previews/{source["id"]}__p{old["page"]:04d}.png'
                    page.get_pixmap(matrix=fitz.Matrix(1.25,1.25),alpha=False).save(out/preview)
                def distance(t):return sum(abs(a-b) for a,b in zip(t['bbox'],old['bbox']))
                matches=page_cache[key]
                if not matches or min(map(distance,matches))>8:raise ValueError('Frozen table geometry no longer reproducible: '+identity)
                layout=json.loads(json.dumps(min(matches,key=distance)));layout['table_id']=item['table_id']
                for cell in layout['cells']:cell['cell_id']=item['table_id']+':r'+str(cell['row']+1)+'c'+str(cell['col']+1)
                top=layout['bbox'][1];layout['context_before']=page.get_text('text',clip=(0,max(0,top-100),page.rect.width,top))
                preview=f'previews/{source["id"]}__p{old["page"]:04d}.png';page_size=layout['page_size']
            elif old.get('original_ooxml'):layout=office_table(old['original_ooxml'],item['table_id'])
            elif old.get('original_html'):layout=html_table(old['original_html'],item['table_id'])
            else:raise ValueError('No original structure: '+identity)
            flags.extend(layout['flags'])
            # Compare textual coverage only; this cannot prove cell association correctness.
            from html import unescape
            before=''.join(''.join(str(v or '') for v in row) for row in old['rows'])
            after=''.join(c['text'] for c in layout['cells'])
            import re
            norm=lambda text:re.sub(r'\s+','',unescape(text))
            same=Counter(norm(before))==Counter(norm(after))
            if not same:flags.append('text_or_script_representation_changed')
            if (layout['row_count'],layout['col_count'])!=(old['row_count'],old['col_count']):flags.append('grid_dimensions_changed')
            if any(c['rowspan']>1 or c['colspan']>1 for c in layout['cells']):flags.append('merged_cells_require_review')
            classification='structure_candidate'
            if 'image_or_formula_requires_source' in flags:classification='image_or_formula_layout_container'
            elif 'no_cell_text' in flags or 'sparse_grid_possible_diagram' in flags:classification='sparse_or_graphic_candidate'
            elif flags:classification='structure_with_review_flags'
            entry=dict(candidate_id=identity,source_id=source['id'],table_id=item['table_id'],source_sha256=source['sha256'],
                       source_artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),original_uri=original.as_uri(),
                       preview=preview,page_size=page_size,previous_rows=old['rows'],layout=layout,
                       flags=sorted(set(flags)),automatic_classification=classification,text_multiset_preserved=same,
                       review_status=PENDING,human_approved=False,training_allowed=False)
            write_json(out/'records'/(identity+'.json'),entry);render_entry(entry,out);entries.append(entry)
        links=[]
        for sid in verified:
            tables=[e['layout'] for e in entries if e['source_id']==sid and e['layout'].get('page')]
            links.extend({'source_id':sid,**v} for v in continuation_links(tables))
        summary=dict(candidates=len(entries),expected_candidates=len(candidates),source_files_verified=len(verified),
                     source_pages_rendered=len(page_cache),cells=sum(len(e['layout']['cells']) for e in entries),
                     classification_counts=dict(Counter(e['automatic_classification'] for e in entries)),
                     flag_counts=dict(Counter(f for e in entries for f in e['flags'])),
                     continuation_links=links,review_status=PENDING,human_approved=0,training_allowed=False,
                     table_index_sha256=sha256((root/'table-index.json').read_bytes()).hexdigest(),
                     implementation_sha256={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scripts/audit_table_candidates.py', ROOT/'app/preprocessors/structured_tables.py')},
                     candidates_index=[{k:e[k] for k in ('candidate_id','source_id','table_id','flags','automatic_classification','review_status')} for e in entries])
        write_json(out/'audit.json',summary)
        import csv
        with (out/'manual-review-template.csv').open('x',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['candidate_id','source_sha256','automatic_classification','flags','review_status','reviewer','reviewed_at','decision','note'])
            for entry in entries:writer.writerow([entry['candidate_id'],entry['source_sha256'],entry['automatic_classification'],';'.join(entry['flags']),PENDING,'','','',''])
        cards=''.join('<li><a href="entries/'+escape(e['candidate_id'])+'.html">'+escape(e['candidate_id'])+'</a> · '+escape(e['automatic_classification'])+'<p>'+escape(', '.join(e['flags']) or '未触发自动异常规则')+'</p></li>' for e in entries)
        with (out/'index.html').open('x',encoding='utf-8') as f:f.write('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{max-width:1100px;margin:auto;padding:24px;font:16px/1.7 system-ui}li{padding:10px;border-bottom:1px solid #ccd;overflow-wrap:anywhere}input{font:inherit;width:95%;padding:10px}</style><h1>284项结构候选核对材料</h1><p>全部逐项自动核对，0项人工批准。含原页、单元格位置、上下标和合并关系；差异与疑似非表格单列。自动通过不代表工业事实核验。</p><input placeholder="筛选来源、表号或异常" aria-label="筛选" oninput="document.querySelectorAll(\'li\').forEach(e=>e.hidden=!e.textContent.toLowerCase().includes(this.value.toLowerCase()))"><ul>'+cards+'</ul></html>')
        return {k:v for k,v in summary.items() if k not in {'candidates_index','continuation_links'}}
    finally:
        for pdf in pdfs.values():pdf.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--collection',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(audit(a.collection,a.output),ensure_ascii=False))
