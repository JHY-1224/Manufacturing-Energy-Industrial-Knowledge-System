"""Source-bound table geometry. Machine extraction never confers human approval.

Coordinates are top-left PDF points or image pixels; DOM/OOXML cells use logical
row/column locators. Ambiguous formulas and broken grids remain explicit evidence.
"""
from hashlib import sha256
from html import escape
from pathlib import Path
import re

VERSION = 'source_geometry_v2'
PENDING = 'source_checked_pending_human'


def record(identity, cells, method, **extra):
    rows = max((c['row'] + c['rowspan'] for c in cells), default=0)
    cols = max((c['col'] + c['colspan'] for c in cells), default=0)
    if rows * cols > 50000:
        raise ValueError('Table exceeds cell budget')
    occupied = {}; flags = list(extra.pop('flags', []))
    for cell in cells:
        cell['cell_id'] = f"{identity}:r{cell['row']+1}c{cell['col']+1}"
        for r in range(cell['row'], cell['row'] + cell['rowspan']):
            for c in range(cell['col'], cell['col'] + cell['colspan']):
                if (r, c) in occupied: flags.append('overlapping_cells')
                occupied[r, c] = cell['cell_id']
    if len(occupied) != rows * cols: flags.append('incomplete_grid')
    return dict(table_id=identity, cells=cells, row_count=rows, col_count=cols,
                source_method=method, parser_version=VERSION, review_status=PENDING,
                human_approved=False, training_allowed=False, flags=sorted(set(flags)), **extra)


def to_html(table):
    out = ['<table data-source-table="'+escape(table['table_id'], quote=True)+'">']
    for row in range(table['row_count']):
        out.append('<tr>')
        for c in sorted((c for c in table['cells'] if c['row'] == row), key=lambda c: c['col']):
            out.append(f'<td rowspan="{c["rowspan"]}" colspan="{c["colspan"]}" data-cell="{escape(c["cell_id"], quote=True)}">'
                       + c.get('html', escape(c['text']).replace('\n', '<br>')) + '</td>')
        out.append('</tr>')
    return ''.join(out) + '</table>'


def _dom_content(node):
    """Allow only textual formatting; never render active source HTML in review UI."""
    text = node.text or ''; markup = escape(text); formulas = []
    for child in node:
        tag = child.tag.lower() if isinstance(child.tag, str) else ''
        if tag in {'script', 'style', 'iframe', 'object'}:
            pass
        elif tag == 'br': text += '\n'; markup += '<br>'
        elif tag == 'img': text += '[原图公式/图像待核对]'; markup += '[原图公式/图像待核对]'
        elif tag.endswith('math'):
            from lxml import etree
            formulas.append(etree.tostring(child, encoding='unicode'))
            from app.preprocessors.math_notation import linear_math
            try:notation=linear_math(child)
            except ValueError:notation='[MathML公式保留，未转写]'
            text += notation;markup += escape(notation)
        else:
            t, h, f = _dom_content(child); formulas.extend(f)
            if tag in {'sup', 'sub'}:
                text += ('^' if tag == 'sup' else '_') + '(' + t + ')'
                markup += '<'+tag+'>'+h+'</'+tag+'>'
            else: text += t; markup += h
        text += child.tail or ''; markup += escape(child.tail or '')
    return text.strip(), markup, formulas


def html_table(markup, identity):
    from lxml import html
    tree = html.fromstring(markup)
    if tree.tag != 'table':
        tables = tree.xpath('.//table')
        if len(tables) != 1: raise ValueError('Expected one independent table')
        tree = tables[0]
    if tree.xpath('.//table'): raise ValueError('Nested tables require separate review')
    cells = []; occupied = set(); flags = []
    rows = tree.xpath('./tr|./thead/tr|./tbody/tr|./tfoot/tr')
    for ri, row in enumerate(rows):
        col = 0
        for ci, node in enumerate(row.xpath('./th|./td'), 1):
            while (ri, col) in occupied: col += 1
            rs, cs = int(node.get('rowspan', '1')), int(node.get('colspan', '1'))
            if not 1 <= rs <= 100 or not 1 <= cs <= 100: raise ValueError('Invalid cell span')
            if ri + rs > len(rows): flags.append('rowspan_beyond_source_rows')
            text, rich, formulas = _dom_content(node)
            if node.xpath('.//img'): flags.append('image_or_formula_requires_source')
            cells.append(dict(row=ri, col=col, rowspan=rs, colspan=cs, text=text, html=rich,
                              formula_sources=formulas, locator={'dom_row':ri+1, 'dom_cell':ci, 'column':col+1}))
            occupied.update((r, c) for r in range(ri, ri+rs) for c in range(col, col+cs));col += cs
    return record(identity, cells, 'source_html_dom', flags=flags, coordinate_system='logical_grid')


def _office_paragraph(p, ns):
    from lxml import etree
    w='{'+ns['w']+'}'; flags=[];formulas=[]
    plain='';rich=''
    for run in p:
        if run.tag in {'{'+ns['m']+'}oMath','{'+ns['m']+'}oMathPara'}:
            formulas.append(etree.tostring(run,encoding='unicode'))
            from app.preprocessors.math_notation import linear_math
            try:notation=linear_math(run)
            except ValueError:notation='[OMML公式保留，未转写]';flags.append('unsupported_math_structure')
            plain+=notation;rich+=escape(notation);continue
        if run.tag!=w+'r':
            # Preserve hyperlink/field text; flag unhandled run-level formatting.
            t=''.join(run.xpath('.//w:t/text()',namespaces=ns));plain+=t;rich+=escape(t)
            if t:flags.append('nested_office_run_format_requires_review')
            continue
        t=''.join('\n' if x.tag==w+'br' else '\t' if x.tag==w+'tab' else (x.text or '') if x.tag==w+'t' else '' for x in run)
        align=run.find('w:rPr/w:vertAlign',ns);kind=align.get(w+'val') if align is not None else None
        if kind in {'superscript','subscript'}:
            tag='sup' if kind=='superscript' else 'sub';plain+=('^' if tag=='sup' else '_')+'('+t+')';rich+='<'+tag+'>'+escape(t)+'</'+tag+'>'
        else:plain+=t;rich+=escape(t).replace('\n','<br>')
    return plain, rich, formulas, flags


def office_paragraph(xml):
    from lxml import etree
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'm':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    p=etree.fromstring(xml.encode(),etree.XMLParser(resolve_entities=False,no_network=True))
    return _office_paragraph(p, ns)


def office_table(xml, identity):
    from lxml import etree
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'm':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    w='{'+ns['w']+'}'; tree=etree.fromstring(xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    cells=[]; active={};flags=[]
    for ri,tr in enumerate(tree.findall('w:tr',ns)):
        before=tr.find('w:trPr/w:gridBefore',ns); col=int(before.get(w+'val','0')) if before is not None else 0
        next_active={}
        for ci,tc in enumerate(tr.findall('w:tc',ns)):
            span=tc.find('w:tcPr/w:gridSpan',ns);width=int(span.get(w+'val','1')) if span is not None else 1
            if not 1<=width<=100:raise ValueError('Invalid Office span')
            merge=tc.find('w:tcPr/w:vMerge',ns)
            continuation=merge is not None and merge.get(w+'val','continue')=='continue'
            text_parts=[];html_parts=[];formulas=[]
            for p in tc.findall('w:p',ns):
                plain,rich,fs,ps=_office_paragraph(p,ns)
                formulas.extend(fs);flags.extend(ps)
                text_parts.append(plain);html_parts.append(rich)
            text='\n'.join(text_parts).strip();rich='<br>'.join(html_parts)
            if continuation and col in active and active[col]['colspan']==width and not text:
                anchor=active[col];anchor['rowspan']+=1;anchor['locator']['continuation_rows'].append(ri+1);next_active[col]=anchor
            else:
                if continuation:flags.append('orphan_or_conflicting_vertical_merge')
                cell=dict(row=ri,col=col,rowspan=1,colspan=width,text=text,html=rich,formula_sources=formulas,
                          locator={'ooxml_row':ri+1,'ooxml_cell':ci+1,'column':col+1,'continuation_rows':[]})
                cells.append(cell)
                if merge is not None:next_active[col]=cell
            col+=width
        active=next_active
    return record(identity,cells,'source_ooxml',flags=flags,coordinate_system='logical_grid',pagination='not_inferred')


def _cluster(values, tolerance=1.5):
    groups=[]
    for value in sorted(values):
        if groups and abs(value-sum(groups[-1])/len(groups[-1]))<=tolerance:groups[-1].append(value)
        else:groups.append([value])
    return [sum(g)/len(g) for g in groups]


def pdf_cell(page, bbox):
    lines=[];rich_lines=[];runs=[];flags=[]
    for block in page.get_text('rawdict',clip=bbox,flags=0)['blocks']:
        for line in block.get('lines',[]):
            spans=line.get('spans',[]);size=max((s['size'] for s in spans),default=0)
            normal=[s for s in spans if s['size']>=size*.95]
            baseline=sum(s['origin'][1] for s in normal)/max(1,len(normal));plain='';rich=''
            for s in spans:
                # MuPDF's default clipping accepts intersecting glyphs, so a thin
                # padding row can duplicate text from its neighbour. Assign each
                # character by its centre, preserving original glyph positions.
                x0,y0,x1,y1=bbox
                chars=[c for c in s.get('chars',[]) if x0 <= (c['bbox'][0]+c['bbox'][2])/2 < x1 and y0 <= (c['bbox'][1]+c['bbox'][3])/2 < y1]
                text=''.join(c['c'] for c in chars);kind=None
                if not text:continue
                if s['flags'] & 1:kind='sup'
                elif size and s['size']<size*.9 and s['origin'][1]>baseline+size*.12:
                    kind='sub';flags.append('subscript_geometry_inferred')
                if any(ord(c)<32 and c not in '\n\t\r' or c=='\ufffd' for c in text):flags.append('unmapped_glyph')
                runs.append({'text':text,'bbox':list(s['bbox']),'origin':list(s['origin']),'font':s['font'],'size':s['size'],'flags':s['flags'],'script':kind,'characters':chars,'assignment':'glyph_center_inside_source_cell'})
                plain+=(('^' if kind=='sup' else '_')+'('+text+')') if kind else text
                rich+=('<'+kind+'>'+escape(text)+'</'+kind+'>') if kind else escape(text)
            lines.append(plain);rich_lines.append(rich)
    return '\n'.join(lines),'<br>'.join(rich_lines),runs,flags


def pdf_table(page, detected, identity):
    boxes=sorted({tuple(b) for row in detected.rows for b in row.cells if b})
    xs=_cluster([v for b in boxes for v in (b[0],b[2])]);ys=_cluster([v for b in boxes for v in (b[1],b[3])])
    cells=[];flags=[]
    for box in boxes:
        c0,c1=[min(range(len(xs)),key=lambda i:abs(xs[i]-v)) for v in (box[0],box[2])]
        r0,r1=[min(range(len(ys)),key=lambda i:abs(ys[i]-v)) for v in (box[1],box[3])]
        if r1<=r0 or c1<=c0:flags.append('degenerate_cell');continue
        text,rich,runs,cf=pdf_cell(page,box);flags.extend(cf)
        cells.append(dict(row=r0,col=c0,rowspan=r1-r0,colspan=c1-c0,text=text,html=rich,bbox=list(box),runs=runs,
                          locator={'page':page.number+1,'bbox':list(box),'coordinate_system':'pdf_points_top_left'}))
    if not any(c['text'].strip() for c in cells):flags.append('no_cell_text')
    if sum(bool(c['text'].strip()) for c in cells)<len(cells)*.35:flags.append('sparse_grid_possible_diagram')
    return record(identity,cells,'pymupdf_cell_geometry',page=page.number+1,bbox=list(detected.bbox),
                  page_size=[page.rect.width,page.rect.height],coordinate_system='pdf_points_top_left',
                  header_external=detected.header.external,header_names=detected.header.names,flags=flags)


def page_tables(page):
    if not hasattr(page,'find_tables'):return []
    return [pdf_table(page,t,f'p{page.number+1:04d}_table{i:02d}') for i,t in enumerate(page.find_tables().tables,1) if t.row_count>=2 and t.col_count>=2]


def continuation_links(tables):
    """Link, never silently concatenate. Repeated header alone is insufficient."""
    links=[]
    def header(t):return [(c['row'],c['col'],c['rowspan'],c['colspan'],' '.join(c['text'].split())) for c in sorted(t['cells'],key=lambda c:(c['row'],c['col'])) if c['row']<t.get('header_row_count',1)]
    for a in tables:
        for b in tables:
            if not a.get('page') or b.get('page')!=a['page']+1 or a['col_count']!=b['col_count']:continue
            if not header(a) or header(a)!=header(b):continue
            if not any(x[-1] for x in header(a)) or a.get('header_row_count',1)!=b.get('header_row_count',1):continue
            aw,ah=a.get('page_size',[0,0]);bw,bh=b.get('page_size',[0,0])
            if not aw or not bw:continue
            ax=sorted({round(c['bbox'][0]/aw,3) for c in a['cells']});bx=sorted({round(c['bbox'][0]/bw,3) for c in b['cells']})
            if len(ax)!=len(bx) or any(abs(x-y)>.015 for x,y in zip(ax,bx)):continue
            edge=a['bbox'][3]>ah*.72 and b['bbox'][1]<bh*.3
            if not edge:continue
            explicit=bool(re.search(r'\bcontinued\b|续表|接上页',b.get('context_before',''),re.I))
            links.append({'from_table_id':a['table_id'],'to_table_id':b['table_id'],'source_pages':[a['page'],b['page']],
                          'kind':'explicit_continuation' if explicit else 'possible_continuation',
                          'reason':['matching_header','aligned_columns','adjacent_page_edges']+(['source_continuation_marker'] if explicit else []),
                          'review_status':PENDING,'auto_joined':False})
    return links


def explicit_continuation_tables(tables):
    """Build conservative multi-page views without carrying blank values forward.

    Only a linear chain with an explicit repeated table number and continuation
    marker can be joined. Every cell keeps its source page, bbox and original ID.
    Multi-row headers require an explicit header_row_count; irregular or uncertain grids stay separate.
    """
    import copy
    by={t['table_id']:t for t in tables};links=continuation_links(tables);valid=[]
    for link in links:
        a,b=(by[link[k]] for k in ('from_table_id','to_table_id'))
        def caption(t):
            matches=re.findall(r'(?:\bTable|表)\s*(\d+(?:[.-]\d+)*)',t.get('context_before',''),re.I)
            return matches[-1] if matches else None
        if link['kind']!='explicit_continuation' or not caption(a) or caption(a)!=caption(b):continue
        if any(t['flags'] or not 1<=t.get('header_row_count',1)<=3 or any(c['row']<t.get('header_row_count',1) and c['row']+c['rowspan']>t.get('header_row_count',1) for c in t['cells']) for t in (a,b)):continue
        valid.append(link)
    out=[]
    for start in tables:
        if any(l['to_table_id']==start['table_id'] for l in valid):continue
        chain=[start];seen={start['table_id']}
        while len(chain)<10:
            nxt=[l for l in valid if l['from_table_id']==chain[-1]['table_id']]
            if len(nxt)!=1:break
            target=nxt[0]['to_table_id']
            if target in seen or sum(l['to_table_id']==target for l in valid)!=1:break
            chain.append(by[target]);seen.add(target)
        if len(chain)<2:continue
        cells=[];row_offset=0
        for i,table in enumerate(chain):
            for original in table['cells']:
                header_rows=table.get('header_row_count',1)
                if i and original['row']<header_rows:continue
                cell=copy.deepcopy(original);cell['source_cell_id']=cell['cell_id'];cell['source_table_id']=table['table_id']
                cell['row']+=row_offset-(header_rows if i else 0);cells.append(cell)
            row_offset+=table['row_count']-(table.get('header_row_count',1) if i else 0)
        out.append(record('continued_'+start['table_id'],cells,'explicit_numbered_cross_page_view',
                          source_pages=[t['page'] for t in chain],source_table_ids=[t['table_id'] for t in chain],
                          coordinate_system='per_cell_source_page_pdf_points',repeated_headers_retained_in_source=True,
                          source_contexts=[t.get('context_before','') for t in chain],blank_cells_filled=False))
    return out


def scan_table(image_path, regions, identity='scan_table01'):
    """Recover a single axis-aligned ruled grid; keep unsplittable OCR lines apart.

No gridless inference, skew correction, value interpolation or guessing merges.
Partial borders form merges only when the connected area is rectangular.
"""
    import fitz
    import numpy as np
    pix=fitz.Pixmap(str(image_path));width,height=pix.width,pix.height
    if width*height>40_000_000:raise ValueError('Scan exceeds pixel budget')
    if pix.colorspace and pix.colorspace.n!=1:pix=fitz.Pixmap(fitz.csGRAY,pix)
    pixels=np.frombuffer(pix.samples,dtype=np.uint8).reshape(height,width,pix.n)
    gray=pixels[:,:,0].astype(np.float32)
    if pix.alpha:
        # MuPDF samples are premultiplied; composite transparent backgrounds on white.
        gray=gray+255-pixels[:,:,-1].astype(np.float32)
    a=gray<200
    # Continuous line runs distinguish faint antialiased borders from aligned text.
    # Counting dark pixels alone merges unrelated columns and misses grey borders.
    def line_positions(rows, minimum):
        found=[]
        for i,row in enumerate(rows):
            changes=np.diff(np.r_[False,row,False].astype(np.int8))
            starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
            if len(starts) and (ends-starts).max()>=minimum:found.append(i)
        groups=[]
        for i in found:
            if groups and i==groups[-1][-1]+1:groups[-1].append(i)
            else:groups.append([i])
        return [sum(g)/len(g) for g in groups if len(g)<=8]
    ys=line_positions(a,max(24,min(width*.25,100)))
    if len(ys)<3:return None
    band=a[max(0,round(ys[0])):min(height,round(ys[-1])+1)]
    xs=line_positions(band.T,max(24,min(len(band)*.25,80)))
    if not 3<=len(xs)<=70 or not 3<=len(ys)<=100:return None
    xs=[round(x) for x in xs];ys=[round(y) for y in ys]
    if min(np.diff(xs))<12 or min(np.diff(ys))<12:return None
    nr,nc=len(ys)-1,len(xs)-1
    if nr*nc>2000:return None
    def vertical(x,y0,y1):return a[y0+3:y1-2,max(0,x-2):x+3].any(axis=1).mean()>.8
    def horizontal(y,x0,x1):return a[max(0,y-2):y+3,x0+3:x1-2].any(axis=0).mean()>.8
    if not all(vertical(x,ys[0],ys[-1]) for x in (xs[0],xs[-1])) or not all(horizontal(y,xs[0],xs[-1]) for y in (ys[0],ys[-1])):return None
    parent=list(range(nr*nc))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):parent[find(a)]=find(b)
    for r in range(nr):
        for c in range(nc):
            if c<nc-1 and not vertical(xs[c+1],ys[r],ys[r+1]):union(r*nc+c,r*nc+c+1)
            if r<nr-1 and not horizontal(ys[r+1],xs[c],xs[c+1]):union(r*nc+c,(r+1)*nc+c)
    groups={}
    for r in range(nr):
        for c in range(nc):groups.setdefault(find(r*nc+c),[]).append((r,c))
    cells=[]
    for positions in groups.values():
        r0=min(r for r,c in positions);r1=max(r for r,c in positions)+1;c0=min(c for r,c in positions);c1=max(c for r,c in positions)+1
        if len(positions)!=(r1-r0)*(c1-c0):return None
        box=[xs[c0],ys[r0],xs[c1],ys[r1]]
        cells.append(dict(row=r0,col=c0,rowspan=r1-r0,colspan=c1-c0,text='',bbox=box,ocr_regions=[],locator={'bbox':box,'coordinate_system':'image_pixels_top_left'}))
    unassigned=[]
    for i,region in enumerate(regions):
        x,y,w,h=region['bbox'];box=[x*width,y*height,(x+w)*width,(y+h)*height];area=max(1,w*width*h*height)
        def overlap(c):
            b=c['bbox'];return max(0,min(b[2],box[2])-max(b[0],box[0]))*max(0,min(b[3],box[3])-max(b[1],box[1]))/area
        matches=[c for c in cells if overlap(c)>.8]
        if len(matches)==1:matches[0]['ocr_regions'].append({**region,'source_region_index':i})
        else:unassigned.append(i)
    for c in cells:
        c['ocr_regions'].sort(key=lambda v:(v['bbox'][1],v['bbox'][0]));c['text']='\n'.join(v['text'] for v in c['ocr_regions'])
        c['confidence']=min((v['confidence'] for v in c['ocr_regions']),default=None)
    flags=['ocr_requires_source_verification']
    if unassigned:flags.append('unassigned_or_cross_cell_ocr')
    return record(identity,cells,'ruled_scan_geometry_and_ocr',bbox=[xs[0],ys[0],xs[-1],ys[-1]],
                  image_size=[width,height],coordinate_system='image_pixels_top_left',unassigned_region_indices=unassigned,
                  image_sha256=sha256(Path(image_path).read_bytes()).hexdigest(),flags=flags)
