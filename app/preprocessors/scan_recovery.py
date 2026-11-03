"""Conservative scan candidates. Never infer missing values or approve source facts."""
from pathlib import Path
from hashlib import sha256
import math
from app.preprocessors.structured_tables import record, scan_table


def borderless_candidate(image_path, regions, identity='borderless_candidate'):
    import fitz
    pix=fitz.Pixmap(str(image_path));w,h=pix.width,pix.height
    valid=[]
    for i,r in enumerate(regions):
        x,y,rw,rh=r['bbox']
        if rw<=0 or rh<=0 or not r.get('text','').strip():continue
        valid.append({**r,'source_region_index':i,'center_y':y+rh/2})
    rows=[]
    for r in sorted(valid,key=lambda x:x['center_y']):
        if rows and abs(r['center_y']-sum(x['center_y'] for x in rows[-1])/len(rows[-1]))<min(r['bbox'][3],min(x['bbox'][3] for x in rows[-1]))*.65:rows[-1].append(r)
        else:rows.append([r])
    rows=[sorted(row,key=lambda r:r['bbox'][0]) for row in rows]
    # A title or one OCR line spanning columns cannot establish a grid.
    if len(rows)<3 or len(rows)>100:return None
    n=len(rows[0])
    if not 2<=n<=20 or any(len(row)!=n for row in rows):return None
    anchors=[sum(row[c]['bbox'][0] for row in rows)/len(rows) for c in range(n)]
    if any(abs(row[c]['bbox'][0]-anchors[c])>.015 for row in rows for c in range(n)):return None
    if any(row[c]['bbox'][0]+row[c]['bbox'][2]>=row[c+1]['bbox'][0]-.005 for row in rows for c in range(n-1)):return None
    cells=[]
    for ri,row in enumerate(rows):
        for ci,r in enumerate(row):
            x,y,rw,rh=r['bbox'];box=[x*w,y*h,(x+rw)*w,(y+rh)*h]
            cells.append(dict(row=ri,col=ci,rowspan=1,colspan=1,text=r['text'],bbox=box,confidence=r.get('confidence'),ocr_regions=[r],locator={'bbox':box,'source_region_index':r['source_region_index'],'coordinate_system':'image_pixels_top_left','bbox_meaning':'ocr_text_extent_not_cell_border'}))
    return record(identity,cells,'borderless_aligned_ocr_candidate',image_size=[w,h],coordinate_system='image_pixels_top_left',image_sha256=sha256(Path(image_path).read_bytes()).hexdigest(),flags=['borderless_structure_inferred','ocr_requires_source_verification'],indexable=False,merged_cells='not_inferred')


def deskew_candidate(image_path, regions, output_dir):
    """Try a bounded ±6 degree rotation, then require a complete ruled grid."""
    import fitz
    import numpy as np
    pix=fitz.Pixmap(str(image_path));w,h=pix.width,pix.height
    if w*h>12_000_000:return None
    if pix.colorspace.n!=1:pix=fitz.Pixmap(fitz.csGRAY,pix)
    a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(h,w,pix.n)[:,:,0]
    yy,xx=np.nonzero(a<170)
    if len(xx)<100:return None
    step=max(1,len(xx)//80000);xx=xx[::step];yy=yy[::step]
    def score(angle):
        rad=math.radians(angle);y=xx*math.sin(rad)+yy*math.cos(rad)
        hist=np.bincount(np.round(y-y.min()).astype(int));return float(np.sort(hist)[-8:].sum())
    scores=[(score(float(a)),float(a)) for a in np.arange(-6,6.01,.25)]
    best,angle=max(scores)
    if abs(angle)<.5 or best<score(0)*1.5:return None
    matrix=fitz.Matrix(1,1).prerotate(angle);rect=fitz.Rect(0,0,w,h)*matrix
    with fitz.open() as doc:
        p=doc.new_page(width=w,height=h);p.insert_image(p.rect,filename=str(image_path));rendered=p.get_pixmap(matrix=matrix,alpha=False)
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    data=rendered.tobytes('png');path=out/(sha256(data).hexdigest()+'.png')
    if not path.exists():path.write_bytes(data)
    rw,rh=rendered.width,rendered.height
    offset=fitz.Point(rendered.x,rendered.y)
    transformed=[]
    for r in regions:
        x,y,ww,hh=r['bbox'];b=fitz.Rect(x*w,y*h,(x+ww)*w,(y+hh)*h)*matrix
        transformed.append({**r,'source_bbox':r['bbox'],'bbox':[(b.x0-offset.x)/rw,(b.y0-offset.y)/rh,b.width/rw,b.height/rh]})
    table=scan_table(path,transformed,'deskewed_scan_candidate')
    if not table:return None
    inverse=~matrix
    for c in table['cells']:
        x,y,x1,y1=c['bbox'];quad=[]
        for px,py in [(x,y),(x1,y),(x1,y1),(x,y1)]:
            point=(fitz.Point(px,py)+offset)*inverse;quad.append([point.x,point.y])
        c['locator'].update(source_quad=quad,source_coordinate_system='original_image_pixels_top_left')
    table.update(source_method='deskewed_ruled_scan_candidate',source_image_sha256=sha256(Path(image_path).read_bytes()).hexdigest(),derived_image_path=str(path),deskew_degrees=angle,indexable=False)
    table['flags'].append('deskew_geometry_requires_verification')
    return table
