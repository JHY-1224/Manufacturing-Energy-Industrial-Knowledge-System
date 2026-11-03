import tempfile,unittest
from pathlib import Path
from scripts.serve_workbench_fixture import seed
from app.storage.knowledge_store import KnowledgeStore

class ExplicitFixtureTests(unittest.TestCase):
    def test_synthetic_fixture_is_repeatable_and_refuses_existing_corpora(self):
        directory=Path(tempfile.mkdtemp());value=seed(directory);self.assertTrue(value['no_model_inference']);self.assertEqual(value,seed(directory));store=KnowledgeStore(directory);self.assertEqual(len(store.documents()),3)
        other=Path(tempfile.mkdtemp());existing=KnowledgeStore(other);existing.register('owner-data',{'document_id':'preserve','title':'existing fixture corpus'})
        with self.assertRaises(ValueError):seed(other)
        self.assertIsNotNone(existing.get_document('preserve'))

if __name__=='__main__':unittest.main()
