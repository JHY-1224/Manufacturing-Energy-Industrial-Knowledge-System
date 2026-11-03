import json
from pathlib import Path
import tempfile
import unittest

from app.core.engine import ConfigurableRagEngine
from app.core.models import IngestRequest, QueryOptions, QueryRequest
from app.knowledge.technical_import import TechnicalImporter, preflight
from app.post_retrieval.context_compressor import ContextCompressor
from app.storage.knowledge_store import KnowledgeStore, digest
from scripts.collection.technical_extract import fragments, html_text, table_html
from scripts.collection.technical_prepare import office_table_structure, mark_unmapped_controls
from scripts.collection.text_download import download, public_url


class TechnicalCollectionTests(unittest.TestCase):
    def test_units_unicode_and_html_cells_are_not_normalized_or_executed(self):
        value = table_html([['μ', 'MPa', '°C', 'kWh/kg H₂'], ['<script>x</script>', '≥60', None, '10^−30']])
        self.assertIn('μ', value); self.assertIn('kWh/kg H₂', value)
        self.assertIn('&lt;script&gt;', value); self.assertNotIn('<script>', value)
        self.assertIn('<td></td>', value)

    def test_inline_elements_do_not_invent_spaces_in_numbers_or_chinese(self):
        from lxml import html
        self.assertEqual(html_text(html.fromstring('<p>温度<span>30</span>°C；压力 <b>0.5</b> MPa</p>')), '温度30°C；压力 0.5 MPa')

    def test_fragments_keep_entire_symbol_sequence_and_tail(self):
        text = ('μ≥30°C；' * 300) + 'END'
        self.assertEqual(''.join(fragments(text, 180)), text)

    def test_unmapped_formula_glyphs_are_marked_without_guessing(self):
        text, codes = mark_unmapped_controls('v=\x00x\ny\x01\tμ °C')
        self.assertEqual(codes, [0, 1]); self.assertNotIn('\x00', text)
        self.assertIn('【未映射字形U+0000】x\ny【未映射字形U+0001】', text)
        self.assertTrue(text.endswith('\tμ °C'))

    def test_office_merges_preserve_spans_without_repeated_blank_fill(self):
        xml = '''<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>温度      °C</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>30</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>40</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'''
        rows, markup = office_table_structure(xml)
        self.assertEqual(rows, [['温度 °C', None, '30'], [None, None, '40']])
        self.assertIn('colspan="2" rowspan="2"', markup)
        self.assertEqual(markup.count('温度'), 1)

    def test_protected_tables_retain_distant_conditions_and_footnotes(self):
        content = '<table>' + '<tr><td>value</td></tr>' * 200 + '</table>\nCondition: 30°C, 60% RH; not an operating setpoint.'
        for mode in ('auto', 'table', 'keyword'):
            self.assertEqual(ContextCompressor().compress('value', content, mode=mode, preserve_structure=True), content)
        self.assertLess(len(ContextCompressor().compress('value', content)), len(content))

    def test_download_does_not_reuse_corrupted_cache_or_private_address(self):
        root = Path(tempfile.mkdtemp(prefix='technical-cache-test-')); directory = root / 'sources' / 'one'; directory.mkdir(parents=True)
        raw = directory / 'source.txt'; raw.write_text('changed')
        (directory / 'receipt.json').write_text(json.dumps({'file': str(raw.relative_to(root)), 'sha256': digest(b'old'), 'bytes': 3, 'url': 'https://example.com/a'}))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            download(root, {'id': 'one', 'url': 'https://example.com/a'})
        for url in ('file:///tmp/a', 'http://127.0.0.1/a', 'https://user:secret@example.com/a', 'http://localhost/a'):
            with self.assertRaises(ValueError): public_url(url)


class TechnicalImportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='technical-import-test-'))
        source = self.root / 'original.txt'; source.write_text('Synthetic source for import verification only.')
        self.table = table_html([['Synthetic code', 'Unit', 'Value'], ['VERIFY-UNIT-ROW', '°C', '30']]) + '\nApplicable only to synthetic fixture.'
        prepared = self.root / 'prepared.json'
        prepared.write_text(json.dumps({'source_sha256': digest(source.read_bytes()), 'knowledge_base_id': 'electronics',
            'chunks': [{'content': self.table, 'metadata': {'page': 2, 'table_id': 'verified_table', 'protected_structure': True, 'chunk_kind': 'technical_table'}}]}))
        self.record = {'id': 'synthetic_technical', 'knowledge_base_id': 'electronics', 'file': 'original.txt',
            'sha256': digest(source.read_bytes()), 'prepared_file': 'prepared.json', 'prepared_sha256': digest(prepared.read_bytes()),
            'title_zh': '合成单位表', 'title': 'Synthetic table', 'industry_name': '电子制造', 'reference_type': 'test_fixture',
            'resolved_url': 'https://example.com/synthetic', 'publisher': 'Synthetic', 'version': '1', 'downloaded_at': '2026-11-02',
            'topics': ['合成测试'], 'license_status': 'synthetic_fixture', 'machine_table_candidates': 0, 'checked_tables': 1, 'language': 'en'}
        self.store = KnowledgeStore(self.root / 'store'); self.store.set_meta('initialized', '1')
        self.importer = TechnicalImporter(self.store, self.root / 'runs')

    def test_import_idempotency_provenance_and_full_table_query(self):
        plan = preflight(self.root, [self.record]); first = self.importer.import_item(plan[0])
        before = self.store.snapshot()
        self.assertEqual(self.importer.import_item(plan[0])['status'], 'reused')
        self.assertEqual(before, self.store.snapshot())
        doc = self.store.get_document(first['document_id'])
        self.assertEqual(doc['parse_summary']['routes'], ['source_pages_tables_figures'])
        self.assertFalse(doc['allow_cloud_upload']); self.assertNotIn('exclusive_to_system', doc)
        options = QueryOptions(retrieval_mode='bm25', metadata_filter={'knowledge_base_id': 'electronics'}, metadata_router=False,
            query_transformation=False, query_expansion=False, query_rewrite=False, rerank=False, compression=True, parent_recovery=False)
        result = ConfigurableRagEngine(self.store).query(QueryRequest(question='VERIFY-UNIT-ROW', options=options, generate_answer=False))
        self.assertEqual(len(result.reranked_docs), 1)
        self.assertIn(self.table, result.compressed_context[0]); self.assertEqual(result.reranked_docs[0].metadata['page'], 2)
        self.assertEqual(result.reranked_docs[0].metadata['parser'], 'reviewed_technical_collection_v1')
        with self.assertRaisesRegex(ValueError, 'dedicated importer'):
            ConfigurableRagEngine(self.store).ingest(IngestRequest(path=doc['local_path'], document_id=doc['document_id']))

    def test_invalid_preparation_rejected_before_any_database_changes(self):
        bad = dict(self.record, prepared_sha256='wrong')
        with self.assertRaisesRegex(ValueError, 'changed'): preflight(self.root, [bad])
        with self.assertRaisesRegex(ValueError, 'Duplicate'): preflight(self.root, [self.record, self.record])
        with self.assertRaisesRegex(ValueError, 'escapes'): preflight(self.root, [dict(self.record, file='../outside.txt')])
        self.assertEqual(self.store.documents(), [])

    def test_source_change_after_preflight_is_not_published(self):
        plan = preflight(self.root, [self.record]); (self.root / 'original.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed after'): self.importer.import_item(plan[0])
        self.assertEqual(self.store.documents(), [])


if __name__ == '__main__': unittest.main()
