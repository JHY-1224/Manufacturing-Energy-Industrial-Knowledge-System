"""Revision-bound recovery decisions. Original chunks and rejected layouts stay intact."""
import json,re
from pathlib import Path
from html import escape
from app.storage.knowledge_store import digest,now,KnowledgeConflictError

def evidence_html(table):
 """Keep source text and merges; cell IDs/geometry live in trace metadata."""
 rows=[]
 for row in range(table['row_count']):
  cells=[]
  for c in sorted((c for c in table['cells'] if c['row']==row),key=lambda c:c['col']):
   attrs=''.join(f' {k}="{c[k]}"' for k in ('rowspan','colspan') if c[k]>1)
   cells.append('<td'+attrs+'>'+c.get('html',escape(c['text']).replace('\n','<br>'))+'</td>')
  rows.append('<tr>'+''.join(cells)+'</tr>')
 return '<table>'+''.join(rows)+'</table>'

class StructureResolutions:
 def __init__(self,store):
  self.store=store
  with store.connection() as db:db.execute('CREATE TABLE IF NOT EXISTS structure_resolutions (candidate_id TEXT NOT NULL,document_id TEXT NOT NULL,revision_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(candidate_id,revision_id))')
 def put(self,value):
  required={'candidate_id','document_id','revision_id','source_sha256','disposition','note','source_inspected','original_candidate'}
  if not required<=value.keys() or not value['source_inspected'] or not value['note']:raise ValueError('Explicit source disposition required')
  if value['disposition'] not in {'recovered_table','graphic_reference','superseded'}:raise ValueError('Unknown structure disposition')
  if value['disposition']=='recovered_table' and not value.get('table'):raise ValueError('Recovered cells required')
  value={**value,'review_kind':'ai_source_review','human_approved':False,'training_allowed':False,'created_at':now()}
  with self.store.connection() as db:
   db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM documents WHERE document_id=? AND deleted=0',(value['document_id'],)).fetchone();d=json.loads(row[0]) if row else {}
   if (d.get('revision_id'),d.get('source_sha256'))!=(value['revision_id'],value['source_sha256']):raise KnowledgeConflictError('Resolution source changed')
   old=db.execute('SELECT payload FROM structure_resolutions WHERE candidate_id=? AND revision_id=?',(value['candidate_id'],value['revision_id'])).fetchone()
   if old:raise KnowledgeConflictError('Resolution already recorded; preserve the previous decision')
   db.execute('INSERT INTO structure_resolutions VALUES (?,?,?,?,?)',(value['candidate_id'],value['document_id'],value['revision_id'],value['source_sha256'],self.store.encode(value)));self.store.bump(db)
  self.record_review(value)
  return value
 def record_review(self,value):
  from app.storage.evidence_review_store import EvidenceReviewStore
  reviews=EvidenceReviewStore(self.store)
  existing=reviews.current(value['document_id'])['items']
  prior=next((r for r in existing if r['scope']=='source_structure_resolution' and r['evidence'].get('candidate_id')==value['candidate_id'] and r['evidence'].get('source_sha256')==value['source_sha256']),None)
  if prior:return prior
  return reviews.append(value['document_id'],value['revision_id'],'corrected','source_structure_resolution',value['note']+' 本决定批准来源处置方式；不批准原退回网格或现场工艺。',{'candidate_id':value['candidate_id'],'source_sha256':value['source_sha256'],'disposition':value['disposition'],'source_table_id':value.get('source_table_id'),'page':value.get('page'),'recovered_cells':len(value.get('table',{}).get('cells',[])),'graphic_kind':value.get('graphic_kind'),'technical_fact_approved':False})
 def current(self,document_id=None):
  with self.store.connection() as db:
   rows=db.execute("SELECT json_remove(s.payload,'$.original_candidate') FROM structure_resolutions s JOIN documents d ON d.document_id=s.document_id AND d.deleted=0 AND s.revision_id=json_extract(d.payload,'$.revision_id') AND s.source_sha256=json_extract(d.payload,'$.source_sha256')"+(' WHERE s.document_id=?' if document_id else '')+' ORDER BY s.candidate_id',(document_id,) if document_id else ()).fetchall()
  return [json.loads(r[0]) for r in rows]
 def preview(self,document_id,candidate_id):
  value=next((v for v in self.current(document_id) if v['candidate_id']==candidate_id),None)
  if not value:raise FileNotFoundError('Current structure not found')
  d=self.store.get_document(document_id);path=Path(d['local_path']).resolve()
  if path.parent!=self.store.asset_dir.resolve() or digest(path.read_bytes())!=value['source_sha256']:raise KnowledgeConflictError('Original checksum changed')
  import fitz
  if value.get('page'):
   with fitz.open(path) as pdf:return pdf[value['page']-1].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
  # HTML equation images are copied as inert, hashed derived evidence, never fetched remotely.
  graphic=value.get('graphic_asset',{});p=(self.store.directory/graphic.get('relative_path','')).resolve()
  if not p.is_relative_to((self.store.directory/'structure-assets').resolve()) or not p.is_file() or digest(p.read_bytes())!=graphic.get('sha256'):raise FileNotFoundError('Bound graphic unavailable')
  return fitz.Pixmap(str(p)).tobytes('png')
 @staticmethod
 def matches(value,chunk):
  m=chunk.metadata
  return chunk.doc_id==value['document_id'] and m.get('revision_id')==value['revision_id'] and (m.get('table_id')==value.get('source_table_id') or (value.get('page') is not None and m.get('page')==value['page']))
 def table_chunks(self,chunks):
  """Only the table recall channel gets derived cells; no synthetic vector records."""
  result=[];seen=set()
  for value in self.current():
   if value['disposition']!='recovered_table':continue
   anchors=[c for c in chunks if self.matches(value,c)]
   if not anchors:continue
   source=next((c for c in anchors if c.metadata.get('chunk_kind')=='source_page_with_table'),anchors[0]);key=value.get('recovery_group',value['candidate_id'])
   if key in seen:continue
   seen.add(key);note=value.get('table_note','');content='【来源恢复表格】'+value.get('title','')+'\n'+evidence_html(value['table'])+'\n【来源范围与说明】'+note
   result.append(source.model_copy(update={'content':content,'metadata':{**source.metadata,'table_id':key,'chunk_kind':'technical_table','protected_structure':True,'structure_review':'agent_visual_cell_transcription_geometry_recovered','structure_resolution_id':value['candidate_id'],'derived_structure':True,'source_chunk_id':source.chunk_id,'source_table_layout':value['table']}}))
  return result
 def guard_hits(self,hits):
  values=self.current()
  for hit in hits:
   bibliographies=[v for v in values if v['document_id']==hit.doc_id and v['revision_id']==hit.metadata.get('revision_id') and v.get('source_reference_annotations')]
   for value in bibliographies:
    for token in value['source_reference_annotations']:
     hit.content=re.sub(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])','〔参考文献编号：'+token+'〕',hit.content)
   if bibliographies:hit.metadata['source_reference_roles_restored']=True
   if hit.metadata.get('derived_structure'):continue
   matching=[v for v in values if self.matches(v,hit)]
   if not matching:continue
   graphic=[v for v in matching if v['disposition']=='graphic_reference']
   if graphic:
    hit.metadata.update(structure_dispositions=[{'candidate_id':v['candidate_id'],'graphic_kind':v.get('graphic_kind'),'note':v['note']} for v in graphic],graphic_structure_only=True)
    # An HTML layout table containing an equation is not a numerical data table.
    if hit.metadata.get('table_id') in {v.get('source_table_id') for v in graphic}:
     hit.metadata['table_recall_disabled']=True
    hit.content='【来源结构说明：本页包含图形/布局候选；图形标签不构成可逐格读取的数值表，不得据此补值或推断行列关联。】\n'+hit.content
    equations=[v for v in graphic if v.get('formula_transcription')]
    if equations:
     hit.content+='\n【公式原图AI转写，未求值】\n'+'\n'.join(v['formula_transcription'] for v in equations)+'\n变量、单位和适用范围须按相邻原文核对，不由图片补猜。'
     hit.metadata.update(derived_formula=True,structure_resolution_id=equations[0]['candidate_id'])
  return hits
