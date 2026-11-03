"""Keep source pages, table cells, units and figures for reviewed technical references."""
import csv
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

VERSION='technical_reference_pages_tables_figures_v2'


def html_text(node):
    from app.preprocessors.html_notation import readable_html
    return readable_html(node)


def table_html(rows):
    return '<table>\n'+''.join('<tr>'+''.join('<td>'+escape('' if cell is None else str(cell)).replace('\n','<br>')+'</td>' for cell in row)+'</tr>\n' for row in rows)+'</table>'


def fragments(text, limit=1800):
    """Bound prose without normalizing unit symbols or dropping tail text."""
    text=text.strip()
    while text:
        stop=min(limit,len(text))
        if stop<len(text):
            cut=max(text.rfind('\n',0,stop),text.rfind('。',0,stop),text.rfind('. ',0,stop))
            if cut>limit//2:stop=cut+1
        yield text[:stop]
        text=text[stop:].lstrip()


def write_table(folder, identity, rows, attributes):
    table={'table_id':identity,'rows':rows,**attributes,'structure_review':'machine_extracted_requires_source_check'}
    (folder/(identity+'.json')).write_text(json.dumps(table,ensure_ascii=False,indent=2))
    (folder/(identity+'.html')).write_text('<meta charset="utf-8">'+table_html(rows))
    with (folder/(identity+'.csv')).open('w',encoding='utf-8-sig',newline='') as handle:csv.writer(handle).writerows(rows)
    return table


def pdf_extract(source, output, runtime):
    import fitz
    from app.loaders.ocr_router import NativeVisionOcr
    pages=[];tables=[];figures=[];chunks=[];assets={};warnings=[];ocr=NativeVisionOcr(runtime)
    with fitz.open(source) as pdf:
        if pdf.needs_pass:raise ValueError('Encrypted source; no extraction attempted')
        if len(pdf)>600:raise ValueError('Review large source before extraction')
        for number,page in enumerate(pdf,1):
            text=page.get_text('text',sort=True).strip();blocks=page.get_text('blocks',sort=True)
            drawings=len(page.get_drawings());images=page.get_images(full=True);route='native_text'
            local_tables=[]
            if hasattr(page,'find_tables'):
                try:
                    detected=page.find_tables()
                    for index,t in enumerate(detected.tables,1):
                        rows=t.extract()
                        if len(rows)<2 or t.col_count<2:continue
                        identity=f'p{number:04d}_table{index:02d}'
                        attributes={'page':number,'bbox':list(t.bbox),'row_count':t.row_count,'col_count':t.col_count,
                            'cell_bboxes':[list(c) if c else None for c in t.cells],
                            'header_names':t.header.names,'header_external':t.header.external,
                            'context':text[:1600],'source_method':'pymupdf_find_tables'}
                        item=write_table(output/'tables',identity,rows,attributes);tables.append(item);local_tables.append(item)
                        # Each source table remains one protected evidence chunk.
                        chunks.append({'content':table_html(rows)+'\n\n【原文同页上下文，含适用条件与脚注】\n'+text,'metadata':{'page':number,'table_id':identity,'chunk_kind':'technical_table',
                            'chunk_type':'table','evidence_kind':'source_table','section':f'Page {number} / {identity}',
                            'table_row_count':len(rows),'table_col_count':t.col_count,'bbox':list(t.bbox),
                            'table_context':text[:1200],'protected_structure':True,'structure_review':'machine_extracted_requires_source_check'}})
                except Exception as exc:warnings.append({'page':number,'stage':'tables','error':type(exc).__name__})
            if len(text)<60 and (images or drawings):
                image=output/'previews'/f'p{number:04d}-ocr.png'
                page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(image)
                recognized=ocr.recognize(image)
                (output/'pages'/f'p{number:04d}-ocr.json').write_text(json.dumps(recognized,ensure_ascii=False,indent=2))
                if len(recognized['text'])>len(text):text=recognized['text'];route='machine_ocr'
            for entry in images:
                xref=entry[0]
                if xref not in assets:
                    try:
                        image=pdf.extract_image(xref);raw=image['image'];digest=sha256(raw).hexdigest()
                        file=output/'figures'/(digest+'.'+image['ext'])
                        if not file.exists():file.write_bytes(raw)
                        assets[xref]={'file':str(file.relative_to(output)),'sha256':digest,'width':image['width'],'height':image['height']}
                    except Exception as exc:warnings.append({'page':number,'stage':'embedded_image','xref':xref,'error':type(exc).__name__});continue
                figures.append({'page':number,'xref':xref,**assets[xref],'rects':[list(r) for r in page.get_image_rects(xref)],'semantics':'not_inferred'})
            # Original PDF keeps diagrams, superscripts, equations and merged layout.
            geometry=[]
            for block in page.get_text('dict')['blocks']:
                if block.get('type')==0:geometry.append(block)
            payload={'page':number,'text':text,'parse_route':route,'text_geometry':geometry,'table_ids':[t['table_id'] for t in local_tables],
                     'embedded_image_count':len(images),'vector_drawing_count':drawings,'source_page_retained':True}
            (output/'pages'/f'p{number:04d}.json').write_text(json.dumps(payload,ensure_ascii=False))
            # Avoid indexing the same detected table again as flattened prose.
            prose=[]
            if route=='native_text':
                for block in blocks:
                    if len(block)>6 and block[6]!=0:continue
                    rect=fitz.Rect(block[:4]);area=max(rect.get_area(),1)
                    if any((rect & fitz.Rect(t['bbox'])).get_area()/area>.6 for t in local_tables):continue
                    prose.append(block[4])
            else:prose=[text]
            for fragment in fragments('\n'.join(prose)):
                if fragment.strip():chunks.append({'content':fragment,'metadata':{'page':number,'section':f'Page {number}',
                    'chunk_kind':'technical_prose','evidence_kind':'source_text' if route=='native_text' else 'ocr_text','parse_route':route}})
            if number==1 or (local_tables and sum(bool(p['table_ids']) for p in pages)<2):
                page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(output/'previews'/f'p{number:04d}.png')
            pages.append({k:v for k,v in payload.items() if k not in {'text_geometry','text'}}|{'text_characters':len(text)})
    return {'pages':pages,'tables':tables,'figures':figures,'chunks':chunks,'warnings':warnings}


def html_extract(source,output,item):
    from lxml import html
    from app.loaders.base_loader import decode_text
    from urllib.parse import urljoin,urlsplit
    from scripts.collection.text_download import download
    tree=html.fromstring(decode_text(source))
    candidates=tree.xpath(item['content_xpath']) if item.get('content_xpath') else (tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' entry-content ')]") or tree.xpath('//main') or tree.xpath('//article'))
    if not candidates:raise ValueError('No explicit article/main content; review page selection')
    article=candidates[0]
    for node in article.xpath('.//script|.//style|.//nav|.//form|.//iframe|.//footer|.//noscript'):
        if node.getparent() is not None:node.drop_tree()
    tables=[];figures=[];chunks=[];warnings=[]
    for index,table in enumerate(list(article.xpath('.//table')),1):
        if table.xpath('.//table'):warnings.append({'stage':'tables','error':'nested table preserved in source'});continue
        rows=[[html_text(c) for c in row.xpath('./th|./td')] for row in table.xpath('.//tr')]
        if not rows:continue
        identity=f'html_table{index:02d}'
        item_table=write_table(output/'tables',identity,rows,{'page':None,'row_count':len(rows),'col_count':max(map(len,rows)),
            'original_html':html.tostring(table,encoding='unicode'),'source_method':'html_table_dom',
            'cell_spans':[[{'rowspan':c.get('rowspan','1'),'colspan':c.get('colspan','1')} for c in row.xpath('./th|./td')] for row in table.xpath('.//tr')]})
        tables.append(item_table)
        nearby='\n'.join(html_text(n) for n in list(table.itersiblings(preceding=True))[:2]+list(table.itersiblings())[:4])
        chunks.append({'content':html.tostring(table,encoding='unicode')+'\n\n【原文邻近说明与脚注】\n'+nearby,'metadata':{'table_id':identity,'chunk_kind':'technical_table','chunk_type':'table',
            'evidence_kind':'source_table','section':identity,'table_row_count':len(rows),'table_col_count':max(map(len,rows)),
            'protected_structure':True,'structure_review':'source_html_structure_retained'}})
        table.getparent().replace(table,html.fromstring('<p>['+identity+']</p>'))
    for index,img in enumerate(article.xpath('.//img'),1):
        raw_url=img.get('data-src') or img.get('src') or '';url=urljoin(item['resolved_url'],raw_url)
        record={'figure_id':f'html_figure{index:02d}','url':url,'caption':img.get('alt',''),'semantics':'not_inferred'}
        if raw_url and urlsplit(url).scheme in {'https','http'}:
            try:
                receipt=download(output,{'id':record['figure_id'],'url':url,'allowed_hosts':item['allowed_hosts']})
                record.update(file=receipt['file'],sha256=receipt['sha256'],downloaded=True)
            except Exception as exc:record.update(downloaded=False,error=type(exc).__name__)
        figures.append(record)
        if record['caption']:chunks.append({'content':record['caption'],'metadata':{'figure_id':record['figure_id'],'chunk_kind':'figure_caption','evidence_kind':'source_caption','section':'Figure caption'}})
    for node in article.xpath('.//h1|.//h2|.//h3|.//h4|.//p|.//li|.//pre'):
        if node.tag=='li' and node.xpath('.//p'):continue
        if node.xpath('ancestor::table|ancestor::pre'):continue
        text=''.join(node.itertext()).strip() if node.tag=='pre' else html_text(node)
        for fragment in fragments(text):chunks.append({'content':fragment,'metadata':{'chunk_kind':'technical_prose','evidence_kind':'source_text','section':node.tag,'parse_route':'native_html'}})
    text='\n'.join(article.itertext()).strip()
    (output/'pages'/'article.txt').write_text(text)
    return {'pages':[{'page':None,'text_characters':len(text),'parse_route':'native_html'}],'tables':tables,'figures':figures,'chunks':chunks,'warnings':warnings}


def docx_extract(source,output,item):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    from zipfile import ZipFile
    converted=Path(item['converted_docx'])
    if sha256(converted.read_bytes()).hexdigest()!=item['converted_sha256']:raise ValueError('Converted DOCX changed')
    document=Document(converted);chunks=[];tables=[];figures=[];prose=[]
    for index,element in enumerate(document.element.body,1):
        if element.tag==qn('w:p'):
            text=Paragraph(element,document).text.strip();prose.append(text)
            for fragment in fragments(text):chunks.append({'content':fragment,'metadata':{'section':f'Paragraph {index}','chunk_kind':'technical_prose','evidence_kind':'source_text','parse_route':'libreoffice_doc_to_docx','page':None}})
        elif element.tag==qn('w:tbl'):
            table=Table(element,document);rows=[[cell.text for cell in row.cells] for row in table.rows];identity=f'docx_table{len(tables)+1:02d}'
            attributes={'page':None,'body_position':index,'row_count':len(rows),'col_count':max(map(len,rows)),
                'original_ooxml':element.xml,'source_method':'python_docx_after_libreoffice_conversion','context':'\n'.join(prose[-4:])}
            tables.append(write_table(output/'tables',identity,rows,attributes))
            chunks.append({'content':table_html(rows)+'\n\n【原文前文】\n'+attributes['context'],'metadata':{'table_id':identity,'section':identity,
                'chunk_kind':'technical_table','chunk_type':'table','evidence_kind':'source_table','protected_structure':True,
                'table_row_count':len(rows),'table_col_count':attributes['col_count'],'structure_review':'converted_office_table_requires_source_check',
                'page':None,'body_position':index}})
    with ZipFile(converted) as archive:
        for member in archive.infolist():
            if not member.filename.startswith('word/media/') or member.is_dir():continue
            if member.file_size>32*1024*1024:raise ValueError('Office image too large')
            raw=archive.read(member);digest=sha256(raw).hexdigest();file=output/'figures'/(digest+Path(member.filename).suffix)
            if not file.exists():file.write_bytes(raw)
            figures.append({'file':str(file.relative_to(output)),'sha256':digest,'office_part':member.filename,'page':None,'semantics':'not_inferred'})
    return {'pages':[{'page':None,'text_characters':sum(len(c['content']) for c in chunks),'parse_route':'libreoffice_doc_to_docx'}],
        'tables':tables,'figures':figures,'chunks':chunks,'warnings':[],
        'conversion':{'tool':'LibreOfficeDev 26.8.0.0.alpha0','source_retained':True,'converted_sha256':item['converted_sha256'],'pagination':'not_claimed_for_docx'}}


def extract(root,item,runtime):
    source=Path(root)/item['file'];output=source.parent/VERSION
    receipt=output/'extraction.json'
    if sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Downloaded source changed')
    if receipt.exists():return json.loads(receipt.read_text())
    for folder in ['pages','tables','figures','previews']:(output/folder).mkdir(parents=True,exist_ok=True)
    if source.suffix=='.pdf':result=pdf_extract(source,output,runtime)
    elif source.suffix=='.html':result=html_extract(source,output,item)
    elif source.suffix=='.doc' and item.get('converted_docx'):result=docx_extract(source,output,item)
    else:raise ValueError('Format requires a reviewed extraction path')
    result.update(source_sha256=item['sha256'],extractor=VERSION,id=item['id'],knowledge_base_id=item['knowledge_base_id'],
                  extractor_code_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (output/'readable.txt').write_text('\n\n'.join(c['content'] for c in result['chunks']))
    return result
