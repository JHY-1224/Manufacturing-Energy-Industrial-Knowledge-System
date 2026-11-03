from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.models import RagChunk, RetrievalHit, QueryOptions, QueryRequest
from app.core.engine import ConfigurableRagEngine
from app.indexes.bm25_index import BM25Index
from app.retrievers.evidence_retriever import EvidenceRetriever, lexical_tokens, exact_caption, header_matches
from app.storage.knowledge_store import KnowledgeStore


def chunk(identity,text,**metadata):
    return RagChunk(doc_id='doc_'+identity,chunk_id=identity,content=text,metadata={'knowledge_base_id':'electronics',**metadata})

def hit(c,score=.2):
    return RetrievalHit(doc_id=c.doc_id,chunk_id=c.chunk_id,content=c.content,metadata=dict(c.metadata),score=score,rerank_score=score)

def picture(identity,title,**extra):
    return chunk(identity,'【来源网页介绍，未经核验】\n'+title+'\n来源搜索分类：测试',chunk_kind='source_caption',evidence_kind='source_caption',image_sha256=identity,**extra)


class EvidenceRetrievalTests(unittest.TestCase):
    def test_tail_conditions_survive_length_and_repeated_title_noise(self):
        target=chunk('tail','【资料目录标题】合成安装资料\n'+('Unrelated synthetic paragraph. '*180)+' outside diameter 731 mm; clean before mounting.')
        distractors=[chunk('d'+str(i),'【资料目录标题】合成安装资料\n安装资料安装资料说明。') for i in range(20)]
        hits=EvidenceRetriever().search('安装前清洗说明中731 mm外径条件',distractors+[target],4)
        self.assertEqual(hits[0].chunk_id,'tail');self.assertEqual(hits[0].content,target.content)
        self.assertGreater(hits[0].metadata['evidence_window_offset'],1000)
        self.assertEqual(hits[0].metadata['evidence_numeric_matches'],['731 mm'])

    def test_number_unit_does_not_match_other_values_or_units(self):
        records=[chunk('good','diameter 73 mm'),chunk('wrong_number','diameter 730 mm'),chunk('wrong_unit','pressure 73 MPa')]
        hits=EvidenceRetriever().search('外径 73 mm',records,3)
        self.assertEqual(hits[0].chunk_id,'good')
        self.assertTrue(all(not h.metadata['evidence_numeric_matches'] for h in hits if h.chunk_id!='good'))

    def test_body_terms_are_not_repeated_metadata_and_single_characters(self):
        records=[chunk('good','健康档案与培训记录'),chunk('noisy','健壮康复记录',title='健康档案与培训记录'*100)]
        self.assertEqual(EvidenceRetriever().search('健康档案培训记录',records,2)[0].chunk_id,'good')
        self.assertIn('健康',lexical_tokens('健康档案'));self.assertNotIn('健',lexical_tokens('健康档案'))

    def test_signed_condition_is_not_silently_changed(self):
        records=[chunk('positive','temperature 73 °C'),chunk('negative','temperature -73 °C')]
        matches={h.chunk_id:h.metadata['evidence_numeric_matches'] for h in EvidenceRetriever().search('温度为-73 °C',records,2)}
        self.assertEqual(matches['negative'],['-73 °C']);self.assertEqual(matches['positive'],[])
        matches={h.chunk_id:h.metadata['evidence_numeric_matches'] for h in EvidenceRetriever().search('温度为73 °C',records,2)}
        self.assertEqual(matches['positive'],['73 °C']);self.assertEqual(matches['negative'],[])

    def test_explicit_table_number_retrieves_scope_prose_without_matching_other_tables(self):
        records=[chunk('scope','Table 12 assumptions: baseline input power 19 kW.'),chunk('other','Table 120 baseline power example.'),chunk('split','Table 12.3 different example.')]
        hits=EvidenceRetriever().search('协议的Table 12的功率假设',records,3)
        self.assertEqual(hits[0].chunk_id,'scope');self.assertEqual(hits[0].metadata['evidence_table_references'],['12'])
        self.assertTrue(all(not h.metadata['evidence_table_references'] for h in hits[1:]));self.assertEqual(hits[0].content,records[0].content)

    def test_expansion_has_no_new_numeric_facts(self):
        q,terms=EvidenceRetriever.expand('试点能源绩效改善在几年内验证？')
        self.assertIn('pilot sites',q);self.assertIn('energy performance',q)
        self.assertFalse(any(c.isdigit() for c in q));self.assertEqual(EvidenceRetriever.expand('SYN-ALPHA'),('SYN-ALPHA',[]))

    def test_bm25_defaults_remain_compatible_and_custom_body_fields_are_optional(self):
        c=chunk('x','unrelated',title='special')
        default=BM25Index();default.build([c]);self.assertEqual(default.search('special',1)[0].chunk_id,'x')
        body=BM25Index(tokenizer=lexical_tokens,searchable_fields=());body.build([c]);self.assertEqual(body.search('special',1),[])

    def test_exact_caption_survives_adverse_model_ranking_without_score_change(self):
        wanted=picture('wanted','合成设备清洗步骤示意图');other=picture('other','合成设备安装说明示意图')
        a,b=hit(other,.99),hit(wanted,.01)
        result=EvidenceRetriever.prefer_literal_evidence([a,b],'查找图片：合成设备清洗步骤示意图',[other,wanted])
        self.assertEqual(result[0].chunk_id,'wanted');self.assertEqual(result[0].rerank_score,.01)
        self.assertEqual(result[0].metadata['literal_source_chunk_ids'],['wanted']);self.assertEqual(result[0].content,wanted.content)

    def test_partial_caption_and_non_lookup_do_not_force_selection(self):
        c=picture('a','合成设备清洗步骤示意图')
        self.assertFalse(exact_caption('查找合成设备图片',c));self.assertFalse(exact_caption('解释合成设备清洗步骤示意图中的未知根因',c))

    def test_caption_metadata_cannot_fabricate_literal_match(self):
        c=picture('a','真实来源设备图标题',source_caption='并不存在的错误图片标题')
        self.assertFalse(exact_caption('查找并不存在的错误图片标题',c));self.assertTrue(exact_caption('查找真实来源设备图标题',c))

    def test_omitted_caption_is_not_treated_as_supplied_evidence(self):
        c=picture('a','合成设备清洗步骤示意图');h=hit(c);h.content='Only unrelated OCR';h.metadata['evidence_chunk_ids']=[]
        result=EvidenceRetriever.prefer_literal_evidence([h],'查找合成设备清洗步骤示意图',[c])
        self.assertNotIn('selection_reason',result[0].metadata)

    def test_foreign_source_ids_cannot_be_promoted(self):
        c=picture('private','合成设备清洗步骤示意图');h=hit(c)
        self.assertNotIn('selection_reason',EvidenceRetriever.prefer_literal_evidence([h],'查找合成设备清洗步骤示意图',[])[0].metadata)

    def test_multiple_merged_headers_match_without_guessing_row_relationship(self):
        c=chunk('table','<table><tr><td colspan="2">是否有健康档案</td><td>是否填写培训记录</td></tr><tr><td>未知</td><td>—</td><td>—</td></tr></table>',table_id='t')
        matches=header_matches('检查表有哪些健康档案和培训记录栏目',c);self.assertEqual(matches,['是否有健康档案','是否填写培训记录'])
        h=hit(c,.01);other=hit(chunk('prose','资料说明'),.99)
        self.assertEqual(EvidenceRetriever.prefer_literal_evidence([other,h],'检查表健康档案和培训记录',[c])[0].chunk_id,'table')
        self.assertEqual(EvidenceRetriever.prefer_literal_evidence([other,h],'说明健康档案',[c])[0].chunk_id,other.chunk_id)

    def test_merge_is_bounded_and_preserves_original_model_scores(self):
        original=hit(chunk('one','SYN code'));original.vector_score=.83;original.bm25_score=.7;original.retrieval_sources=['vector','bm25']
        extra=EvidenceRetriever().search('SYN',[chunk('one','SYN code'),chunk('two','SYN extra')],2)
        merged=EvidenceRetriever.merge([original],extra,2);self.assertEqual(len(merged),2)
        one=next(h for h in merged if h.chunk_id=='one');self.assertEqual(one.vector_score,.83);self.assertEqual(one.bm25_score,.7);self.assertIn('source_evidence',one.retrieval_sources)
        self.assertIn('evidence_fusion_score',one.metadata)
        self.assertLessEqual(len(EvidenceRetriever.merge([original],extra,1)),1)

    def test_engine_filters_before_new_recall_and_trace_records_policy(self):
        store=KnowledgeStore(Path(tempfile.mkdtemp(prefix='kb-evidence-test-')));engine=ConfigurableRagEngine(store)
        public=chunk('public','Scope fixture electronic production');private=picture('private','合成设备清洗步骤示意图',exclusive_to_system='fixture')
        private.metadata['knowledge_base_id']='cosmetics_manufacturing';engine.add_chunks([public,private])
        before=[c.model_dump_json() for c in engine.chunks]
        opt=QueryOptions(retrieval_mode='bm25',evidence_retrieval=True,metadata_filter={'knowledge_base_id':'electronics'},metadata_router=False,query_transformation=False,query_expansion=False,query_rewrite=False,rerank=False)
        trace=engine.query(QueryRequest(question='查找合成设备清洗步骤示意图',options=opt,generate_answer=False))
        self.assertTrue(all(h.doc_id!=private.doc_id for h in trace.retrieved_docs));self.assertTrue(trace.runtime['evidence_retrieval']['enabled'])
        self.assertEqual(before,[c.model_dump_json() for c in engine.chunks])
        opt.review_mode='published_only';trace=engine.query(QueryRequest(question='electronic',options=opt))
        self.assertEqual(trace.retrieved_docs,[]);self.assertTrue(trace.answer)

    def test_scope_and_switch_disabled_do_not_call_new_search(self):
        store=KnowledgeStore(Path(tempfile.mkdtemp(prefix='kb-evidence-off-')));engine=ConfigurableRagEngine(store);engine.add_chunks([chunk('a','SYN fixture')])
        with patch.object(EvidenceRetriever,'search',side_effect=AssertionError('disabled')):
            engine.query(QueryRequest(question='SYN',options=QueryOptions(retrieval_mode='bm25',evidence_retrieval=False),generate_answer=False))

    def test_summary_mode_accepts_original_evidence_hits(self):
        store=KnowledgeStore(Path(tempfile.mkdtemp(prefix='kb-evidence-summary-')));engine=ConfigurableRagEngine(store)
        engine.add_chunks([picture('literal','合成设备清洗步骤示意图')])
        trace=engine.query(QueryRequest(question='查找合成设备清洗步骤示意图',options=QueryOptions(retrieval_mode='summary',evidence_retrieval=True),generate_answer=False))
        self.assertTrue(trace.retrieved_docs);self.assertTrue(all(h.chunk_id=='literal' for h in trace.retrieved_docs))
        self.assertTrue(trace.runtime['evidence_revision_checked'])
