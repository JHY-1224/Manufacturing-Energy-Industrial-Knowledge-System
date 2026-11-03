import json,unittest
from app.knowledge.evidence_workbench import EvidenceWorkbench
from app.knowledge.quality_dashboard import QualityDashboard
from app.knowledge.version_diff import RevisionDiff
from app.storage.knowledge_store import KnowledgeConflictError
from app.knowledge.workbench import WorkbenchService
from app.knowledge.service import KnowledgeService
from app.core.models import RagChunk
from tests import test_round5_multimodal as fixture

class EvidenceWorkbenchTests(unittest.TestCase):
    def setUp(self):
        fixture.MultimodalTests.setUp(self);d=self.store.get_document('a');self.mm.process('a',d['write_version'],'observation')
        with self.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS image_analysis_cache (cache_key TEXT PRIMARY KEY,source_sha256 TEXT,fingerprint TEXT,payload TEXT)')
            db.execute('INSERT INTO image_analysis_cache VALUES (?,?,?,?)',('ocr',d['source_sha256'],'ocr-fixture',self.store.encode({'width':100,'height':100,'lines':[{'text':'fixture visible text','confidence':.8,'bbox':[.1,.2,.3,.4]},{'text':'invalid','bbox':[.9,.9,.5,.5]}]})))
            d['image_analysis_key']='ocr';db.execute("UPDATE documents SET payload=? WHERE document_id='a'",(self.store.encode(d),))
        self.evidence=EvidenceWorkbench(self.store)
    def form(self):
        v=self.evidence.view('a');return dict(expected_revision=v['revision_id'],expected_write_version=v['write_version'],expected_source_sha256=v['source_sha256'],expected_model_fingerprint=v['observations'][0]['fingerprint'],expected_label_review_id=v['latest_label_review']['review_id'] if v['latest_label_review'] else None,source_visible_description='orange mechanical arm',reviewer='fixture editor',note='Checked the source image',decision='corrected',source_inspected=True)
    def test_geometry_and_pagination_do_not_invent_regions(self):
        v=self.evidence.view('a',1,1);self.assertEqual(v['regions'][0]['bbox'],[.1,.2,.3,.4]);self.assertEqual(v['regions'][0]['coordinate_system'],'normalized_top_left_xywh');self.assertEqual(v['invalid_regions_omitted'],1);self.assertEqual(self.evidence.view('a',2,1)['regions'],[])
    def test_saved_source_correction_is_used_by_retrieval_without_editing_original(self):
        before=self.store.get_document('a');saved=self.evidence.review('a',**self.form());self.assertEqual(saved['review']['review_kind'],'user_source_review');self.assertFalse(saved['human_gold_claimed']);self.assertEqual(self.store.get_document('a'),before)
        self.mm.process('a',before['write_version'],'embedding');r=self.mm.search('automotive',text='orange mechanical arm');m=next(m for m in r['items'][0]['matches'] if m['channel']=='source_reviewed_visual_text');self.assertEqual(m['evidence_review_id'],saved['review']['review_id'])
    def test_stale_editor_and_unviewed_source_are_rejected(self):
        old=self.form();self.evidence.review('a',**old)
        with self.assertRaises(KnowledgeConflictError):self.evidence.review('a',**old)
        form=self.form();form['source_inspected']=False
        with self.assertRaises(ValueError):self.evidence.review('a',**form)
        form=self.form();form['expected_source_sha256']='wrong'
        with self.assertRaises(KnowledgeConflictError):self.evidence.review('a',**form)
    def test_causal_labels_and_foreign_ocr_cannot_be_published(self):
        form=self.form();form['source_visible_description']='10 MPa导致缺陷'
        with self.assertRaises(ValueError):self.evidence.review('a',**form)
        with self.store.connection() as db:db.execute("UPDATE image_analysis_cache SET source_sha256='another source'")
        with self.assertRaises(KnowledgeConflictError):self.evidence.view('a')
    def test_withdrawal_updates_quality_counts_and_preserves_history(self):
        self.evidence.review('a',**self.form());q=QualityDashboard(self.store);self.assertEqual(q.summary('automotive')['source_reviewed_image_labels'],1)
        form=self.form();form['decision']='rejected';self.evidence.review('a',**form);self.assertEqual(q.summary('automotive')['source_reviewed_image_labels'],0);self.assertEqual(len(self.evidence.view('a')['source_reviews']),2)

class RevisionDiffTests(unittest.TestCase):
    def setUp(self):
        fixture.MultimodalTests.setUp(self);self.diff=RevisionDiff(self.store);self.before=self.store.get_document('a')['revision_id'];d=self.store.get_document('a')
        with self.store.connection() as db:chunks=[json.loads(r[0]) for r in db.execute("SELECT payload FROM chunks WHERE document_id='a'")]
        chunks[0]['content']='changed source text';d['revision_id']='new-rev'
        for c in chunks:c['metadata']['revision_id']='new-rev'
        self.store.replace_document(d,chunks,{'operation':'fixture edit'})
    def test_current_previous_counts_and_source_text(self):
        v=self.diff.compare('a');self.assertEqual(v['from_revision'],self.before);self.assertEqual(v['to_revision'],'new-rev');self.assertEqual(v['counts']['modified'],1);self.assertIn('changed source text',v['items'][0]['diff']);self.assertTrue(v['read_only'])
    def test_foreign_revisions_removed_documents_and_invalid_pages_are_rejected(self):
        foreign=self.store.get_document('b')['revision_id']
        with self.assertRaises(FileNotFoundError):self.diff.compare('a',foreign)
        with self.assertRaises(ValueError):self.diff.compare('a',page_size=1000)
        self.store.remove_document('a')
        with self.assertRaises(FileNotFoundError):self.diff.compare('a',self.before)
    def test_selecting_same_revision_is_not_reported_as_only_one_existing_revision(self):
        value=self.diff.compare('a',self.before,self.before);self.assertTrue(value['same_revision']);self.assertFalse(value['single_revision']);self.assertEqual(value['total'],0)
    def test_diagnostics_are_read_only_and_optional_services_not_faked(self):
        generation=self.store.get_meta('generation');d=QualityDashboard(self.store).diagnostics();self.assertEqual(d['database']['quick_check'],'ok');self.assertEqual(d['services'],[]);self.assertFalse(d['services_probed']);self.assertEqual(self.store.get_meta('generation'),generation)

if __name__=='__main__':unittest.main()
