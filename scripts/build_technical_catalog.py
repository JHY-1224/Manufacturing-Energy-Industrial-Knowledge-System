"""Build a local, source-linked professional document collection catalog."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from urllib.parse import quote
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.collection.technical_extract import table_html


CSS = '''*{box-sizing:border-box}body{margin:0;background:#f1f5fb;color:#152139;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:1240px;margin:auto;padding:32px 24px 64px}header,.card,.panel{background:white;border:1px solid #d9e4fa;border-radius:22px;padding:24px;margin-bottom:20px}.eyebrow{font-size:12px;letter-spacing:1.5px;color:#2364ed;font-weight:700}h1{font-size:30px;line-height:1.3}h2{font-size:21px}h3{font-size:18px}p{margin:8px 0;color:#64748b}.stats,.links,.controls{display:flex;gap:12px;flex-wrap:wrap}.stats b{font-size:24px;color:#1c55ce}.stats span{background:#f2f6ff;border-radius:14px;padding:12px 18px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{margin:0}.card h2{margin:8px 0}.chip{background:#edf4ff;color:#2159c7;padding:4px 10px;border-radius:12px;font-size:12px}a{color:#1c5ed5;overflow-wrap:anywhere}button,select,input{font:inherit;border:1px solid #c9d9f2;background:white;border-radius:10px;padding:9px 12px}input{flex:1;min-width:180px}button{cursor:pointer}small{color:#64748b}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8fc;padding:16px;border-radius:10px;font:13px/1.7 ui-monospace,monospace}details{padding:12px 0;border-bottom:1px solid #e3eaf5}summary{cursor:pointer;font-weight:600}.figures{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.figures img{max-width:100%;max-height:230px;object-fit:contain}figure{margin:0;border:1px solid #e4e9f3;padding:10px;border-radius:12px}figcaption{font-size:12px;color:#65738b;overflow-wrap:anywhere}.links a{display:inline-block;background:#edf4ff;border-radius:8px;padding:6px 10px}.muted{color:#718098}.hidden,[hidden]{display:none!important}@media(max-width:700px){main{padding:16px 12px}.grid{grid-template-columns:1fr}.figures{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:25px}header,.card,.panel{padding:18px}}'''


def link(path, label, download=False):
    return '<a href="' + quote(str(path), safe='/') + '"' + (' download' if download else '') + '>' + escape(label) + '</a>'


def html_page(title, body, script=''):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + CSS + '</style><main>' + body + '</main>' + script + '</html>'


def build(root):
    root = Path(root).resolve()
    records = [json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines()]
    summary = json.loads((root / 'collection-summary.json').read_text())
    catalog = root / 'catalog'; catalog.mkdir(exist_ok=True)
    (catalog / 'tables').mkdir(exist_ok=True)
    cards = []; table_index = []; figures = set()
    for record in records:
        prepared = root / record['prepared_file']; data = json.loads(prepared.read_text())
        base = (root / data['base_extraction']).parent if data.get('base_extraction') else prepared.parent
        table_links = []; reviewed_links = []
        for table in data['tables']:
            ident = table['table_id']; folder = prepared.parent / 'tables'
            if not (folder / (ident + '.json')).exists(): folder = base / 'tables'
            checked = table.get('structure_review', '').startswith(('agent_visual', 'source_dictionary')) or record['reference_type'] == 'dataset_archive'
            row = {'source_id': record['id'], 'table_id': ident, 'checked': checked, 'page': table.get('page'),
                'source_url': record['resolved_url'], 'json': str((folder / (ident + '.json')).relative_to(root)),
                'csv': str((folder / (ident + '.csv')).relative_to(root)), 'html': str((folder / (ident + '.html')).relative_to(root))}
            table_index.append(row)
            cells = json.loads((folder / (ident + '.json')).read_text())
            view_name = record['id'] + '__' + ident + '.html'
            row['view_html'] = 'catalog/tables/' + view_name
            table_title = table.get('title', record['title_zh'] + ' · ' + ident)
            table_body = '<header><div class="eyebrow">TABLE · SOURCE EVIDENCE</div><h1>' + escape(table_title) + '</h1><p>' + escape(record['publisher'] + ' · ' + record['version']) + '</p><p>' + ('逐格核对 / 原始字段字典' if checked else '结构候选 · 尚未逐格核对，请与原件比对') + '</p><div class="links">' + link('../' + record['id'] + '.html', '返回资料') + link('../../' + record['file'], '原件下载', True) + link('../../' + row['csv'], 'CSV', True) + '</div></header><section class="panel"><div class="table-scroll">' + cells.get('structured_html', table_html(cells['rows'])) + '</div><h2>适用范围、脚注与来源上下文</h2><pre>' + escape(cells.get('context', '参见原文件与来源定位。')) + '</pre><p>PDF文件页：' + escape(str(cells.get('page') or '不适用')) + ' · Source SHA256：' + record['sha256'] + '</p></section>'
            table_style = '<style>.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:620px;font-size:14px}td,th{border:1px solid #cad7ea;padding:10px;vertical-align:top;white-space:pre-wrap}tr:first-child{background:#eaf1ff;font-weight:600}tr:nth-child(even){background:#f8faff}</style>'
            (catalog / 'tables' / view_name).write_text(html_page(table_title, table_body + table_style))
            entry = '<p>' + escape(table.get('title', ident)) + (' · PDF页 ' + str(table['page']) if table.get('page') else '') + ' · ' + link('tables/' + view_name, '查看表格') + ' · ' + link('../' + row['csv'], 'CSV', True) + ' · ' + link('../' + row['json'], '单元格与溯源JSON', True) + '</p>'
            (reviewed_links if checked else table_links).append(entry)
        gallery = []
        for figure in data['figures']:
            if not figure.get('file'): continue
            file = base / figure['file']; figures.add(figure['sha256'])
            gallery.append('<figure><a href="' + quote('../' + str(file.relative_to(root)), safe='/') + '"><img loading="lazy" alt="原文嵌图" src="' + quote('../' + str(file.relative_to(root)), safe='/') + '"></a><figcaption>' + escape(figure.get('caption') or ('PDF页 ' + str(figure.get('page')) if figure.get('page') else '原文嵌图；未推断语义')) + '</figcaption></figure>')
        header = '<header><div class="eyebrow">SOURCE REFERENCE</div><h1>' + escape(record['title_zh']) + '</h1><p>' + escape(record['title']) + '</p><p>' + escape(record['publisher'] + ' · ' + record['version']) + '</p><p>' + escape(record['industry_name']) + ' · ' + str(record['chunks']) + ' 个证据块</p><div class="links">' + link('../index.html', '返回目录') + link('../' + record['file'], '原件下载', True) + '<a href="' + escape(record['resolved_url'], quote=True) + '">发布方原始来源</a>' + link('../' + record['prepared_file'], '处理记录JSON', True) + '</div></header>'
        body = header + '<section class="panel"><h2>表格与数据字典</h2><p>核对的是来源字词、单元格及脚注。工程适用性、生产参数和法规现行性仍须按实际业务审核。</p>' + (''.join(reviewed_links) or '<p>本资料暂无逐格核对表。</p>') + '<details><summary>机器/Office结构候选 ' + str(len(table_links)) + ' 项 · 尚未全部逐格核对</summary><p>PDF自动检测可能包含示意图或拆错的列；这些猜测网格没有作为准确表格发布到检索证据，保留原页文本供回看。</p>' + ''.join(table_links) + '</details></section>'
        if data.get('dataset_validation'):
            body += '<section class="panel"><h2>原始数据检查</h2><pre>' + escape(json.dumps(data['dataset_validation'], ensure_ascii=False, indent=2)) + '</pre></section>'
        if gallery:
            body += '<section class="panel"><details><summary>原文图片 ' + str(len(gallery)) + ' 个出现位置</summary><div class="figures">' + ''.join(gallery) + '</div></details></section>'
        body += '<section class="panel"><h2>正文与来源定位</h2><p>英文原文未伪装为中文译文；目录标题为中文整理标签。表格HTML可通过上方“查看表格”阅读。</p>'
        for i, chunk in enumerate(data['chunks'], 1):
            m = chunk['metadata']; label = f'{i} · ' + str(m.get('section', '正文'))
            body += '<details><summary>' + escape(label) + '</summary><small>' + escape(str(m.get('evidence_kind', 'source_text')) + ' / ' + str(m.get('structure_review', '原文提取'))) + '</small><pre>' + escape(chunk['content']) + '</pre></details>'
        body += '</section><p>原件SHA256：' + record['sha256'] + '</p><p>' + escape(record['license_status']) + '</p>'
        (catalog / (record['id'] + '.html')).write_text(html_page(record['title_zh'], body))
        search = ' '.join([record['title_zh'], record['title'], record['publisher'], ' '.join(record['topics'])])
        cards.append('<article class="card" data-base="' + record['knowledge_base_id'] + '" data-search="' + escape(search.lower(), quote=True) + '"><span class="chip">' + escape(record['industry_name']) + '</span><h2>' + escape(record['title_zh']) + '</h2><p>' + escape(record['publisher']) + '</p><small>' + escape(record['version']) + '</small><p>' + escape(' / '.join(record['topics'])) + '</p><p>' + (str(record['pdf_pages']) + ' PDF页 · ' if record['pdf_pages'] else '') + str(record['checked_tables']) + ' 重点表/字典 · ' + str(record['chunks']) + ' 证据块</p><div class="links">' + link('catalog/' + record['id'] + '.html', '阅读与表格') + link(record['file'], '原件', True) + '<a href="' + escape(record['resolved_url'], quote=True) + '">来源</a></div></article>')
    groups = {r['knowledge_base_id']: r['industry_name'] for r in records}
    options = ''.join('<option value="' + k + '">' + escape(v) + '</option>' for k, v in groups.items())
    head = '<header><div class="eyebrow">MANUFACTURING · PROFESSIONAL REFERENCES</div><h1>制造能源工业 · 专业文本资料</h1><p>六大行业的首批专业资料，另含通用单位与符号。保留原件、正文、图表、字段与来源。</p><div class="stats"><span><b>28</b> 份文档</span><span><b>2</b> 套完整数据集</span><span><b>' + str(summary['pdf_pages']) + '</b> PDF页</span><span><b>14 + 2</b> 核对表格 / 数据字典</span></div><p>原始来源优先。入库可检索、工程审核、模型训练是分别记录的状态。</p></header>'
    body = head + '<section class="panel"><div class="controls"><select id="base" aria-label="行业筛选"><option value="">全部行业</option>' + options + '</select><input id="q" aria-label="资料搜索" placeholder="搜索：回流焊、工艺验证、蒸汽、单位…"><button id="clear">重置</button></div><p id="count">30 项资料</p></section><div class="grid">' + ''.join(cards) + '</div><p id="empty" hidden>没有匹配的资料。</p><section class="panel"><h2>资料处理范围</h2><p>原生PDF、网页与Office表格已提取；14张重点表逐格核对，2份数据字典对照原始字段。其余284项自动/Office结构候选仍保留待核对标记。图片表OCR与原图分别保存，不把识字当成工艺理解。</p><p>数据集完整ZIP保留。液压2205周期、17个传感器；钢板1941行、27特征与7缺陷。知识库收录字典和说明，完整数值矩阵不逐行伪装为问答。</p><div class="links">' + link('manifest.jsonl', '来源清单JSONL', True) + link('table-index.json', '表格索引JSON', True) + link('screening-decisions.json', '候选筛选与失败记录', True) + '</div></section>'
    script = '''<script>const cards=[...document.querySelectorAll('.card')],base=document.querySelector('#base'),q=document.querySelector('#q');function filter(){let n=0;for(const c of cards){const show=(!base.value||c.dataset.base===base.value)&&c.dataset.search.includes(q.value.trim().toLowerCase());c.hidden=!show;if(show)n++}document.querySelector('#count').textContent=n+' 项资料';document.querySelector('#empty').hidden=n!==0}base.addEventListener('change',filter);q.addEventListener('input',filter);document.querySelector('#clear').onclick=()=>{base.value='';q.value='';filter()};</script>'''
    (root / 'index.html').write_text(html_page('制造能源工业专业文本资料', body, script))
    (root / 'table-index.json').write_text(json.dumps(table_index, ensure_ascii=False, indent=2))
    summary['unique_embedded_image_sha256'] = len(figures)
    (root / 'collection-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--run-dir', required=True)
    args = parser.parse_args(); print(json.dumps(build(args.run_dir), ensure_ascii=False))
