import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collection.relevance import reject_reason

class ProcessScopeTests(unittest.TestCase):
 category={'relevance_required':'process_manufacturing'}
 def test_material_production_flow_passes(self):
  self.assertIsNone(reject_reason({'id':'materials'},self.category,{'caption':'PVC树脂聚合生产工艺流程图'}))
  self.assertIsNone(reject_reason({'id':'materials'},self.category,{'caption':'人造石墨负极材料烧结生产工艺流程'}))
  self.assertIsNone(reject_reason({'id':'materials'},self.category,{'caption':'Welding electrode manufacturing production line'}))
 def test_energy_production_passes(self):
  self.assertIsNone(reject_reason({'id':'energy'},self.category,{'caption':'水电解制氢工艺流程图及纯化系统'}))
  self.assertIsNone(reject_reason({'id':'energy'},self.category,{'caption':'火力发电厂锅炉汽轮机热力系统流程图'}))
  self.assertIsNone(reject_reason({'id':'energy'},self.category,{'caption':'Hydrogen production and PSA purification process'}))
 def test_macro_exhibition_and_equipment_assembly_are_rejected(self):
  for text in ['材料工业景气指数走势图','新能源产业招聘生产流程简介','光伏组件生产工艺流程图','锂电池包模组装配工艺流程','Lithium-ion battery pack manufacturing process']:
   self.assertIsNotNone(reject_reason({'id':'energy'},self.category,{'caption':text}),text)
 def test_ordinary_reference_collection_keeps_its_previous_scope(self):
  self.assertIsNone(reject_reason({'id':'automotive'},{},{'caption':'汽车工厂照片'}))
