import unittest,tempfile
from pathlib import Path
from lxml import html
from app.preprocessors.html_notation import readable_html
from app.loaders.html_loader import HtmlLoader
from app.knowledge.structure_resolution import evidence_html
class HtmlReferenceRoleTests(unittest.TestCase):
 def test_source_citation_is_not_a_grade_or_power(self):
  source='<p>Grade X1. Estimates<a href="https://example.org/citations/h-23/"><sup>H-23</sup></a>; CO<sub>2</sub> and 10<sup>3</sup>.</p>'
  expected=readable_html(html.fromstring(source));self.assertIn('〔参考文献编号：H-23〕',expected);self.assertIn('CO_(2)',expected);self.assertIn('10^(3)',expected);self.assertNotIn('^(H-23)',expected)
  path=Path(tempfile.mkdtemp())/'source.html';path.write_text(source);loaded=HtmlLoader().load(path)[0].content;self.assertIn('〔参考文献编号：H-23〕',loaded);self.assertIn('CO_(2)',loaded);self.assertIn('10^(3)',loaded)
 def test_inline_table_scripts_and_citation_roles_survive(self):
  source='<table><tr><td>10<sup>3</sup></td><td><a role="doc-biblioref" href="#ref-1"><sup>A-1</sup></a></td></tr></table>'
  path=Path(tempfile.mkdtemp())/'source.html';path.write_text(source);text=HtmlLoader().load(path)[0].content;self.assertIn('10<sup>3</sup>',text);self.assertIn('〔参考文献编号：A-1〕',text)
 def test_compact_model_table_keeps_merges_without_ui_ids(self):
  table={'row_count':1,'cells':[{'row':0,'col':0,'rowspan':1,'colspan':2,'text':'A','cell_id':'ui-coordinate-identifier'}]};content=evidence_html(table);self.assertIn('colspan="2"',content);self.assertNotIn('ui-coordinate',content);self.assertNotIn('rowspan="1"',content)

 def test_malformed_href_does_not_drop_source_text(self):
  value=readable_html(html.fromstring('<p>Text <a href="https://[broken">visible label</a></p>'));self.assertIn('visible label',value)
