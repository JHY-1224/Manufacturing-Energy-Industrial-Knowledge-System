import unittest
from app.core.engine import ConfigurableRagEngine
from app.core.models import RagChunk,QueryOptions,QueryRequest
from app.retrievers.table_retriever import TableRetriever


class TableRetrievalTests(unittest.TestCase):
    def test_named_cells_trademark_grades_and_technology_headers_survive_recall_quota(self):
        from app.core.models import RetrievalHit
        body='<table><tr><td>Name</td><td>Symbol</td><td>X-SYN Status</td></tr><tr><td>novaprefix</td><td>N</td><td>reference</td></tr><tr><td>Sample® B</td><td>B</td><td>reference</td></tr></table>'
        meta={'table_id':'synthetic','structure_review':'source_dictionary'}
        table=RagChunk(doc_id='syn',chunk_id='named',content=body,metadata=meta)
        distractor=RagChunk(doc_id='syn',chunk_id='distractor',content='<table>novaprefix mentions only; no factor/name row</table>',metadata={'table_id':'d'})
        query='词头 novaprefix 的符号，请引用实际 source ID 并说明限制'
        self.assertEqual(TableRetriever().search(query,[distractor,table],1)[0].chunk_id,'named')
        self.assertIn('Sample B',TableRetriever.structural_matches(body,'Sample B 的重复单元下标'))
        self.assertIn('X-SYN',TableRetriever.structural_matches(body,'X-SYN 系统能效脚注'))
        self.assertEqual(TableRetriever.structural_matches(body,'novaprefixes 的符号'),[])
        high=RetrievalHit(doc_id='syn',chunk_id='prose',content='wrong prose',score=1,rerank_score=.99)
        hit=TableRetriever().search(query,[table],1)[0];hit.rerank_score=.2
        self.assertEqual(TableRetriever.prefer_checked_tables([high,hit],query)[0].chunk_id,'named')
        self.assertEqual(hit.rerank_score,.2)

    def test_footnote_subscript_intent_and_scope_notes_are_retrievable(self):
        for query in ['某材料重复单元的下标','报告表2的系统能效脚注']:
            self.assertNotEqual(TableRetriever.expand(query)[1],[])
        hit=TableRetriever().search('点焊分组能否作为批准工艺？',[RagChunk(doc_id='syn',chunk_id='scoped',
            content='<table><tr><td>Group</td></tr><tr><td>1</td></tr></table>\n【来源范围与说明】synthetic limited reference',
            metadata={'table_id':'t','title':'点焊分组说明','structure_review':'source_dictionary'})],1)[0]
        result=TableRetriever.prefer_checked_tables([hit],'点焊分组能否作为批准工艺？')
        self.assertEqual(result[0].metadata['selection_reason'],'table_scope_note_with_matching_title')

    def test_bilingual_table_recall_retains_original_cells_and_tail_conditions(self):
        table='<table><tr><td>Grade</td><td>Melting point [°C]</td></tr><tr><td>SYN-PA66</td><td>261</td></tr></table>\n'+'Original condition. '*150+'NOT AN INJECTION SETPOINT'
        chunks=[RagChunk(doc_id='syn',chunk_id='table',content=table,metadata={'table_id':'t','protected_structure':True})]
        query,aliases=TableRetriever.expand('SYN-PA66 的熔点是多少，单位是什么？')
        self.assertIn('melting point',aliases)
        hit=TableRetriever().search(query,chunks)[0]
        self.assertEqual(hit.content,table)
        self.assertEqual(hit.chunk_id,'table');self.assertEqual(hit.retrieval_sources,['table'])

    def test_table_channel_never_crosses_explicit_base_or_enables_disabled_chunks(self):
        engine=ConfigurableRagEngine()
        engine.add_chunks([RagChunk(doc_id=str(i),chunk_id=str(i),content='<table>melting point SYN-PA 261 °C</table>',
            metadata={'table_id':'t','knowledge_base_id':base,'enabled':enabled}) for i,(base,enabled) in enumerate([('materials',True),('automotive',True),('materials',False)])])
        engine.trace_dir=__import__('pathlib').Path(__import__('tempfile').mkdtemp(prefix='table-query-'))
        trace=engine.query(QueryRequest(question='SYN-PA 熔点多少',generate_answer=False,options=QueryOptions(table_retrieval=True,metadata_router=False,query_transformation=False,metadata_filter={'knowledge_base_id':'materials'})))
        self.assertEqual({h.chunk_id for h in trace.reranked_docs},{'0'})

    def test_merge_is_bounded_deduplicated_and_preserves_channel_scores(self):
        from app.core.models import RetrievalHit
        base=[RetrievalHit(doc_id='d',chunk_id=str(i),content='source',score=.8,vector_score=.8,retrieval_sources=['vector']) for i in range(30)]
        tables=[RetrievalHit(doc_id='d',chunk_id=str(i),content='table',score=1,retrieval_sources=['table']) for i in (2,31,32,33,34,35)]
        merged=TableRetriever().merge(base,tables,30)
        self.assertEqual(len(merged),len({h.chunk_id for h in merged}));self.assertLessEqual(len(merged),30)
        duplicate=next(h for h in merged if h.chunk_id=='2')
        self.assertEqual(duplicate.vector_score,.8);self.assertEqual(duplicate.content,'source')
        self.assertIn('table',duplicate.retrieval_sources)
        self.assertTrue(all(h.score<.1 for h in merged))

    def test_non_table_intent_does_not_add_aliases_or_table_results(self):
        query='解释机器人日常维护背景'
        self.assertEqual(TableRetriever.expand(query),(query,[]))
        self.assertEqual(TableRetriever().search(query,[]),[])

    def test_checked_table_supersedes_only_ocr_of_the_same_source_figure(self):
        from app.core.models import RetrievalHit
        ocr=RetrievalHit(doc_id='one',chunk_id='ocr',content='machine OCR',score=.1,rerank_score=.99,
            metadata={'chunk_kind':'image_table_ocr','figure_id':'fig'})
        other=ocr.model_copy(deep=True,update={'doc_id':'two','chunk_id':'other'})
        table=RetrievalHit(doc_id='one',chunk_id='checked',content='exact cells + units + footnotes',score=.1,rerank_score=.4,
            retrieval_sources=['table'],metadata={'figure_id':'fig','table_id':'t','structure_review':'agent_visual_cell_transcription_checked_against_source_image'})
        ranked=TableRetriever.prefer_checked_tables([ocr,other,table])
        self.assertEqual([h.chunk_id for h in ranked],['checked','other'])
        self.assertEqual(ranked[0].rerank_score,.4)
        self.assertEqual(ranked[0].content,table.content)
        self.assertEqual(ranked[0].metadata['replaced_ocr_chunk_id'],'ocr')

    def test_exact_content_deduplication_preserves_different_source_documents(self):
        from app.post_retrieval.deduplicator import Deduplicator
        from app.core.models import RetrievalHit
        hits=[RetrievalHit(doc_id=doc,chunk_id=str(i),content='same source text',score=1) for i,doc in enumerate(['one','one','two'])]
        self.assertEqual([h.chunk_id for h in Deduplicator().deduplicate(hits)],['0','2'])

    def test_exact_header_row_lookup_prioritizes_checked_cells_without_changing_model_score(self):
        from app.core.models import RetrievalHit
        formula=RetrievalHit(doc_id='d',chunk_id='formula',content='Shear strength formula uses S',score=.5,rerank_score=.99)
        table=RetrievalHit(doc_id='d',chunk_id='table',content='<table><tr><td>Group</td><td>Minimum tensile strength (MPa)</td></tr><tr><td>4</td><td>&gt;800</td></tr></table>',score=.4,rerank_score=.3,retrieval_sources=['table'],metadata={'table_id':'t','table_recall_score':1,'structure_review':'agent_visual_cell_transcription_checked_against_source_image'})
        result=TableRetriever.prefer_checked_tables([formula,table],'Group 4 最低抗拉强度是多少？')
        self.assertEqual(result[0].chunk_id,'table');self.assertEqual(result[0].rerank_score,.3)
        self.assertEqual(result[0].content,table.content)
        self.assertEqual(TableRetriever.prefer_checked_tables([formula,table],'Group 40 的定义')[0].chunk_id,'formula')

    def test_polyamide_query_alias_preserves_input_grade_and_matches_its_column(self):
        from app.core.models import RetrievalHit
        query,aliases=TableRetriever.expand('Ultramid PA 66 和 PA 6 的熔点各是多少°C？')
        self.assertIn('polyamide 66',aliases);self.assertIn('polyamide 6',aliases)
        table=RetrievalHit(doc_id='d',chunk_id='t',content='<table><tr><td>Ultramid</td><td>Polyamide</td><td>Melting point</td></tr><tr><td>A</td><td>66</td><td>260 °C</td></tr></table>',score=.1,rerank_score=.1,retrieval_sources=['table'],metadata={'table_id':'t','structure_review':'agent_visual_cell_transcription_checked_against_source_page'})
        result=TableRetriever.prefer_checked_tables([table],query)
        self.assertEqual(result[0].metadata['matched_table_row_key'],'Polyamide: 66')
        self.assertEqual(TableRetriever.expand('Pa 66 压力单位是什么？')[1],[])


class OriginalDictionaryRowTests(unittest.TestCase):
    def test_original_dataset_dictionary_row_outranks_incomplete_prose(self):
        from app.core.models import RetrievalHit
        source=RetrievalHit(doc_id='fixture',chunk_id='dictionary',content='<table><tr><td>Sensor</td><td>Unit</td></tr><tr><td>Q17</td><td>kPa</td></tr></table>',metadata={'table_id':'fixture_table','reference_type':'dataset_archive','source_method':'original_fixture_dictionary'},score=.1,rerank_score=.1,retrieval_sources=['table'])
        prose=RetrievalHit(doc_id='fixture',chunk_id='prose',content='Q17 sampling rate is 20 Hz, units absent in this paragraph.',metadata={},score=.9,rerank_score=.9)
        hits=TableRetriever.prefer_checked_tables([prose,source],'Q17的单位和采样频率？')
        self.assertEqual(hits[0].chunk_id,'dictionary');self.assertEqual(hits[0].metadata['selection_reason'],'exact_table_header_row_key')
