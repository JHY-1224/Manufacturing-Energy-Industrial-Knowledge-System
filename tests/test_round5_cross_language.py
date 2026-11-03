import unittest
from app.pre_retrieval.cross_language import should_translate
from app.core.models import RagChunk
from app.post_retrieval.context_compressor import ContextCompressor
from app.runtime.models import CompatibleRagChain,RuntimeAdapterError
from unittest.mock import Mock
class CrossLanguageTests(unittest.TestCase):
 def test_translation_applies_only_to_chinese_non_caption_english_scope(self):
  source=[RagChunk(doc_id='d',chunk_id='c',content='Fixture',metadata={'source_language':'en'})]
  self.assertTrue(should_translate('轴承过盈配合的游隙变化？',source))
  self.assertFalse(should_translate('查找来源标题为某某的图片',source));self.assertFalse(should_translate('English question',source));self.assertFalse(should_translate('中文问题',[]))
 def test_english_evidence_after_long_intro_is_not_lost(self):
  intro='Unrelated boilerplate words '*90
  text=intro+'\n\nThe component must be at the same temperature as the measuring equipment.\n\nThis applies to measurement, not an assembly temperature setpoint.'
  result=ContextCompressor().compress('same temperature measuring equipment',text)
  self.assertIn('same temperature',result);self.assertIn('not an assembly temperature',result)
 def test_query_translation_has_separate_call_stage(self):
  chain=object.__new__(CompatibleRagChain);chain.endpoint=Mock(model='fixture');chain.model_name='fixture';chain.calls=[]
  chain._post=Mock(return_value={'choices':[{'finish_reason':'stop','message':{'content':'What is the fixture temperature?'}}]})
  chain._record=lambda response:chain.calls.append({'stage':'chat'})
  self.assertEqual(chain.translate_query('合成温度问题'),'What is the fixture temperature?');self.assertEqual(chain.calls[-1]['stage'],'query_translation')
  self.assertIn('Do not answer',chain._post.call_args.args[0]['messages'][0]['content'])
 def test_incomplete_translation_does_not_enter_retrieval(self):
  chain=object.__new__(CompatibleRagChain);chain.endpoint=Mock(model='fixture');chain.model_name='fixture';chain.calls=[];chain._post=Mock(return_value={'choices':[{'finish_reason':'length','message':{'content':'partial'}}]})
  with self.assertRaises(RuntimeAdapterError):chain.translate_query('合成问题')

 def test_named_chinese_source_avoids_unrelated_english_translation(self):
  scoped=[RagChunk(doc_id='a',chunk_id='a',content='fixture',metadata={'source_language':'en'}),RagChunk(doc_id='b',chunk_id='b',content='fixture',metadata={'source_language':'zh','source_title':'合成材料生产工艺验证报告模板'})]
  self.assertFalse(should_translate('合成材料生产工艺验证报告模板的批准人是什么？',scoped))
