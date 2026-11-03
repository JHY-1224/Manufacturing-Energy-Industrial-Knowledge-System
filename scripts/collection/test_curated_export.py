"""Synthetic checks for exporting reviewed supplements without changing the primary collection."""
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_curated_supplement import export
from image_store import ImageStore
import test_image_store as fixtures

class CuratedExportTests(unittest.TestCase):
 def setUp(self):
  fixture=fixtures.CollectionTests();fixture.setUp();self.root=fixture.root;self.store=fixture.store;self.png=fixture.png;self.result=fixture.result;self.item=fixture.item

 def test_reviewed_export_preserves_caption_and_skips_primary_duplicate(self):
  domain={'id':'automotive','manufacturing_type':'discrete'};category={'id':'auto_01','name':'factory'}
  red=self.result(self.png('red'));self.store.accept(domain,category,self.item('primary'),red)
  green=self.result(self.png('green'));target=self.root/'精选补充';target.mkdir()
  candidates=[]
  for identity,result in [('duplicate',red),('new',green)]:
   candidates.append({**self.item('Unmodified original title'),**result,'candidate_id':identity,'industry_id':'automotive','category_id':'auto_01','category':'factory'})
  (target/'候选清单.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in candidates))
  review=self.root/'review.json';review.write_text(json.dumps({'decisions':[{'candidate_id':identity,'decision':'select','reason':'Visible structured diagram for software testing'} for identity in ['duplicate','new']]}))
  result=export(self.root,review);self.assertEqual(result['selected_images'],1);self.assertEqual(len(result['omitted']),1);self.assertEqual(len(self.store.records()),1)
  selected=json.loads((target/'总清单.jsonl').read_text());self.assertEqual(selected['category_id'],'curated_automotive');self.assertEqual(selected['label_status'],'source_caption_unverified');self.assertFalse(selected['allow_training'])
  self.assertEqual((self.root/selected['caption_file']).read_text(),'Unmodified original title\n')
  self.assertEqual(hashlib.sha256((self.root/selected['file']).read_bytes()).hexdigest(),green['sha256'])
  self.assertEqual(export(self.root,review)['selected_images'],1)

if __name__=='__main__':unittest.main()
