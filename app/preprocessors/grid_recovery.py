"""Apply explicitly reviewed PDF grid boundaries without inventing blank values."""
from app.preprocessors.structured_tables import record,pdf_cell

def bounded_grid(page,identity,xs,ys,headers=None):
 if len(xs)<3 or len(ys)<3 or any(a>=b for a,b in zip(xs,xs[1:])) or any(a>=b for a,b in zip(ys,ys[1:])):raise ValueError('Strictly increasing grid required')
 if xs[0]<0 or ys[0]<0 or xs[-1]>page.rect.width or ys[-1]>page.rect.height:raise ValueError('Grid outside source page')
 if headers and len(headers)!=len(xs)-1:raise ValueError('Header count mismatch')
 cells=[];all_flags=[];offset=bool(headers)
 if headers:
  for col,h in enumerate(headers):cells.append(dict(row=0,col=col,rowspan=1,colspan=1,text=h,locator={'kind':'ai_normalized_header','page':page.number+1,'source_header_region':[xs[0],0,xs[-1],ys[0]],'not_source_cell_border':True}))
 for row,(top,bottom) in enumerate(zip(ys,ys[1:])):
  for col,(left,right) in enumerate(zip(xs,xs[1:])):
   bbox=[left,top,right,bottom];text,rich,scripts,flags=pdf_cell(page,bbox)
   all_flags.extend(flags)
   cells.append(dict(row=row+offset,col=col,rowspan=1,colspan=1,text=text,html=rich,bbox=bbox,script_runs=scripts,locator={'page':page.number+1,'bbox':bbox,'coordinate_system':'pdf_points_top_left','assignment':'glyph_center_in_reviewed_grid'}))
 result=record(identity,cells,'ai_source_reviewed_explicit_grid',flags=all_flags,page=page.number+1,page_size=[page.rect.width,page.rect.height],bbox=[xs[0],ys[0],xs[-1],ys[-1]],blank_cells_filled=False,header_normalized=bool(headers))
 result['review_status']='ai_source_reviewed'
 return result
