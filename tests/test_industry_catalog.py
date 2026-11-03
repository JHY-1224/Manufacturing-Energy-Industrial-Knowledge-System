import unittest
from app.knowledge.catalog import KNOWLEDGE_BASES,base_policy,validate_base

class IndustryCatalogTests(unittest.TestCase):
 def test_frontend_offline_catalog_matches_backend_without_fabricated_counts(self):
  import json
  from pathlib import Path
  catalog=json.loads((Path(__file__).resolve().parents[1]/'frontend/src/knowledge-catalog.json').read_text())
  self.assertEqual(catalog,KNOWLEDGE_BASES)
  self.assertTrue(all('document_count' not in b for b in catalog))
 def test_six_primary_domains_split_four_and_two(self):
  primary=[b for b in KNOWLEDGE_BASES if b.get('primary_industry')]
  self.assertEqual({b['id'] for b in primary},{'automotive','high_end_equipment','electronics','consumer_goods','materials','energy'})
  self.assertEqual({b['id'] for b in primary if b['industry_group']=='discrete'},{'automotive','high_end_equipment','electronics','consumer_goods'})
  self.assertEqual({b['id'] for b in primary if b['industry_group']=='process'},{'materials','energy'})
  for old in ['manufacturing','traditional','equipment_electronics','electrical','cosmetics_manufacturing']:self.assertEqual(validate_base(old),old)

 def test_cosmetics_parent_does_not_remove_its_explicit_local_policy(self):
  cosmetics=next(b for b in KNOWLEDGE_BASES if b['id']=='cosmetics_manufacturing')
  self.assertEqual(cosmetics['parent_id'],'consumer_goods')
  self.assertTrue(cosmetics['local_only'])
  self.assertNotIn('local_only',next(b for b in KNOWLEDGE_BASES if b['id']=='consumer_goods'))
  self.assertEqual(base_policy('cosmetics_manufacturing')['allow_cloud_upload'],False)
