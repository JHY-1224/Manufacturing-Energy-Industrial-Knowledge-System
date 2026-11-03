"""Offline synthetic checks; requires the same Pillow runtime as the collector."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from image_store import ImageStore,describe,public_url

class CollectionTests(unittest.TestCase):
 def setUp(self):self.root=Path(tempfile.mkdtemp(prefix='industry-collection-test-'));self.store=ImageStore(self.root)
 def png(self,colour):
  out=BytesIO();Image.new('RGB',(200,120),colour).save(out,format='PNG');return out.getvalue()
 def item(self,name='caption',url='https://example.org/a.png'):return {'caption':name,'original_url':url,'source_page':'https://example.org/source','thumbnail_url':'','search_url':'https://www.google.com/search?q=synthetic&udm=2'}
 def result(self,raw):
  info=describe(raw);path=self.store.downloads/(info['sha256']+'.png');path.write_bytes(raw);return {**info,'download_file':str(path.relative_to(self.root)),'saved_kind':'source_image'}
 def test_pairing_resume_and_global_exact_duplicate(self):
  domain={'id':'automotive','manufacturing_type':'discrete'};a={'id':'auto_01','name':'工厂'};b={'id':'auto_02','name':'工廠'};result=self.result(self.png('red'))
  self.assertTrue(self.store.accept(domain,a,self.item('Exact source caption'),result))
  self.assertFalse(self.store.accept(domain,b,self.item('Different caption'),result))
  reopened=ImageStore(self.root);rows=reopened.records();self.assertEqual(len(rows),1)
  self.assertEqual((self.root/rows[0]['caption_file']).read_text(),'Exact source caption\n')
  self.assertEqual(json.loads((self.root/rows[0]['metadata_file']).read_text())['source_page'],'https://example.org/source')
  self.assertEqual(reopened.counts(),{'auto_01':1});self.assertFalse(rows[0]['allow_training'])
 def test_invalid_images_and_private_urls_rejected(self):
  for url in ['http://127.0.0.1/a','https://localhost/a','file:///tmp/x','https://user:secret@example.org/a','http://[::1]/a']:
   with self.assertRaises(ValueError):public_url(url)
  for raw in [b'not an image',self.png('blue')[:24]]:
   with self.assertRaises(Exception):describe(raw)
  self.assertEqual(public_url('https://example.org/a'),'https://example.org/a')
 def test_truncated_animation_is_not_verified_as_complete_image(self):
  stream=BytesIO();a=Image.new('RGB',(96,96),'red');b=Image.new('RGB',(96,96),'blue');a.save(stream,format='GIF',save_all=True,append_images=[b],duration=100,loop=0);raw=stream.getvalue()
  info=describe(raw);self.assertEqual(info['verified_frame_count'],2);self.assertTrue(info['all_frames_decoded'])
  with self.assertRaises(Exception):describe(raw[:-25])
 def test_export_cannot_claim_incomplete_quota_complete(self):
  domain={'id':'automotive','manufacturing_type':'discrete','categories':[{'id':'auto_01','name':'factory','target':50}]}
  self.store.accept(domain,domain['categories'][0],self.item(),self.result(self.png('green')))
  report=self.store.export({'domains':[domain]});self.assertEqual(report['status'],'in_progress');self.assertEqual(report['images'],1);self.assertEqual(report['completed_categories'],0)

if __name__=='__main__':unittest.main()
