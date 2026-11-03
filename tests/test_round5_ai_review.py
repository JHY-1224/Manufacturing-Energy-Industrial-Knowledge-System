import tempfile,unittest
from pathlib import Path
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError
from app.core.engine import ConfigurableRagEngine
from app.core.models import IngestRequest
from app.storage.evidence_review_store import EvidenceReviewStore
from app.knowledge.quality_profile import quality_profile
class AiEvidenceReviewTests(unittest.TestCase):
 def setUp(self):
  self.root=Path(tempfile.mkdtemp());self.store=KnowledgeStore(self.root/'store');path=self.root/'fixture.txt';path.write_text('SYN-AI reference only, no production approval.')
  result=ConfigurableRagEngine(self.store).ingest(IngestRequest(path=str(path),knowledge_base_id='materials'));self.id=result['document_id'];self.doc=self.store.get_document(self.id);self.ledger=EvidenceReviewStore(self.store)
 def test_evidence_review_does_not_publish_or_modify_source(self):
  before=self.store.get_document(self.id);r=self.ledger.append(self.id,self.doc['revision_id'],'accepted','fixture_fact','Fixture source supported',{'chunk_id':'fixture'})
  self.assertFalse(r['human_approved']);self.assertFalse(r['document_published']);self.assertEqual(before,self.store.get_document(self.id))
  with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM knowledge_reviews').fetchone()[0],0)
 def test_review_requires_current_revision_and_explicit_evidence(self):
  with self.assertRaises(KnowledgeConflictError):self.ledger.append(self.id,'old','accepted','fact','fixture',{'source':'x'})
  with self.assertRaises(ValueError):self.ledger.append(self.id,self.doc['revision_id'],'accepted','fact','fixture',{})
 def test_normalization_preserves_unknown_conditions(self):
  profile=quality_profile(self.doc);self.assertIsNone(profile['version']);self.assertIsNone(profile['applicability']['process_conditions']);self.assertFalse(profile['inferred_production_values']);self.assertEqual(profile['industry'],'materials')
