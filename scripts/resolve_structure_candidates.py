"""Apply explicit source-reviewed dispositions to a snapshot, preserving original bodies."""
import argparse,json,sys,copy,shutil
from pathlib import Path
from urllib.parse import unquote,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest
from app.knowledge.structure_resolution import StructureResolutions
from app.preprocessors.grid_recovery import bounded_grid
from app.preprocessors.structured_tables import to_html
from html import escape

def build(root,store_dir):
 import fitz
 root=Path(root);store=KnowledgeStore(store_dir);service=StructureResolutions(store)
 items=json.loads((root/'rejected79.json').read_text());out=root/'structure-resolutions';out.mkdir(exist_ok=False);(out/'records').mkdir();(out/'entries').mkdir()
 with store.connection() as db:docs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM documents WHERE deleted=0')]
 dispositions=[];tables={}
 def attempt(name):return json.loads((root/'recovery-attempts/attempts'/f'{name}.json').read_text())['attempts']
 for item in items:
  name=item['candidate_id'];old=json.loads(Path(item['source_record']).read_text());source=Path(unquote(urlparse(old['original_uri']).path));d=next(d for d in docs if d.get('source_sha256')==item['source_sha256']);page=old['layout'].get('page');sid=item['source_id']
  if digest(source.read_bytes())!=d['source_sha256']:raise ValueError('Frozen source bytes changed')
  v=dict(candidate_id=name,document_id=d['document_id'],revision_id=d['revision_id'],source_sha256=d['source_sha256'],source_table_id=old['table_id'],page=page,original_candidate=old,source_inspected=True,inspection_scope='all_candidate_crops_and_selected_full_pages',disposition='graphic_reference',graphic_kind='chart_or_engineering_diagram',note='已查看原图候选：按曲线/图形来源保留整页、轴线、图例和脚注；原提取网格不作行列数值依据。',original_uri=old['original_uri'])
  if sid=='basf_molding_defects':
   v.update(graphic_kind='color_coded_matrix' if page in [68,69] else 'photograph_panels',note='保留整页彩色矩阵、图例及空格，不将颜色或空白推断成缺失参数。' if page in [68,69] else '原对象为并排照片及标题，按图像证据处置；不转作工艺参数表。')
  if sid=='basf_ultramid' and page==15:v.update(graphic_kind='symbol_matrix',note='原对象为产品/应用符号矩阵；实心/空心圆及左侧标签必须同图查看，不补猜符号含义或数值。')
  if sid=='doe_air_protocol':v.update(graphic_kind='blank_collection_form' if page==28 else 'numeric_table_graphic_only',note='空白采集表按表单图形保留，未填写单元格不是零值。' if page==28 else '原页125 psig行的文字与边框出现错位；完整保留压力、孔径、流量、修正系数及歧义，暂不从错位行建立数值关联。')
  if sid=='doe_electrolysis' and page in [39]:v.update(graphic_kind='text_matrix',note='保留研发事项的原始双栏布局和项目符号；不把长段落拆成数值表。')
  if sid=='skf_install' and page==23:v.update(graphic_kind='numeric_table_graphic_only',note='轴承游隙表多级表头与范围列完整图形化保存；原自动合并不可靠，不将相邻范围补齐或推断。')
  if sid=='ti_surface':v.update(graphic_kind='flowchart',note='原对象为带方向箭头的流程图；保留整页节点及箭头关系，不将箭头通道作为空白数据列。')
  if sid=='ahss_flc':
   graphic=root/'flc-equation-source.jpg';target=store.directory/'structure-assets'/graphic.name;target.parent.mkdir(exist_ok=True);shutil.copyfile(graphic,target)
   receipt=json.loads((root/'flc-equation-receipt.json').read_text());v.update(graphic_kind='equation_image',graphic_asset={'relative_path':str(target.relative_to(store.directory)),'sha256':digest(target.read_bytes()),**receipt},formula_transcription='FLC_0 = (n / 0.21) * (23.3 + 14.13 * t)',formula_transcription_status='ai_visual_transcription_normalized_against_adjacent_source_not_evaluated',note='已补存原 HTML 中 Equation 1 指向的官方公式图片并逐字核对。保留下标与括号；原1×2网格是排版容器，禁止当作数据表。变量含义与单位仍须配合相邻正文。')
  if sid=='ahss_flc':
   from lxml import html
   import re
   tree=html.fromstring(source.read_bytes());v['source_reference_annotations']=sorted(set(token for a in tree.xpath('//a[contains(@href,"/citations/")]') for token in re.findall(r'[A-Z]-\d+',a.text_content())))
  if sid=='doe_electrolysis' and page==22:
   v.update(disposition='superseded',graphic_kind=None,superseded_by='reviewed_electrolysis_targets',note='该候选由既有逐格核对的 reviewed_electrolysis_targets 取代；保留旧网格、原页和目标表引用，禁止重复发布有冲突的原候选。')
  if name=='basf_ultramid__p0048_table01':
   t=attempt(name)[0];xs=sorted({round(x,3) for c in t['cells'] for x in [c['bbox'][0],c['bbox'][2]]});ys=sorted({round(x,3) for c in t['cells'] for x in [c['bbox'][1],c['bbox'][3]] if x>=449.7})
   with fitz.open(source) as pdf:table=bounded_grid(pdf[page-1],name,xs,ys,['Ultramid grade','T_M (°C)','T_w (°C)','1.0 mm spiral length (mm)','1.0 mm ratio (i)','1.5 mm spiral length (mm)','1.5 mm ratio (i)','2.0 mm spiral length (mm)','2.0 mm ratio (i)'])
   v.update(disposition='recovered_table',table=table,title='Table 8: Flow characteristic of Ultramid during injection molding',table_note='来源印刷页48，18个牌号。T_M为熔体温度，T_w为模具表面温度。1.0/1.5/2.0 mm指螺旋厚度；i为流长/壁厚比。表头按原多级表头展开，数值未重算。仅原资料规定条件下的流动试验，不作现场批准参数。',note='逐行查看原表；按可见列界和行底色恢复18×9数据区域，修复误合并并展开多级表头，保留原字符及位置。')
  if name in ['doe_air_protocol__p0030_table01','doe_air_protocol__p0033_table01','doe_electrolysis__p0054_table01']:
   table=copy.deepcopy(attempt(name)[0]);table['table_id']=name;table['review_status']='ai_source_reviewed';v.update(disposition='recovered_table',table=table,title={'doe_air_protocol__p0030_table01':'Table 6: Compressed Air CFM-bin Hour Profile','doe_air_protocol__p0033_table01':'Table 9: Example Project Compressed-Air CFM-Bin Hour Table','doe_electrolysis__p0054_table01':'Table A-4: Financial Assumptions'}[name],table_note='保留合并表头、原值、单位及空白；行列按本页可见边界核对。该表是报告中的示例/假设，不作为其他设备或工况的批准数据。',note='对照完整原页核对严格边界提取结果，消除上一版重叠合并；保留总计、空格与合并表头。')
  if name=='doe_air_protocol__p0030_table01':v['table_note']+=' CFM-bin属于原表的行编号，负荷单位按表头ACFM保留；不由分箱名推导标准状态或单位换算。'
  if name=='doe_electrolysis__p0052_table01':
   t=attempt(name)[0];xs=sorted({round(x,3) for c in t['cells'] for x in [c['bbox'][0],c['bbox'][2]]});ys=sorted({round(x,3) for c in t['cells'] for x in [c['bbox'][1],c['bbox'][3]]})
   with fitz.open(source) as pdf:table=bounded_grid(pdf[page-1],name,xs,ys)
   v.update(disposition='recovered_table',table=table,title='Table A-2: LA electrolyzer H2A-Lite key parameters',table_note='仅LA电解槽；Current Status、2026 Target和2031 Target分列。脚注26—31仍见原页，成本是2020美元，范围值、上标及连字符照原文，不补算或跨PEM/O-SOEC混用。',note='原第四列被误合并为贯穿全表单元格；以其余列一致行界和可见列界重建19×4网格，并逐行核对原页。')
  # Header/body fragments on page55 describe one table. Use explicit group, preserve both old IDs.
  if sid=='doe_electrolysis' and page==55:
   if page not in tables:
    parts=attempt(name);native=[x for x in parts if 'lines_strict' in x['table_id']];header=copy.deepcopy(native[0]);cells=header['cells'];offset=2
    with fitz.open(source) as pdf:
     pp=pdf[page-1]
     # Full group preserved graphically; no inferred propagation of category labels.
     page_text=pp.get_text()
    v.update(disposition='graphic_reference',graphic_kind='grouped_numeric_table',recovery_group='doe_electrolysis_page55_A5',note='两个候选是Table A-5的表头/正文片段，已按整页同表归组；保留Current/2026/2031分组、价格和容量因数。禁止跨分组拼接或丢失币值年份；当前以完整表格图形提供依据。')
  value=service.put(v);dispositions.append(value);(out/'records'/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
  body=to_html(value['table']) if value.get('table') else '<p>图形来源：请查看完整原页，禁止从退回网格读取数值。</p>'
  (out/'entries'/f'{name}.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>body{font:16px/1.6 system-ui;margin:24px}td{border:1px solid #aaa;padding:6px;white-space:pre-wrap}table{border-collapse:collapse}pre{white-space:pre-wrap}a{overflow-wrap:anywhere}</style><h1>'+escape(name)+'</h1><p>'+escape(value['note'])+'</p><p>'+escape(value.get('table_note',''))+'</p><p><a href="'+escape(value['original_uri'])+'">来源原件（完整页面）</a></p>'+body+'<details><summary>来源定位及保留的退回记录</summary><pre>'+escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details></html>')
 from collections import Counter
 summary={'total':len(dispositions),'dispositions':dict(Counter(x['disposition'] for x in dispositions)),'graphic_kinds':dict(Counter(x.get('graphic_kind') for x in dispositions if x['disposition']=='graphic_reference')),'recovered_cells':sum(len(x.get('table',{}).get('cells',[])) for x in dispositions),'all_original_chunks_preserved':True,'human_approved':False,'training_allowed':False}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--store',type=Path,required=True);a=p.parse_args();build(a.root,a.store)
