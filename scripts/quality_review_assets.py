"""Local, hash-checked source previews for explicit human case review."""
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re


def build_assets(cases, chunks, output, allowed_root, originals=None):
    from app.preprocessors.structured_tables import html_table, to_html
    import fitz
    out=Path(output);out.mkdir(parents=True,exist_ok=False);root=Path(allowed_root).resolve()
    cached={};cards={};receipt=[]
    for case in cases:
        parts=[];sources=[]
        for identity in case.expected_chunk_ids:
            ch=chunks[identity];m=ch['metadata'];source=Path(m.get('source_project_path') or '')
            if (not source.is_file() or not source.resolve().is_relative_to(root)) and (originals or {}).get(ch['doc_id']):
                source=Path(originals[ch['doc_id']])
            if not source.is_file() or not source.resolve().is_relative_to(root):
                sources.append({'chunk_id':identity,'source_preview':'unavailable','reason':'No local source within project boundary'});continue
            expected=m.get('source_sha256') or m.get('image_sha256');actual=sha256(source.read_bytes()).hexdigest()
            if expected and actual!=expected:raise ValueError('Source preview fingerprint mismatch: '+identity)
            asset=source;page=m.get('page');artifact=m.get('source_artifact')
            if artifact:
                # Resolve only relative to the frozen professional collection manifest root.
                collection=next((p for p in source.parents if (p/'manifest.jsonl').is_file()),None)
                if collection:
                    candidate=(collection/artifact).resolve()
                    if candidate.is_relative_to(collection) and candidate.is_file():
                        if m.get('artifact_sha256') and sha256(candidate.read_bytes()).hexdigest()!=m['artifact_sha256']:raise ValueError('Source image changed')
                        asset=candidate
            key=(str(asset),page if asset.suffix.lower()=='.pdf' else None)
            preview=None
            if key not in cached:
                name=sha256(str(key).encode()).hexdigest()[:24]+'.png';dest=out/name
                if asset.suffix.lower()=='.pdf' and page:
                    with fitz.open(asset) as pdf:
                        if not 1<=int(page)<=len(pdf):raise ValueError('Frozen page is outside source')
                        pdf[int(page)-1].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(dest)
                    preview=name
                elif asset.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}:
                    try:
                        pix=fitz.Pixmap(str(asset))
                        if pix.colorspace and pix.colorspace.n>3:pix=fitz.Pixmap(fitz.csRGB,pix)
                        pix.save(dest)
                    except Exception:
                        import subprocess
                        converted=subprocess.run(['/usr/bin/sips','-s','format','png',str(asset),'--out',str(dest)],capture_output=True,timeout=30)
                        if converted.returncode:raise ValueError('Source preview decoder unavailable: '+asset.name)
                    preview=name
                cached[key]=preview
            preview=cached[key]
            source_info={'chunk_id':identity,'revision_id':m.get('revision_id'),'source_sha256':actual,'content_sha256':sha256(ch['content'].encode()).hexdigest(),'page':page,'source_version':m.get('source_version'),'source_artifact':str(asset),'preview':preview,'review_status':'source_checked_pending_human'}
            sources.append(source_info)
            parts.append('<p>来源版本：'+escape(str(m.get('source_version') or '原件快照，版本待核对'))+'；冻结修订：'+escape(str(m.get('revision_id')))+'；SHA256：'+actual+'</p>')
            if preview:parts.append('<a target="_blank" rel="noopener" href="'+escape(out.name+'/'+preview,quote=True)+'"><img loading="lazy" alt="冻结来源原页/图片，点击放大" src="'+escape(out.name+'/'+preview,quote=True)+'"></a>')
            else:parts.append('<p>原件无固定页码图像，请通过修订来源链接核对；不编造页码。</p>')
            for markup in re.findall(r'<table\b[^>]*>.*?</table>',ch['content'],re.S|re.I):
                table=html_table(markup,m.get('table_id') or identity)
                parts.append('<div class="table-scroll">'+to_html(table)+'</div>')
                parts.append('<details><summary>单元格位置（逻辑行列）</summary><pre>'+escape(json.dumps(table['cells'],ensure_ascii=False,indent=2))+'</pre></details>')
        if case.expected_empty:parts.append('<p>空范围审核：确认知识库与文档过滤的交集、发布状态和拒答预期；无来源不能填写事实答案。</p>')
        cards[case.query_id]=''.join(parts)
        receipt.append({'query_id':case.query_id,'sources':sources,'review_status':'source_checked_pending_human','human_approved':False})
    (out/'sources.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    return cards
