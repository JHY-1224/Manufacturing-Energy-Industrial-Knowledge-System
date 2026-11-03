import tempfile,unittest
from pathlib import Path
import fitz
from app.preprocessors.scan_recovery import borderless_candidate,deskew_candidate
from app.preprocessors.structured_tables import html_table,office_paragraph,pdf_cell,explicit_continuation_tables
from app.preprocessors.math_notation import linear_math
from tests.test_structured_tables import grid_page,W,M
class ParserRecoveryTests(unittest.TestCase):
 def setUp(self):self.root=Path(tempfile.mkdtemp())
 def test_borderless_is_candidate_with_exact_ocr_positions(self):
  with fitz.open() as pdf:
   p=pdf.new_page(width=400,height=400);path=self.root/'plain.png';p.get_pixmap().save(path)
  regions=[dict(text=f'R{r}C{c}',bbox=[.1+c*.4,.1+r*.15,.2,.04],confidence=.9) for r in range(3) for c in range(2)]
  t=borderless_candidate(path,regions);self.assertEqual((t['row_count'],t['col_count']),(3,2));self.assertFalse(t['indexable']);self.assertEqual(t['cells'][-1]['text'],'R2C1')
  regions[-1]['bbox'][0]=.8;self.assertIsNone(borderless_candidate(path,regions))
 def test_skewed_grid_original_preserved(self):
  with fitz.open() as pdf:
   p=grid_page(pdf);path=self.root/'rotated.png';p.get_pixmap(matrix=fitz.Matrix(2,2).prerotate(3)).save(path)
  before=path.read_bytes();t=deskew_candidate(path,[],self.root/'derived');self.assertIsNotNone(t);self.assertAlmostEqual(t['deskew_degrees'],-3,delta=.3);self.assertEqual((t['row_count'],t['col_count']),(2,2));self.assertIn('source_quad',t['cells'][0]['locator']);self.assertEqual(before,path.read_bytes())
 def test_mathml_fraction_scripts_and_unknown_retained(self):
  t=html_table('<table><tr><td><math><mfrac><mi>x</mi><msup><mi>y</mi><mn>2</mn></msup></mfrac></math></td></tr></table>','x');self.assertEqual(t['cells'][0]['text'],'(x)/((y)^(2))');self.assertIn('<mfrac>',t['cells'][0]['formula_sources'][0])
  t=html_table('<table><tr><td><math><unknown>x</unknown></math></td></tr></table>','x');self.assertIn('未转写',t['cells'][0]['text'])
 def test_office_fraction_preserves_structure(self):
  p=f'<w:p xmlns:w="{W}" xmlns:m="{M}"><m:oMath><m:f><m:num><m:r><m:t>x</m:t></m:r></m:num><m:den><m:r><m:t>y</m:t></m:r></m:den></m:f></m:oMath></w:p>'
  t,h,s,f=office_paragraph(p);self.assertEqual(t,'(x)/(y)');self.assertTrue(s)

 def test_glyph_crossing_row_boundary_is_not_duplicated(self):
  with fitz.open() as pdf:
   p=pdf.new_page(width=200,height=200);p.insert_text((30,60),'ABCDE',fontsize=12)
   a=pdf_cell(p,(0,40,200,52))[0];b=pdf_cell(p,(0,52,200,75))[0]
   self.assertEqual((a+b).replace('\n',''),'ABCDE')

 def test_explicit_two_row_continuation_keeps_body_origin(self):
  def table(page,top):
   t=html_table('<table><tr><td rowspan="2">Name</td><td>Value</td></tr><tr><td>MPa</td></tr><tr><td>SYN</td><td>10</td></tr></table>',f'p{page}')
   t.update(page=page,page_size=[400,400],bbox=[40,top,360,top+120],header_row_count=2,context_before='Table 9'+(' continued' if page==2 else ''))
   for c in t['cells']:c['bbox']=[40+c['col']*160,top+c['row']*40,200+c['col']*160,top+(c['row']+c['rowspan'])*40];c['locator']['page']=page
   return t
  a,b=table(1,260),table(2,40);joined=explicit_continuation_tables([a,b]);self.assertEqual(len(joined),1);self.assertEqual(joined[0]['row_count'],4);self.assertEqual(joined[0]['cells'][-1]['locator']['page'],2)
  del b['header_row_count'];self.assertEqual(explicit_continuation_tables([a,b]),[])
