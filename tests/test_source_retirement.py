from pathlib import Path
import tempfile,unittest
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError,digest

class SourceRetirementTests(unittest.TestCase):
 def setUp(self):self.store=KnowledgeStore(tempfile.mkdtemp());self.raw=b'explicitly withdrawn synthetic image';self.sha=digest(self.raw)
 def test_retired_bytes_cannot_be_saved_again(self):
  self.store.set_meta('retired_source_sha256:'+self.sha,'explicit owner decision')
  with self.assertRaises(KnowledgeConflictError):self.store.save_asset(self.raw,'bad.gif')
  self.assertEqual(list(self.store.asset_dir.iterdir()),[])
  self.assertEqual(self.store.save_asset(b'repaired bytes','fixed.gif')[0],digest(b'repaired bytes'))
 def test_retired_source_cannot_resurrect_a_removed_document(self):
  self.store.register('synthetic',{'document_id':'d','source_sha256':self.sha})
  self.store.remove_document('d');self.store.set_meta('retired_source_sha256:'+self.sha,'explicit owner decision')
  with self.assertRaises(KnowledgeConflictError):self.store.register('synthetic',{'document_id':'different','source_sha256':self.sha})
  self.assertIsNone(self.store.get_document('d'))

if __name__=='__main__':unittest.main()
