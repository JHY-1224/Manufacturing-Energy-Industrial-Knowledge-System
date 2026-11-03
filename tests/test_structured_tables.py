import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from app.preprocessors.structured_tables import html_table, office_table, office_paragraph, pdf_cell, page_tables, scan_table, continuation_links, explicit_continuation_tables, to_html
from app.loaders.docx_loader import DocxLoader
from app.loaders.ocr_router import RoutedLoader

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M='http://schemas.openxmlformats.org/officeDocument/2006/math'


def office(rows):return f'<w:tbl xmlns:w="{W}" xmlns:m="{M}">'+rows+'</w:tbl>'
def tc(text,prop=''):return '<w:tc><w:tcPr>'+prop+'</w:tcPr><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:tc>'
def tr(*cells):return '<w:tr>'+''.join(cells)+'</w:tr>'


def grid_page(pdf, top=50, merge=False):
    page=pdf.new_page(width=400,height=400)
    for y in (top,top+60,top+120):page.draw_line((40,y),(360,y),width=1)
    for x in (40,360):page.draw_line((x,top),(x,top+120),width=1)
    page.draw_line((200,top+(60 if merge else 0)),(200,top+120),width=1)
    for x,y,t in [(55,top+25,'Code'),(215,top+25,'Unit'),(55,top+85,'SYN-A'),(215,top+85,'mm')]:page.insert_text((x,y),t,fontsize=12)
    return page


class StructureTests(unittest.TestCase):
    def setUp(self):self.root=Path(tempfile.mkdtemp(prefix='kb-structure-'))

    def test_html_spans_and_source_locator(self):
        t=html_table('<table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr><tr><td>C</td><td>D</td></tr></table>','x')
        self.assertEqual((t['row_count'],t['col_count']),(2,3));self.assertEqual(t['flags'],[])
        self.assertEqual([(c['row'],c['col']) for c in t['cells']],[(0,0),(0,1),(1,1),(1,2)])
        self.assertEqual(t['cells'][-1]['locator']['dom_cell'],2);self.assertFalse(t['human_approved'])

    def test_html_script_format_and_active_content(self):
        t=html_table('<table><tr><td onclick="bad()">10<sup>-3</sup> H<sub>2</sub>O<script>bad()</script></td></tr></table>','x')
        self.assertEqual(t['cells'][0]['text'],'10^(-3) H_(2)O');self.assertNotIn('bad',to_html(t));self.assertIn('<sub>2</sub>',to_html(t))

    def test_html_image_formula_not_fabricated(self):
        t=html_table('<table><tr><td><img src="x"></td><td>Equation 1</td></tr></table>','x')
        self.assertIn('image_or_formula_requires_source',t['flags']);self.assertIn('待核对',t['cells'][0]['text'])

    def test_html_invalid_spans_and_nested_tables(self):
        for html in ['<table><tr><td rowspan="-1">A</td></tr></table>','<table><tr><td><table><tr><td>A</td></tr></table></td></tr></table>']:
            with self.assertRaises(ValueError):html_table(html,'x')

    def test_html_overlap_and_incomplete_are_flagged(self):
        t=html_table('<table><tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">C</td></tr></table>','x')
        self.assertIn('overlapping_cells',t['flags'])
        t=html_table('<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>','x');self.assertIn('incomplete_grid',t['flags'])

    def test_office_horizontal_and_vertical_merges(self):
        t=office_table(office(tr(tc('A','<w:vMerge w:val="restart"/>'),tc('B','<w:gridSpan w:val="2"/>'))+tr(tc('','<w:vMerge/>'),tc('C'),tc('D'))),'docx')
        self.assertEqual(t['cells'][0]['rowspan'],2);self.assertEqual(t['cells'][0]['locator']['continuation_rows'],[2]);self.assertEqual(t['flags'],[])
        self.assertEqual(t['col_count'],3);self.assertEqual(len(t['cells']),4)

    def test_office_merge_cannot_cross_gap_or_change_width(self):
        t=office_table(office(tr(tc('A','<w:vMerge w:val="restart"/>'))+tr(tc('B'))+tr(tc('','<w:vMerge/>'))),'x')
        self.assertEqual(t['cells'][0]['rowspan'],1);self.assertIn('orphan_or_conflicting_vertical_merge',t['flags'])
        t=office_table(office(tr(tc('A','<w:vMerge w:val="restart"/>'))+tr(tc('','<w:vMerge/><w:gridSpan w:val="2"/>'))),'x')
        self.assertIn('orphan_or_conflicting_vertical_merge',t['flags'])

    def test_office_superscript_subscript_and_math_not_flattened(self):
        p=f'<w:p xmlns:w="{W}" xmlns:m="{M}"><w:r><w:t>H</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r><m:oMath><m:f><m:num><m:r><m:t>x</m:t></m:r></m:num><m:den><m:r><m:t>y</m:t></m:r></m:den></m:f></m:oMath></w:p>'
        text,html,formulas,flags=office_paragraph(p);self.assertIn('H_(2)',text);self.assertIn('<sub>2</sub>',html);self.assertEqual(len(formulas),1);self.assertIn('<m:f>',formulas[0]);self.assertNotIn('xy',text)

    def test_native_pdf_cells_have_actual_positions(self):
        with fitz.open() as pdf:
            page=grid_page(pdf);t=page_tables(page)[0]
            self.assertEqual((t['row_count'],t['col_count']),(2,2));self.assertEqual(len(t['cells']),4)
            self.assertEqual(t['flags'],[]);self.assertEqual(t['cells'][0]['locator']['page'],1)
            self.assertAlmostEqual(t['cells'][0]['bbox'][0],40);self.assertEqual(t['cells'][-1]['text'],'mm')

    def test_native_pdf_merge_is_one_cell_not_repeated(self):
        with fitz.open() as pdf:
            page=grid_page(pdf,merge=True);t=page_tables(page)[0]
            self.assertEqual(len(t['cells']),3);self.assertEqual(t['cells'][0]['colspan'],2)

    def test_pdf_superscript_is_not_concatenated_number(self):
        with fitz.open() as pdf:
            page=pdf.new_page();page.insert_text((50,80),'10',fontsize=14);page.insert_text((66,74),'-3',fontsize=8)
            text,html,runs,flags=pdf_cell(page,page.rect)
            self.assertIn('^(-3)',text);self.assertIn('<sup>-3</sup>',html);self.assertTrue(all('bbox' in r for r in runs))

    def scan(self,merge=False,regions=()):
        with fitz.open() as pdf:
            page=grid_page(pdf,merge=merge);path=self.root/('merged.png' if merge else 'scan.png');page.get_pixmap(matrix=fitz.Matrix(2,2)).save(path)
        return scan_table(path,list(regions)),path

    def test_scanned_grid_assigns_text_and_keeps_confidence(self):
        regions=[dict(text='Code',bbox=[.13,.15,.2,.04],confidence=.93),dict(text='mm',bbox=[.53,.30,.1,.04],confidence=.88)]
        t,_=self.scan(regions=regions);self.assertIsNotNone(t);self.assertEqual((t['row_count'],t['col_count']),(2,2))
        self.assertEqual(t['cells'][0]['text'],'Code');self.assertEqual(t['cells'][-1]['text'],'mm');self.assertEqual(t['cells'][-1]['confidence'],.88)
        self.assertEqual(t['coordinate_system'],'image_pixels_top_left')

    def test_scan_cross_cell_text_is_never_guessed_into_column(self):
        t,_=self.scan(regions=[dict(text='Code Unit',bbox=[.13,.15,.65,.04],confidence=.9)])
        self.assertEqual(t['unassigned_region_indices'],[0]);self.assertTrue(all(not c['text'] for c in t['cells']))

    def test_scan_recovers_rectangular_merge(self):
        t,_=self.scan(merge=True);self.assertEqual(len(t['cells']),3);self.assertEqual(t['cells'][0]['colspan'],2)

    def test_borderless_image_has_no_invented_table(self):
        with fitz.open() as pdf:
            p=pdf.new_page();p.insert_text((20,30),'A       B');path=self.root/'no-grid.png';p.get_pixmap().save(path)
        self.assertIsNone(scan_table(path,[]))

    def test_transparent_grid_is_composited_without_false_black_background(self):
        with fitz.open() as pdf:
            page=grid_page(pdf);path=self.root/'transparent.png';page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=True).save(path)
        table=scan_table(path,[]);self.assertIsNotNone(table);self.assertEqual((table['row_count'],table['col_count']),(2,2))

    def test_cross_page_link_requires_edges_and_header_alignment(self):
        with fitz.open() as pdf:
            a=page_tables(grid_page(pdf,top=260))[0];b=page_tables(grid_page(pdf,top=40))[0]
        b['context_before']='Table 7 (continued)';links=continuation_links([a,b]);self.assertEqual(len(links),1);self.assertEqual(links[0]['kind'],'explicit_continuation');self.assertFalse(links[0]['auto_joined'])
        b['context_before']='';self.assertEqual(continuation_links([a,b])[0]['kind'],'possible_continuation')
        b['cells'][0]['text']='Other';self.assertEqual(continuation_links([a,b]),[])

    def test_adjacent_repeated_small_tables_not_joined(self):
        with fitz.open() as pdf:
            a=page_tables(grid_page(pdf,top=40))[0];b=page_tables(grid_page(pdf,top=40))[0]
        self.assertEqual(continuation_links([a,b]),[])

    def test_explicit_cross_page_view_preserves_every_cell_origin(self):
        with fitz.open() as pdf:
            a=page_tables(grid_page(pdf,top=260))[0];b=page_tables(grid_page(pdf,top=40))[0]
        a['context_before']='Table 7: Fixture';b['context_before']='Table 7 (continued)'
        t=explicit_continuation_tables([a,b])[0]
        self.assertEqual(t['row_count'],3);self.assertEqual(len(t['cells']),6)
        self.assertEqual(t['source_pages'],[1,2]);self.assertEqual(t['cells'][-1]['locator']['page'],2)
        self.assertFalse(t['blank_cells_filled']);self.assertFalse(t['human_approved'])
        b['context_before']='Table 8 (continued)';self.assertEqual(explicit_continuation_tables([a,b]),[])

    def test_grey_scan_borders_do_not_collapse_columns(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400)
            for y in (50,90,130,170):page.draw_line((40,y),(360,y),color=(.6,.6,.6),width=.4)
            for x in (40,100,240,360):page.draw_line((x,50),(x,170),color=(.6,.6,.6),width=.4)
            page.insert_text((245,75),'SYN 10');path=self.root/'grey.png';page.get_pixmap(matrix=fitz.Matrix(3,3)).save(path)
        table=scan_table(path,[]);self.assertIsNotNone(table);self.assertEqual((table['row_count'],table['col_count']),(3,3))

    def test_docx_ingestion_preserves_geometry_and_protects_table(self):
        from docx import Document
        from app.core.engine import ConfigurableRagEngine
        from app.core.models import IngestRequest
        from app.storage.knowledge_store import KnowledgeStore
        d=Document();p=d.add_paragraph();p.add_run('10');p.add_run('3').font.superscript=True
        t=d.add_table(rows=2,cols=2);t.cell(0,0).merge(t.cell(0,1)).text='SYN HEADER';t.cell(1,0).text='SYN-A';t.cell(1,1).text='mm'
        file=self.root/'original.docx';d.save(file);original=file.read_bytes()
        loaded=DocxLoader().load(file)[0];self.assertIn('10^(3)',loaded.content);self.assertEqual(len(loaded.metadata['formula_evidence']),1)
        store=KnowledgeStore(self.root/'store');engine=ConfigurableRagEngine(store);engine.ingest(IngestRequest(path=str(file),knowledge_base_id='electronics',parser_mode='auto'))
        tables=[c for c in engine.chunks if c.metadata.get('table_layout')];self.assertEqual(len(tables),1)
        self.assertEqual(tables[0].metadata['table_layout']['cells'][0]['colspan'],2);self.assertTrue(tables[0].metadata['protected_structure']);self.assertEqual(file.read_bytes(),original)

    def test_routed_scan_uses_existing_ocr_no_model_training(self):
        t,path=self.scan();ocr=dict(text='SYN-A',regions=[dict(text='SYN-A',bbox=[.13,.30,.2,.04],confidence=.91)],engine='fixture',frame_count=1)
        with patch('app.loaders.ocr_router.NativeVisionOcr.recognize',return_value=ocr):
            loaded=RoutedLoader(path,'auto',self.root).load(path)[0]
        self.assertEqual(loaded.metadata['table_structure'],'ruled_grid_pending_human');self.assertIn('<table',loaded.content)

    def test_parser_revision_invalidates_reuse_only_on_explicit_ingest(self):
        from docx import Document
        from app.core.engine import ConfigurableRagEngine
        from app.core.models import IngestRequest
        from app.storage.knowledge_store import KnowledgeStore
        d=Document();d.add_paragraph('Synthetic revision fixture');path=self.root/'version.docx';d.save(path)
        store=KnowledgeStore(self.root/'store');engine=ConfigurableRagEngine(store);req=IngestRequest(path=str(path),knowledge_base_id='electronics')
        engine.ingest(req);before=store.documents()[0]['revision_id'];engine.ingest(req)
        self.assertEqual(store.documents()[0]['revision_id'],before)
        with patch('app.preprocessors.structured_tables.VERSION','synthetic_next_revision'):engine.ingest(req)
        self.assertNotEqual(store.documents()[0]['revision_id'],before)

    def test_auto_pdf_retains_source_geometry_and_source_file(self):
        with fitz.open() as pdf:
            page=grid_page(pdf);page.insert_text((40,30),'Synthetic table fixture with readable native text.');path=self.root/'source.pdf';pdf.save(path)
        before=path.read_bytes();items=RoutedLoader(path,'auto',self.root).load(path)
        self.assertIn('<table',items[0].content);geo=Path(items[0].metadata['text_geometry_path']);self.assertTrue(geo.is_file());self.assertEqual(path.read_bytes(),before)
        self.assertEqual(json.loads(geo.read_text())['formula_semantics'],'not_inferred')
