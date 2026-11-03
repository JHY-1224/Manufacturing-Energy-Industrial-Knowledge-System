import base64,tempfile,unittest
from pathlib import Path
import fitz
from app.knowledge.image_preview import source_preview
from app.storage.knowledge_store import KnowledgeStore,KnowledgeConflictError

# Generated red/blue two-frame image, with no third-party source material.
GIF=base64.b64decode('R0lGODlhDAAIAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAADAAIAAAIEgABCBxIsKDBgwgTKlzIsGHBgAAh+QQBCgABACwAAAAADAAIAIEAAP8AAAAAAAAAAAAIEgABCBxIsKDBgwgTKlzIsGHBgAA7')
class ImageEvidencePreviewTests(unittest.TestCase):
    def setUp(self):
        self.store=KnowledgeStore(tempfile.mkdtemp());sha,path=self.store.save_asset(GIF,'fixture.gif');self.document=self.store.register('image',{'document_id':'image','source_sha256':sha,'image_sha256':sha,'local_path':path,'revision_id':'r'})
    def test_animation_preview_is_source_bound_static_first_frame(self):
        value=source_preview(self.store,'image','r');self.assertTrue(value.startswith(b'\x89PNG'));self.assertIn(self.document['source_sha256'].encode(),value);pix=fitz.Pixmap(value);self.assertEqual((pix.width,pix.height),(12,8));self.assertEqual(pix.samples[:3],bytes([255,0,0]));self.assertEqual(source_preview(self.store,'image','r'),value)
    def test_changed_source_and_foreign_revision_cannot_reuse_cached_preview(self):
        source_preview(self.store,'image','r')
        with self.assertRaises(KnowledgeConflictError):source_preview(self.store,'image','other')
        Path(self.document['local_path']).write_bytes(b'changed')
        with self.assertRaises(KnowledgeConflictError):source_preview(self.store,'image','r')
    def test_deleted_image_and_cache_symlink_are_not_served(self):
        folder=self.store.directory/'image-preview-cache';folder.mkdir();target=self.store.directory/'unrelated.txt';target.write_text('synthetic unrelated file');path=folder/(self.document['source_sha256']+'-first-frame-v1.png');path.symlink_to(target)
        with self.assertRaises(ValueError):source_preview(self.store,'image','r')
        self.store.remove_document('image')
        with self.assertRaises(FileNotFoundError):source_preview(self.store,'image','r')
        self.assertEqual(target.read_text(),'synthetic unrelated file')

if __name__=='__main__':unittest.main()
