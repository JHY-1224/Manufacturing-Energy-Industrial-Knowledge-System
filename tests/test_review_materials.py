import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from hashlib import sha256
from unittest.mock import patch

from scripts.build_quality_review import build


class ReviewMaterialTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='kb-review-materials-'));self.store=self.root/'store';self.store.mkdir()
        self.source=self.root/'source.txt';self.source.write_text('SYN-A 23 mm')
        self.chunk={'doc_id':'doc-fixture','chunk_id':'chunk-fixture','content':'SYN-A 23 mm <script>unsafe()</script>',
                    'metadata':{'knowledge_base_id':'electronics','revision_id':'rev-fixture','source_project_path':str(self.source),'source_sha256':sha256(self.source.read_bytes()).hexdigest()}}
        self.db=sqlite3.connect(self.store/'knowledge.sqlite3');self.db.execute('CREATE TABLE chunks(chunk_id TEXT, payload TEXT)');self.db.execute('INSERT INTO chunks VALUES (?,?)',('chunk-fixture',json.dumps(self.chunk)));self.db.commit()
        case={'query_id':'review-fixture','question':'Synthetic SYN-A unit?','knowledge_base_id':'electronics','category':'fixture',
              'expected_doc_ids':['doc-fixture'],'expected_chunk_ids':['chunk-fixture'],'required_evidence':['23','mm'],'review_status':'synthetic_contract','training_allowed':False}
        self.cases=self.root/'cases.jsonl';self.cases.write_text(json.dumps(case)+'\n')

    def tearDown(self):self.db.close()

    def test_review_material_defaults_pending_and_needs_four_checks(self):
        result=build(self.cases,self.store,self.root/'review.html');text=(self.root/'review.html').read_text()
        self.assertFalse(result['automatic_approval']);self.assertIn('<option value="pending">',text);self.assertEqual(text.count('type="checkbox"'),4)
        self.assertIn('dataset_sha256',text);self.assertIn('localStorage',text);self.assertNotIn('<script>unsafe()',text)
        self.assertEqual(self.db.execute('SELECT count(*) FROM chunks').fetchone()[0],1)

    def test_existing_review_sheet_not_overwritten(self):
        out=self.root/'review.html';out.write_text('Existing review')
        with self.assertRaises(FileExistsError):build(self.cases,self.store,out)
        self.assertEqual(out.read_text(),'Existing review')

    def test_preview_source_digest_change_is_rejected(self):
        self.source.write_text('Changed source')
        with patch('scripts.build_quality_review.ROOT',self.root),self.assertRaisesRegex(ValueError,'fingerprint mismatch'):
            build(self.cases,self.store,self.root/'review.html',source_previews=True)
        self.assertFalse((self.root/'review.html').exists())

    def test_private_source_uses_retained_in_project_original(self):
        self.chunk['metadata']['source_project_path']='/outside-project/unavailable-original.txt'
        self.db.execute('UPDATE chunks SET payload=?',(json.dumps(self.chunk),))
        self.db.execute('CREATE TABLE documents(document_id TEXT,payload TEXT)')
        self.db.execute('INSERT INTO documents VALUES (?,?)',('doc-fixture',json.dumps({'local_path':str(self.source)})));self.db.commit()
        with patch('scripts.build_quality_review.ROOT',self.root):build(self.cases,self.store,self.root/'review.html',source_previews=True)
        receipt=json.loads((self.root/'review-assets/sources.json').read_text())
        self.assertEqual(receipt[0]['sources'][0]['source_sha256'],sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(receipt[0]['review_status'],'source_checked_pending_human')
