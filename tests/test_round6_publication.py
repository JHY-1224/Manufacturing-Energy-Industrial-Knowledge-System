import sqlite3,unittest
from scripts.publish_round6_derived import insert_checked

class DerivedPublicationTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:')
  self.db.execute('CREATE TABLE multimodal_assets (document_id TEXT,revision_id TEXT,fingerprint TEXT,kind TEXT,source_sha256 TEXT,payload TEXT,PRIMARY KEY(document_id,revision_id,fingerprint,kind))')
  self.original=('d','r','fp','observation','source','original')
  with self.db:insert_checked(self.db,'multimodal_assets',self.original)
 def tearDown(self):self.db.close()
 def test_exact_existing_evidence_is_idempotent(self):
  self.assertEqual(insert_checked(self.db,'multimodal_assets',self.original),0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM multimodal_assets').fetchone()[0],1)
 def test_conflict_preserves_original_and_rolls_back_other_new_rows(self):
  with self.assertRaises(ValueError):
   with self.db:
    insert_checked(self.db,'multimodal_assets',('new','r','fp','observation','source','new evidence'))
    insert_checked(self.db,'multimodal_assets',(*self.original[:-1],'conflicting value'))
  self.assertEqual(self.db.execute('SELECT * FROM multimodal_assets').fetchall(),[self.original])

if __name__=='__main__':unittest.main()
